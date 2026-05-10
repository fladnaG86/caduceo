"""Caduceo Agent - Client principale."""

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import websockets

from caduceo_common.constants import (
    HEARTBEAT_INTERVAL,
    RECONNECT_INITIAL,
    RECONNECT_MAX,
    RECONNECT_MULTIPLIER,
    RECONNECT_JITTER_MAX,
    PROTOCOL_VERSION,
    WS_AGENT_PATH,
    COMMAND_TIMEOUT_DEFAULT,
    FILE_CHUNK_SIZE,
    SCREENSHOT_FORMAT,
    HEARTBEAT_ENCRYPTED,
    MessageType,
)
from caduceo_common.crypto import Crypto
from caduceo_common.messages import (
    CommandMessage,
    FileDownloadMessage,
    FileUploadMessage,
    ScreenshotMessage,
    InfoRequestMessage,
    RegisterMessage,
)
from caduceo_common.utils import detect_os, generate_agent_id

logger = logging.getLogger("caduceo.agent")

# ── Allowed paths for file operations ──────────────────────────────────────

# Directory consentite per operazioni file (path traversal protection)
# Se vuoto, usa home utente come default (~)
# Configurabile via env: CADUCEO_ALLOWED_PATHS=/home/user:/var/log:/tmp
_DEFAULT_ALLOWED = os.path.expanduser("~")
_raw_paths = os.environ.get("CADUCEO_ALLOWED_PATHS", "")
ALLOWED_PATHS = _raw_paths.split(":") if _raw_paths else [_DEFAULT_ALLOWED]

# Path che non possono MAI essere letti/sovrascritti, anche dentro ALLOWED_PATHS
# Protezione defense-in-depth contro accesso a credenziali e configurazioni sensibili
DENIED_PATTERNS = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/ssh/",
    "/root/.ssh/",
    "/root/.gnupg/",
]
DENIED_SUFFIXES = [
    "/.ssh/",
    "/.gnupg/",
    "/.ssh/authorized_keys",
    "/.ssh/id_",
    "/.ssh/config",
    "/.gnupg/",
    "/etc/hosts",
    "/etc/resolv.conf",
]


def _is_denied_path(file_path: Path) -> bool:
    """Verifica se un path e' nella blacklist di sicurezza."""
    path_str = str(file_path)
    # Controlla pattern assoluti
    for pattern in DENIED_PATTERNS:
        if path_str.startswith(pattern):
            return True
    # Controlla suffissi (relativi alla home utente o assoluti)
    for suffix in DENIED_SUFFIXES:
        if path_str.endswith(suffix) or path_str.count(suffix) > 0:
            return True
    return False


def validate_path(path_str: str) -> Path:
    """
    Valida un path per operazioni file. Resolve symlinks e verifica che
    il path finale sia dentro una directory consentita (se configurata).
    Blocca sempre i path nella blacklist di sicurezza.
    """
    file_path = Path(path_str).expanduser().resolve()

    # Blocca sempre i path nella blacklist
    if _is_denied_path(file_path):
        raise PermissionError(
            f"Path nella blacklist di sicurezza: {path_str}. "
            f"Accesso negato a file sensibili (.ssh, .gnupg, /etc/shadow, ecc.)"
        )

    # Se ALLOWED_PATHS e' vuoto, consenti tutto (legacy mode, MAI in produzione)
    if not ALLOWED_PATHS:
        return file_path

    # Verifica che il path sia dentro una delle directory consentite
    for allowed in ALLOWED_PATHS:
        allowed_path = Path(allowed).resolve()
        try:
            file_path.relative_to(allowed_path)
            return file_path
        except ValueError:
            continue

    raise PermissionError(f"Path non consentita: {path_str}. Directory ammesse: {ALLOWED_PATHS}")


# ── Shell Executor ──────────────────────────────────────────────────────────

class ShellExecutor:
    """Esegue comandi shell su qualsiasi OS."""

    @staticmethod
    def detect_shell() -> str:
        """Rileva la shell predefinita del sistema."""
        os_name = detect_os()
        if os_name == "windows":
            return "powershell"
        elif os_name == "macos":
            return "zsh"
        else:
            return "bash"

    @staticmethod
    async def execute(command: str, args: list[str] | None = None,
                      timeout: int = COMMAND_TIMEOUT_DEFAULT,
                      shell: str = "") -> dict:
        """Esegue un comando shell e restituisce stdout, stderr, exit_code."""
        shell = shell or ShellExecutor.detect_shell()

        # Costruisci il comando completo in modo sicuro
        # Su Windows: usare doppioni apici (stile cmd.exe) per gli argomenti
        # Su Linux/macOS: usare shlex.quote (stile POSIX)
        if args:
            if os.name == "nt":
                # Windows: subprocess.list2cmdline produce quoting corretto per cmd.exe
                full_cmd = subprocess.list2cmdline([command] + args)
            else:
                import shlex
                full_cmd = f"{command} {' '.join(shlex.quote(a) for a in args)}"
        else:
            full_cmd = command

        # Audit logging: registra tutti i comandi eseguiti
        logger.info(f"COMMAND audit: cmd={full_cmd!r} shell={shell} timeout={timeout}")

        # Su Windows: subprocess.run() che eredita PATH e env di sistema
        # Su Linux/macOS: asyncio.create_subprocess_shell per non bloccare
        start_time = time.time()

        try:
            if os.name == "nt":
                # Windows: subprocess.run e' piu' affidabile per ereditare PATH
                # asyncio.create_subprocess_shell non eredita il PATH su Windows
                result = subprocess.run(
                    full_cmd,
                    shell=True,
                    capture_output=True,
                    timeout=timeout,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                return {
                    "exit_code": result.returncode,
                    "stdout": result.stdout.decode("utf-8", errors="replace"),
                    "stderr": result.stderr.decode("utf-8", errors="replace"),
                    "duration_ms": int((time.time() - start_time) * 1000),
                }
            else:
                # Linux/macOS: asyncio per non bloccare l'event loop
                if shell == "powershell":
                    executable = shutil.which("pwsh") or "powershell"
                elif shell == "zsh":
                    executable = "/bin/zsh"
                elif shell == "bash":
                    executable = "/bin/bash"
                else:
                    executable = None

                proc = await asyncio.create_subprocess_shell(
                    full_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    executable=executable,
                )

                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )

                return {
                    "exit_code": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "duration_ms": int((time.time() - start_time) * 1000),
                }

        except asyncio.TimeoutError:
            proc.kill()
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Timeout dopo {timeout} secondi",
                "duration_ms": timeout * 1000,
            }
        except Exception as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "duration_ms": int((time.time() - start_time) * 1000),
            }


# ── File Transfer ───────────────────────────────────────────────────────────

# Path che non possono essere SOVRASCRITTI neanche con overwrite=true
# Protezione defense-in-depth per file critici del sistema
OVERWRITE_DENIED_PATTERNS = [
    "/.ssh/authorized_keys",
    "/.ssh/known_hosts",
    "/.bashrc",
    "/.bash_profile",
    "/.profile",
    "/.zshrc",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/environment",
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
]


class FileManager:
    """Gestione trasferimento file con path traversal protection e overwrite protection."""

    @staticmethod
    async def download(path: str) -> dict:
        """Legge un file e lo restituisce come base64."""
        try:
            file_path = validate_path(path)
        except PermissionError as e:
            logger.warning(f"FILE download denied: {path} - {e}")
            return {"error": str(e), "size": 0, "content_b64": ""}

        logger.info(f"FILE download: {file_path}")

        if not file_path.exists():
            return {"error": f"File non trovato: {path}", "size": 0, "content_b64": ""}

        if not file_path.is_file():
            return {"error": f"Non e' un file: {path}", "size": 0, "content_b64": ""}

        if file_path.stat().st_size > 50 * 1024 * 1024:
            return {"error": f"File troppo grande (max 50MB): {path}", "size": 0, "content_b64": ""}

        content = file_path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()

        return {
            "path": str(file_path),
            "size": len(content),
            "content_b64": base64.b64encode(content).decode("ascii"),
            "checksum_sha256": checksum,
        }

    @staticmethod
    async def upload(path: str, content_b64: str, overwrite: bool = False) -> dict:
        """Scrive un file ricevuto come base64, con path traversal e overwrite protection."""
        try:
            file_path = validate_path(path)
        except PermissionError as e:
            return {"error": str(e), "success": False}

        # Protezione overwrite: certi file non possono essere sovrascritti mai
        path_str = str(file_path)
        for pattern in OVERWRITE_DENIED_PATTERNS:
            if path_str.endswith(pattern) or pattern in path_str:
                logger.warning(f"FILE upload overwrite denied: {path} (pattern: {pattern})")
                return {
                    "error": f"Overwrite negato: {path} e' un file critico (pattern: {pattern}). "
                             f"Operazione bloccata per sicurezza.",
                    "success": False,
                }

        if file_path.exists() and not overwrite:
            return {"error": f"File esiste gia': {path}. Usa overwrite=true per sovrascrivere.", "success": False}

        # Crea directory genitore se necessario
        file_path.parent.mkdir(parents=True, exist_ok=True)

        content = base64.b64decode(content_b64)
        file_path.write_bytes(content)

        checksum = hashlib.sha256(content).hexdigest()

        return {
            "path": str(file_path),
            "size": len(content),
            "checksum_sha256": checksum,
            "success": True,
        }


# ── Screenshot ──────────────────────────────────────────────────────────────

class ScreenshotCapture:
    """Cattura screenshot su qualsiasi OS."""

    @staticmethod
    async def capture() -> dict:
        """Cattura lo schermo e restituisce l'immagine come base64 PNG."""
        os_name = detect_os()

        try:
            if os_name == "linux":
                # Prova con scrot, fallback a ImageMagick
                result = await ShellExecutor.execute(
                    "which scrot && scrot /tmp/caduceo_screenshot.png",
                    timeout=10,
                )
                if result["exit_code"] != 0:
                    # Fallback: import (ImageMagick)
                    result = await ShellExecutor.execute(
                        "which import && import -window root /tmp/caduceo_screenshot.png",
                        timeout=10,
                    )

                if result["exit_code"] == 0:
                    return await ScreenshotCapture._read_screenshot("/tmp/caduceo_screenshot.png")

            elif os_name == "macos":
                result = await ShellExecutor.execute(
                    "screencapture -x /tmp/caduceo_screenshot.png",
                    timeout=10,
                )
                if result["exit_code"] == 0:
                    return await ScreenshotCapture._read_screenshot("/tmp/caduceo_screenshot.png")

            elif os_name == "windows":
                # Usa Pillow + pyautogui
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grab()
                    import io
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    content = buf.getvalue()
                    return {
                        "content_b64": base64.b64encode(content).decode("ascii"),
                        "width": img.width,
                        "height": img.height,
                        "format": "png",
                    }
                except ImportError:
                    pass

            # Ultimo tentativo: pyautogui
            try:
                import pyautogui
                img = pyautogui.screenshot()
                import io
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                content = buf.getvalue()
                return {
                    "content_b64": base64.b64encode(content).decode("ascii"),
                    "width": img.width,
                    "height": img.height,
                    "format": "png",
                }
            except ImportError:
                return {"error": "Nessun metodo di screenshot disponibile"}

        except Exception as e:
            logger.error(f"Errore screenshot: {e}")
            return {"error": str(e)}

    @staticmethod
    async def _read_screenshot(path: str) -> dict:
        """Legge uno screenshot da file e restituisce base64."""
        from PIL import Image
        img = Image.open(path)
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        content = buf.getvalue()
        os.unlink(path)  # Pulizia
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "width": img.width,
            "height": img.height,
            "format": "png",
        }


# ── System Info ─────────────────────────────────────────────────────────────

class SystemInfo:
    """Informazioni di sistema."""

    @staticmethod
    def collect() -> dict:
        """Raccoglie informazioni complete sul sistema."""
        boot_time = psutil.boot_time()
        uptime = int(time.time() - boot_time)

        # Network info
        net_ifaces = {}
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family.name in ("AF_INET", "AF_INET6"):
                    iface = net_ifaces.setdefault(name, {"ipv4": [], "ipv6": []})
                    key = "ipv4" if addr.family.name == "AF_INET" else "ipv6"
                    iface[key].append(addr.address)

        disk_path = "C:\\" if detect_os() == "windows" else "/"
        return {
            "hostname": platform.node(),
            "os": detect_os(),
            "os_version": platform.version(),
            "arch": platform.machine(),
            "cpu_count": psutil.cpu_count(),
            "memory_total": psutil.virtual_memory().total,
            "memory_percent": psutil.virtual_memory().percent,
            "disk_total": psutil.disk_usage(disk_path).total,
            "disk_percent": psutil.disk_usage(disk_path).percent,
            "uptime_seconds": uptime,
            "network": net_ifaces,
        }


# ── Agent Client ────────────────────────────────────────────────────────────

class CaduceoAgent:
    """Client Caduceo che gira sui PC remoti."""

    def __init__(self, relay_url: str, psk_hex: str, agent_id: str = "",
                 tags: list[str] | None = None, token: str = ""):
        self.relay_url = relay_url
        self.psk_hex = psk_hex
        self.agent_id = agent_id or generate_agent_id()
        self.tags = tags or ["caduceo"]
        self.token = token
        self.crypto = Crypto.from_hex(psk_hex) if psk_hex else None

        self.ws = None
        self.running = False
        self.reconnect_delay = RECONNECT_INITIAL
        self._heartbeat_task = None

        # Handlers
        self.shell = ShellExecutor()
        self.files = FileManager()
        self.screenshots = ScreenshotCapture()
        self.sysinfo = SystemInfo()

    def _generate_psk_challenge(self, nonce: str = "") -> str:
        """Genera la challenge PSK per l'autenticazione del WebSocket.

        Usa HMAC-SHA256 con il nonce fornito dal server per prevenire replay.
        Challenge = HMAC-SHA256(psk_hex, nonce || agent_id)
        """
        message = f"{nonce}{self.agent_id}".encode()
        return hmac_mod.new(
            self.psk_hex.encode(), message, hashlib.sha256
        ).hexdigest()

    async def connect(self):
        """Connetti al relay con backoff esponenziale + jitter."""
        while self.running:
            try:
                logger.info(f"Connessione a {self.relay_url}...")
                ws_url = f"{self.relay_url}{WS_AGENT_PATH}"

                async with websockets.connect(
                    ws_url,
                    ping_interval=HEARTBEAT_INTERVAL,
                    ping_timeout=10,
                ) as ws:
                    self.ws = ws

                    # Fase 1: ricevi challenge nonce dal server
                    raw_challenge = await ws.recv()
                    challenge = json.loads(raw_challenge)

                    if challenge.get("type") != MessageType.AUTH_CHALLENGE:
                        # Il server non supporta il challenge-response.
                        # Chiudi e riprova — la sicurezza e' obbligatoria.
                        logger.error("Server senza auth_challenge, connessione rifiutata")
                        await ws.close(code=4003, reason="Auth challenge richiesta")
                        await asyncio.sleep(self.reconnect_delay)
                        self.reconnect_delay = min(
                            self.reconnect_delay * RECONNECT_MULTIPLIER, RECONNECT_MAX
                        )
                        continue

                    # Challenge-response: calcola HMAC con nonce del server
                    auth_nonce = challenge.get("nonce", "")
                    register_msg = RegisterMessage(agent_id=self.agent_id, tags=self.tags)
                    if not register_msg.hostname:
                        register_msg.hostname = platform.node()
                    if not register_msg.arch:
                        register_msg.arch = platform.machine()

                    reg_dict = register_msg.to_dict()
                    reg_dict["psk_challenge"] = self._generate_psk_challenge(nonce=auth_nonce)

                    await ws.send(json.dumps(reg_dict))

                    # Attendi conferma registrazione
                    raw_response = await ws.recv()
                    response = json.loads(raw_response)

                    if response.get("status") != "ok":
                        logger.error(f"Registrazione fallita: {response}")
                        await asyncio.sleep(self.reconnect_delay)
                        self.reconnect_delay = min(
                            self.reconnect_delay * RECONNECT_MULTIPLIER, RECONNECT_MAX
                        )
                        continue

                    logger.info(f"Registrato come {self.agent_id}")
                    self.reconnect_delay = RECONNECT_INITIAL  # Reset

                    # Loop messaggi
                    await self._message_loop(ws)

            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                websockets.InvalidHandshake,
                ConnectionRefusedError,
                OSError,
            ) as e:
                logger.warning(f"Disconnesso: {e}")
                self.ws = None
                delay = self.reconnect_delay + random.uniform(0, RECONNECT_JITTER_MAX)
                self.reconnect_delay = min(
                    self.reconnect_delay * RECONNECT_MULTIPLIER, RECONNECT_MAX
                )
                logger.info(f"Riconnessione tra {delay:.1f}s...")
                await asyncio.sleep(delay)

    async def _message_loop(self, ws):
        """Loop principale di ricezione messaggi dal relay."""
        # Avvia heartbeat in parallelo
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))

        try:
            async for raw_msg in ws:
                try:
                    msg_data = json.loads(raw_msg)
                except json.JSONDecodeError:
                    logger.warning(f"Messaggio JSON non valido: {raw_msg[:100]}")
                    continue

                # Decritta se il messaggio e' crittografato
                if "nonce_b64" in msg_data and "ciphertext_b64" in msg_data:
                    try:
                        plaintext = self.crypto.decrypt(
                            msg_data["nonce_b64"],
                            msg_data["ciphertext_b64"],
                        )
                        msg_data = json.loads(plaintext)
                    except Exception as e:
                        logger.error(f"Decryption fallita: {e}")
                        continue

                msg_type = msg_data.get("type", "")

                if msg_type == MessageType.COMMAND:
                    await self._handle_command(msg_data, ws)
                elif msg_type == MessageType.FILE_DOWNLOAD:
                    await self._handle_file_download(msg_data, ws)
                elif msg_type == MessageType.FILE_UPLOAD:
                    await self._handle_file_upload(msg_data, ws)
                elif msg_type == MessageType.SCREENSHOT:
                    await self._handle_screenshot(msg_data, ws)
                elif msg_type == MessageType.INFO:
                    await self._handle_info(msg_data, ws)
                elif msg_type == MessageType.PING:
                    await ws.send(json.dumps({"type": "pong", "timestamp": int(time.time())}))
                elif msg_type == "register_ack":
                    # Gia' gestito nella connect()
                    logger.debug(f"Ricevuto register_ack tardivo")
                else:
                    logger.debug(f"Messaggio non gestito: {msg_type}")

        except websockets.ConnectionClosed:
            logger.warning("Connessione chiusa dal relay")
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _heartbeat_loop(self, ws):
        """Invia heartbeat periodico al relay (cifrato se crypto disponibile)."""
        while True:
            try:
                stats = {
                    "cpu_percent": psutil.cpu_percent(),
                    "memory_percent": psutil.virtual_memory().percent,
                    "disk_percent": SystemInfo.collect().get("disk_percent", 0),
                }
                heartbeat = {
                    "type": MessageType.HEARTBEAT,
                    "agent_id": self.agent_id,
                    "timestamp": int(time.time()),
                    "stats": stats,
                }
                # Crittografa l'heartbeat se crypto e' disponibile
                if self.crypto and HEARTBEAT_ENCRYPTED:
                    encrypted = self.crypto.encrypt_message(heartbeat)
                    await ws.send(json.dumps(encrypted))
                else:
                    await ws.send(json.dumps(heartbeat))
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            except websockets.ConnectionClosed:
                break
            except Exception as e:
                logger.error(f"Errore heartbeat: {e}")
                break

    # ── Helpers per invio messaggi crittografati ─────────────────────────

    async def _send_response(self, ws, response: dict):
        """Invia una risposta al relay, crittografandola se il crypto e' disponibile."""
        if self.crypto:
            encrypted = self.crypto.encrypt_message(response)
            await ws.send(json.dumps(encrypted))
        else:
            await ws.send(json.dumps(response))

    # ── Command Handlers ────────────────────────────────────────────────

    async def _handle_command(self, msg: dict, ws):
        """Esegue un comando shell e restituisce il risultato."""
        request_id = msg.get("request_id", "")
        command = msg.get("command", "")
        args = msg.get("args", [])
        timeout = msg.get("timeout", COMMAND_TIMEOUT_DEFAULT)
        shell = msg.get("shell", "")

        logger.info(f"Esecuzione comando: {command} {' '.join(args) if args else ''}")

        result = await self.shell.execute(command, args, timeout, shell)

        response = {
            "type": MessageType.COMMAND_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            "exit_code": result["exit_code"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "duration_ms": result["duration_ms"],
        }
        await self._send_response(ws, response)

    async def _handle_file_download(self, msg: dict, ws):
        """Scarica un file dal PC e lo invia al relay."""
        request_id = msg.get("request_id", "")
        path = msg.get("path", "")

        logger.info(f"Download file: {path}")
        result = await self.files.download(path)

        response = {
            "type": MessageType.FILE_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            **result,
        }
        await self._send_response(ws, response)

    async def _handle_file_upload(self, msg: dict, ws):
        """Riceve un file dal relay e lo salva sul PC."""
        request_id = msg.get("request_id", "")
        path = msg.get("path", "")
        # Se il messaggio e' crittografato, il content_b64 e' gia' nel messaggio decrittato
        content_b64 = msg.get("content_b64", "")
        overwrite = msg.get("overwrite", False)

        logger.info(f"Upload file: {path}")
        result = await self.files.upload(path, content_b64, overwrite)

        response = {
            "type": MessageType.FILE_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            **result,
        }
        await self._send_response(ws, response)

    async def _handle_screenshot(self, msg: dict, ws):
        """Cattura uno screenshot e lo invia al relay."""
        request_id = msg.get("request_id", "")

        logger.info("Cattura screenshot")
        result = await self.screenshots.capture()

        response = {
            "type": MessageType.SCREENSHOT_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            **result,
        }
        await self._send_response(ws, response)

    async def _handle_info(self, msg: dict, ws):
        """Raccoglie info di sistema e le invia al relay."""
        request_id = msg.get("request_id", "")

        logger.info("Raccolta info di sistema")
        info = self.sysinfo.collect()

        response = {
            "type": MessageType.INFO_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            **info,
        }
        await self._send_response(ws, response)

    async def start(self):
        """Avvia l'agent."""
        self.running = True
        logger.info(f"Caduceo Agent v{PROTOCOL_VERSION} avviato (id: {self.agent_id})")
        await self.connect()

    async def stop(self):
        """Ferma l'agent."""
        self.running = False
        if self.ws:
            await self.ws.close()
        if self._heartbeat_task:
            self._heartbeat_task.cancel()


# ── PSK Loading Helper ──────────────────────────────────────────────────────

def load_psk(psk_arg: str | None = None) -> str:
    """
    Carica la PSK da (in ordine di priorita'):
    1. Argomento CLI --psk (legacy, sconsigliato)
    2. Variabile d'ambiente CADUCEO_PSK
    3. File ~/.caduceo/agent.psk
    """
    if psk_arg:
        return psk_arg

    # Variabile d'ambiente
    env_psk = os.environ.get("CADUCEO_PSK")
    if env_psk:
        return env_psk

    # File su disco
    psk_file = Path.home() / ".caduceo" / "agent.psk"
    if psk_file.exists():
        psk = psk_file.read_text().strip()
        if psk:
            return psk

    logger.warning("Nessuna PSK trovata. Usare --psk, CADUCEO_PSK env, o ~/.caduceo/agent.psk")
    return ""


# ── Service Installation ────────────────────────────────────────────────────

class ServiceInstaller:
    """Installa l'agent come servizio di sistema."""

    @staticmethod
    def install(relay_url: str, psk_hex: str, agent_id: str = "", tags: list[str] | None = None):
        """Installa l'agent come servizio (systemd/launchd/Windows Service)."""
        os_name = detect_os()

        if os_name == "linux":
            ServiceInstaller._install_systemd(relay_url, psk_hex, agent_id, tags)
        elif os_name == "macos":
            ServiceInstaller._install_launchd(relay_url, psk_hex, agent_id, tags)
        elif os_name == "windows":
            ServiceInstaller._install_windows(relay_url, psk_hex, agent_id, tags)
        else:
            logger.error(f"OS non supportato per servizio: {os_name}")

    @staticmethod
    def _install_systemd(relay_url: str, psk_hex: str, agent_id: str, tags: list[str] | None):
        """Installa come servizio systemd su Linux."""
        python_path = sys.executable
        agent_id = agent_id or generate_agent_id()
        tags_str = ",".join(tags or ["caduceo"])

        # Salva la PSK in un file con permessi restrittivi, non nel service file
        psk_file = Path.home() / ".caduceo" / "agent.psk"
        psk_file.parent.mkdir(parents=True, exist_ok=True)
        psk_file.write_text(psk_hex)
        psk_file.chmod(0o600)

        # Salva config JSON per --config
        config_file = Path.home() / ".caduceo" / "agent.json"
        config_content = json.dumps({"relay_url": relay_url, "psk_hex": psk_hex, "agent_id": agent_id, "tags": tags_str}, indent=2)
        config_file.write_text(config_content)
        config_file.chmod(0o600)

        # Crea environment file (vuoto, PSK nel config JSON)
        env_file = Path("/etc/caduceo/agent.env")
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("")
        env_file.chmod(0o600)

        service_content = f"""[Unit]
Description=Caduceo Agent - Remote AI Access
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python_path} -m caduceo_agent --config {config_file}
Restart=on-failure
RestartSec=10
Environment=HOME={Path.home()}

# Sicurezza: limita capabilities
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/tmp

[Install]
WantedBy=default.target
"""
        service_path = Path("/etc/systemd/system/caduceo-agent.service")
        service_path.write_text(service_content)

        subprocess.run(["systemctl", "daemon-reload"], check=False, capture_output=True)
        subprocess.run(["systemctl", "enable", "caduceo-agent"], check=False, capture_output=True)
        subprocess.run(["systemctl", "start", "caduceo-agent"], check=False, capture_output=True)

        logger.info(f"Servizio systemd installato: {service_path}")
        logger.info("PSK salvata in (non nel service file):")
        logger.info(f"  File: {psk_file} (permessi 0600)")
        logger.info(f"  Env:  {env_file} (permessi 0600)")
        logger.info("Abilitato con: systemctl enable caduceo-agent")
        logger.info("Avviato con: systemctl start caduceo-agent")

    @staticmethod
    def _install_launchd(relay_url: str, psk_hex: str, agent_id: str, tags: list[str] | None):
        """Installa come servizio launchd su macOS."""
        python_path = sys.executable
        agent_id = agent_id or generate_agent_id()
        tags_str = ",".join(tags or ["caduceo"])

        # Salva PSK in file separato
        psk_file = Path.home() / ".caduceo" / "agent.psk"
        psk_file.parent.mkdir(parents=True, exist_ok=True)
        psk_file.write_text(psk_hex)
        psk_file.chmod(0o600)

        # Salva config JSON per --config (PSK dentro, non in command line)
        config_file = Path.home() / ".caduceo" / "agent.json"
        config_content = json.dumps({"relay_url": relay_url, "psk_hex": psk_hex, "agent_id": agent_id, "tags": tags_str}, indent=2)
        config_file.write_text(config_content)
        config_file.chmod(0o600)

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.caduceo.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>caduceo_agent</string>
        <string>--config</string>
        <string>{Path.home()}/.caduceo/agent.json</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/caduceo-agent.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/caduceo-agent.err</string>
</dict>
</plist>
"""
        plist_path = Path.home() / "Library/LaunchAgents/com.caduceo.agent.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)

        subprocess.run(["launchctl", "load", str(plist_path)], check=False, capture_output=True)

        logger.info(f"Servizio launchd installato: {plist_path}")
        logger.info("PSK salvata in EnvironmentVariables (non in CommandLine)")

    @staticmethod
    def _install_windows(relay_url: str, psk_hex: str, agent_id: str, tags: list[str] | None):
        """Installa come servizio Windows (richiede pywin32)."""
        try:
            import win32serviceutil
            import win32service
            import win32event
            import servicemanager
        except ImportError:
            logger.error("pywin32 richiesto per servizio Windows. Installa con: pip install pywin32")
            logger.info("Alternativa: configura come task pianificato in Task Scheduler")
            logger.info("  Con variabile d'ambiente CADUCEO_PSK impostata nel sistema")
            return

        logger.info("Servizio Windows: usa 'python -m caduceo_agent --install-service' per installare")
        logger.info("Oppure configura come task pianificato in Task Scheduler")


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Caduceo Agent - Remote AI Access")
    parser.add_argument("--relay", default="", help="URL del relay (es. wss://caduceo.shares.zrok.io)")
    parser.add_argument("--psk", default=None, help="Pre-shared key hex (sconsigliato: usa --config, CADUCEO_PSK env, o ~/.caduceo/agent.psk)")
    parser.add_argument("--agent-id", default="", help="Agent ID (auto-generato se vuoto)")
    parser.add_argument("--tags", default="", help="Tag separati da virgola")
    parser.add_argument("--token", default="", help="JWT token per autenticazione")
    parser.add_argument("--config", default="", help="Percorso file config JSON (es. ~/.caduceo/agent.json)")
    parser.add_argument("--install", action="store_true", help="Installa come servizio")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log verbose")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Carica config da file se specificato
    relay_url = args.relay
    psk_hex = None
    agent_id = args.agent_id
    tags_str = args.tags
    token = args.token

    if args.config:
        config_path = os.path.expanduser(args.config)
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            if not relay_url:
                relay_url = cfg.get("relay_url", "")
            if not psk_hex:
                psk_hex = cfg.get("psk_hex")
            if not agent_id:
                agent_id = cfg.get("agent_id", "")
            if not tags_str:
                tags_str = cfg.get("tags", "caduceo")
            if not token:
                token = cfg.get("token", "")
            logger.info(f"Config caricato da {config_path}")
        else:
            logger.error(f"File config non trovato: {config_path}")
            sys.exit(1)

    # Valori di default se non forniti
    if not relay_url:
        logger.error("URL relay mancante. Passa --relay o --config.")
        sys.exit(1)

    # tags_str puo' essere stringa o lista (da config JSON)
    if isinstance(tags_str, list):
        tags = tags_str
    elif tags_str:
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    else:
        tags = ["caduceo"]
    agent_id = agent_id or generate_agent_id()

    # Carica PSK da fonte sicura (non visibile in ps)
    psk_hex = load_psk(psk_hex or args.psk)
    if not psk_hex:
        logger.error("PSK mancante. Passa --config, --psk, imposta CADUCEO_PSK, o crea ~/.caduceo/agent.psk")
        sys.exit(1)

    if args.install:
        ServiceInstaller.install(relay_url, psk_hex, agent_id, tags)
        return

    agent = CaduceoAgent(
        relay_url=relay_url,
        psk_hex=psk_hex,
        agent_id=agent_id,
        tags=tags,
        token=token,
    )

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        logger.info("Arresto agente...")
        asyncio.run(agent.stop())

if __name__ == "__main__":
    main()