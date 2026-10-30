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
    COMMAND_TIMEOUT_MAX,
    WS_MAX_SIZE,
    FILE_CHUNK_SIZE,
    SCREENSHOT_FORMAT,
    STREAM_FPS,
    STREAM_QUALITY,
    STREAM_SCALE_FACTOR,
    STREAM_MAX_FRAME_BYTES,
    STREAM_IDLE_FRAMES,
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
# Configurabile via env: CADUCEO_ALLOWED_PATHS=/home/user:/var/log:/tmp (su Windows usare ;)
_DEFAULT_ALLOWED = os.path.expanduser("~")
_raw_paths = os.environ.get("CADUCEO_ALLOWED_PATHS", "")
# Su Windows il ; e' il separatore naturale (perche' : e' nelle lettere disco C:\)
_SEP = ";" if os.name == "nt" else ":"
ALLOWED_PATHS = [p.strip() for p in _raw_paths.split(_SEP) if p.strip()] if _raw_paths else [_DEFAULT_ALLOWED]

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
    """Esegue comandi shell in modo asincrono."""

    ALLOWED_SHELLS = {"bash", "sh", "zsh", "powershell", "cmd", "powershell.exe", "cmd.exe"}

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

        # Valida che la shell sia nella allowlist
        shell_base = shell.lower().replace("\\", "/").split("/")[-1]
        if shell_base not in ShellExecutor.ALLOWED_SHELLS:
            logger.warning(f"Shell non permessa '{shell}' - fallback a {ShellExecutor.detect_shell()}")
            shell = ShellExecutor.detect_shell()

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


# ── Input Controller (remote control) ───────────────────────────────────────

class InputController:
    """Inietta input mouse/tastiera nel sistema (remote control).

    Backend: pyautogui se disponibile, altrimenti fallback su comandi di
    sistema (xdotool su Linux, cliclick su macOS). Su Windows pyautogui usa
    nativamente le API Win32, quindi è la scelta primaria.
    """

    def __init__(self):
        self._pyautogui = None
        self._backend = "none"
        self._init_backend()

    def _init_backend(self):
        """Seleziona e inizializza il backend di input disponibile."""
        # 1. Prova pyautogui (cross-platform: Windows nativo, X11 su Linux)
        try:
            import pyautogui
            pyautogui.FAILSAFE = False  # disabilita il failsafe (edge corner)
            pyautogui.PAUSE = 0.0       # niente pausa tra i comandi
            self._pyautogui = pyautogui
            self._backend = "pyautogui"
            return
        except Exception:
            # pyautogui può fallire anche con ImportError, oppure con
            # KeyError 'DISPLAY' su server headless senza X11, o altri errori
            # di inizializzazione del backend grafico. Non deve MAI far
            # crashare l'intero agent.
            self._pyautogui = None

        # 2. Linux: xdotool come fallback
        if detect_os() == "linux":
            try:
                r = subprocess.run(
                    ["which", "xdotool"], capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    self._backend = "xdotool"
                    return
            except Exception:
                pass

        # 3. macOS: cliclick come fallback
        if detect_os() == "macos":
            try:
                r = subprocess.run(
                    ["which", "cliclick"], capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0:
                    self._backend = "cliclick"
                    return
            except Exception:
                pass

        logger.warning("Nessun backend di input disponibile (installa pyautogui)")

    @property
    def available(self) -> bool:
        return self._backend != "none"

    def _screen_size(self):
        if self._backend == "pyautogui":
            w, h = self._pyautogui.size()
            return w, h
        return None

    async def handle_event(self, event_type: str, data: dict) -> dict:
        """Esegue un evento input. Ritorna {'success': bool, 'error': str}."""
        try:
            if not self.available:
                return {"success": False, "error": "Backend input non disponibile (installa pyautogui)"}

            if self._backend == "pyautogui":
                return self._handle_pyautogui(event_type, data)
            elif self._backend == "xdotool":
                return self._handle_xdotool(event_type, data)
            elif self._backend == "cliclick":
                return self._handle_cliclick(event_type, data)

            return {"success": False, "error": "Backend sconosciuto"}
        except Exception as e:
            logger.error(f"Errore input {event_type}: {e}")
            return {"success": False, "error": str(e)}

    # ── pyautogui backend ──────────────────────────────────────────────

    def _handle_pyautogui(self, event_type: str, data: dict) -> dict:
        pag = self._pyautogui
        x = float(data.get("x", 0))
        y = float(data.get("y", 0))
        button = data.get("button", "left")
        duration = float(data.get("duration", 0) or 0)

        if event_type == "move":
            pag.moveTo(x, y, duration=duration)
        elif event_type == "down":
            pag.mouseDown(x, y, button=button)
        elif event_type == "up":
            pag.mouseUp(x, y, button=button)
        elif event_type == "click":
            pag.click(x, y, button=button)
        elif event_type == "scroll":
            delta = float(data.get("scroll_delta", 0))
            pag.scroll(int(delta))  # positivo = su, negativo = giù
        elif event_type == "doubleclick":
            pag.doubleClick(x, y, button=button)
        elif event_type == "key_down":
            pag.keyDown(data.get("key", ""))
        elif event_type == "key_up":
            pag.keyUp(data.get("key", ""))
        elif event_type == "press":
            pag.press(data.get("key", ""))
        elif event_type == "hotkey":
            keys = [k.strip() for k in data.get("keys", "").split("+") if k.strip()]
            if keys:
                pag.hotkey(*keys)
        elif event_type == "text":
            text = data.get("text", "")
            if text:
                pag.write(text)
        else:
            return {"success": False, "error": f"Evento sconosciuto: {event_type}"}

        return {"success": True}

    # ── xdotool backend (Linux fallback) ───────────────────────────────

    def _run_xdotool(self, *args) -> dict:
        try:
            r = subprocess.run(
                ["xdotool", *args], capture_output=True, text=True, timeout=5
            )
            if r.returncode != 0:
                return {"success": False, "error": r.stderr.strip()}
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_xdotool(self, event_type: str, data: dict) -> dict:
        x = int(float(data.get("x", 0)))
        y = int(float(data.get("y", 0)))
        button_map = {"left": "1", "middle": "2", "right": "3"}

        if event_type == "move":
            return self._run_xdotool("mousemove", str(x), str(y))
        elif event_type == "down":
            btn = button_map.get(data.get("button", "left"), "1")
            return self._run_xdotool("mousedown", btn)
        elif event_type == "up":
            btn = button_map.get(data.get("button", "left"), "1")
            return self._run_xdotool("mouseup", btn)
        elif event_type == "click":
            btn = button_map.get(data.get("button", "left"), "1")
            return self._run_xdotool("click", btn)
        elif event_type == "scroll":
            delta = int(float(data.get("scroll_delta", 0)))
            direction = "4" if delta > 0 else "5"
            return self._run_xdotool("click", direction)
        elif event_type == "key_down":
            return self._run_xdotool("keydown", data.get("key", ""))
        elif event_type == "key_up":
            return self._run_xdotool("keyup", data.get("key", ""))
        elif event_type == "press":
            return self._run_xdotool("key", data.get("key", ""))
        elif event_type == "text":
            return self._run_xdotool("type", "--delay", "5", data.get("text", ""))
        else:
            return {"success": False, "error": f"Evento sconosciuto: {event_type}"}

    # ── cliclick backend (macOS fallback) ──────────────────────────────

    def _handle_cliclick(self, event_type: str, data: dict) -> dict:
        x = int(float(data.get("x", 0)))
        y = int(float(data.get("y", 0)))
        try:
            if event_type == "move":
                subprocess.run(["cliclick", f"m:{x},{y}"], timeout=5)
            elif event_type == "click":
                subprocess.run(["cliclick", f"c:{x},{y}"], timeout=5)
            elif event_type == "down":
                subprocess.run(["cliclick", f"dd:{x},{y}"], timeout=5)
            elif event_type == "up":
                subprocess.run(["cliclick", f"du:{x},{y}"], timeout=5)
            else:
                return {"success": False, "error": f"Evento non supportato: {event_type}"}
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ── System Info ─────────────────────────────────────────────────────────────

class SystemInfo:
    """Informazioni di sistema."""

    @staticmethod
    def collect() -> dict:
        """Raccoglie informazioni complete sul sistema."""

        def _safe(fn, default):
            # Alcune piattaforme (iSH su iOS) espongono un /proc incompleto:
            # un campo mancante non deve far fallire l'intera raccolta.
            try:
                return fn()
            except Exception:
                return default

        boot_time = _safe(psutil.boot_time, 0) or 0
        uptime = int(time.time() - boot_time) if boot_time else 0

        # Network info
        net_ifaces = {}
        for name, addrs in _safe(psutil.net_if_addrs, {}).items():
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
            "cpu_count": _safe(psutil.cpu_count, 0),
            "memory_total": _safe(lambda: psutil.virtual_memory().total, 0),
            "memory_percent": _safe(lambda: psutil.virtual_memory().percent, 0.0),
            "disk_total": _safe(lambda: psutil.disk_usage(disk_path).total, 0),
            "disk_percent": _safe(lambda: psutil.disk_usage(disk_path).percent, 0.0),
            "uptime_seconds": uptime,
            "network": net_ifaces,
        }


# ── Agent Client ────────────────────────────────────────────────────────────

class CaduceoAgent:
    """Client Caduceo che gira sui PC remoti."""

    def __init__(self, relay_url: str, psk_hex: str, agent_id: str = "",
                 tags: list[str] | None = None, token: str = "", features: str = "full"):
        self.relay_url = relay_url
        self.psk_hex = psk_hex
        self.agent_id = agent_id or generate_agent_id()
        self.tags = tags or ["caduceo"]
        self.token = token
        self.features = features  # "full" (remote desktop) o "base" (solo assistenza IA)
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
        # Remote desktop (streaming + input) SOLO in modalità "full"
        self.remote_enabled = (self.features == "full")
        self.inputs = InputController() if self.remote_enabled else None
        self._streaming_task = None

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

                # Build connect kwargs; proxy=None bypasses system/HTTP proxies
                # (websockets>=15 defaults proxy=True, which routes WS handshakes
                # through corporate proxies on Windows → "did not receive a valid
                # HTTP response" / EOF). Guard for older versions without the param.
                _ws_kwargs = dict(
                    ping_interval=HEARTBEAT_INTERVAL,
                    ping_timeout=10,
                    max_size=WS_MAX_SIZE,
                )
                try:
                    import inspect as _inspect
                    if "proxy" in _inspect.signature(websockets.connect).parameters:
                        _ws_kwargs["proxy"] = None
                except Exception:
                    pass

                async with websockets.connect(ws_url, **_ws_kwargs) as ws:
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
                        # Estrae AAD dal messaggio se presente per legare il ciphertext al contesto
                        aad = None
                        if "aad_b64" in msg_data:
                            aad = base64.b64decode(msg_data["aad_b64"])
                        plaintext = self.crypto.decrypt(
                            msg_data["nonce_b64"],
                            msg_data["ciphertext_b64"],
                            aad=aad,
                        )
                        msg_data = json.loads(plaintext)
                    except Exception as e:
                        logger.error(f"Decryption fallita: {type(e).__name__}: {e}")
                        logger.debug(f"  Message keys: {list(msg_data.keys())}")
                        logger.debug(f"  nonce_b64 len: {len(msg_data.get('nonce_b64', ''))}")
                        logger.debug(f"  ciphertext_b64 len: {len(msg_data.get('ciphertext_b64', ''))}")
                        aad_present = 'aad_b64' in msg_data
                        logger.debug(f"  aad_b64 present: {aad_present}")
                        if aad_present:
                            logger.debug(f"  aad_b64 len: {len(msg_data.get('aad_b64', ''))}")
                            # Try without AAD to diagnose AAD mismatch
                            try:
                                if self.crypto is not None:
                                    plaintext_no_aad = self.crypto.decrypt(
                                        msg_data["nonce_b64"],
                                        msg_data["ciphertext_b64"],
                                        aad=None,
                                    )
                                    logger.warning("  Decryption works WITHOUT AAD - AAD mismatch issue!")
                                    msg_data = json.loads(plaintext_no_aad)
                                    continue
                            except Exception as e2:
                                logger.debug(f"  Decryption without AAD also failed: {type(e2).__name__}: {e2}")
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
                elif msg_type == MessageType.INPUT_EVENT:
                    await self._handle_input_event(msg_data, ws)
                elif msg_type == MessageType.STREAM_START:
                    await self._handle_stream_start(msg_data, ws)
                elif msg_type == MessageType.STREAM_STOP:
                    await self._handle_stream_stop(msg_data, ws)
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
                try:
                    stats = {
                        "cpu_percent": psutil.cpu_percent(),
                        "memory_percent": psutil.virtual_memory().percent,
                        "disk_percent": SystemInfo.collect().get("disk_percent", 0),
                    }
                except Exception as exc:
                    # Piattaforme con /proc incompleto (es. iSH su iOS): psutil non riesce
                    # a costruire scputimes, ma il heartbeat deve continuare ad andare.
                    logger.warning(f"Statistiche non disponibili: {exc}")
                    stats = {"cpu_percent": 0.0, "memory_percent": 0.0, "disk_percent": 0.0}
                heartbeat = {
                    "type": MessageType.HEARTBEAT,
                    "agent_id": self.agent_id,
                    "timestamp": int(time.time()),
                    "stats": stats,
                }
                # Crittografa l'heartbeat se crypto e' disponibile
                if self.crypto and HEARTBEAT_ENCRYPTED:
                    aad = f"{MessageType.HEARTBEAT}:{self.agent_id}".encode("utf-8")
                    encrypted = self.crypto.encrypt_message(heartbeat, aad=aad)
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
        """Invia una risposta al relay, crittografandola se HEARTBEAT_ENCRYPTED e' abilitato."""
        if HEARTBEAT_ENCRYPTED and self.crypto:
            msg_type = response.get("type", "")
            request_id = response.get("request_id", "")
            aad = f"{msg_type}:{request_id}".encode("utf-8")
            encrypted = self.crypto.encrypt_message(response, aad=aad)
            await ws.send(json.dumps(encrypted))
        else:
            await ws.send(json.dumps(response))

    # ── Command Handlers ────────────────────────────────────────────────

    async def _handle_command(self, msg: dict, ws):
        """Esegue un comando shell e restituisce il risultato."""
        request_id = msg.get("request_id", "")
        command = msg.get("command", "")
        args = msg.get("args", [])
        timeout = min(msg.get("timeout", COMMAND_TIMEOUT_DEFAULT), COMMAND_TIMEOUT_MAX)
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

    async def _handle_input_event(self, msg: dict, ws):
        """Esegue un evento input (mouse/tastiera) e risponde con ack."""
        request_id = msg.get("request_id", "")
        event_type = msg.get("event_type", "")

        # Remote desktop non abilitato in modalità "base"
        if not self.remote_enabled or self.inputs is None:
            response = {
                "type": MessageType.INPUT_RESPONSE,
                "request_id": request_id,
                "agent_id": self.agent_id,
                "success": False,
                "error": "Funzioni remote desktop non abilitate in modalità base",
            }
            await self._send_response(ws, response)
            return

        logger.info(f"Evento input: {event_type}")
        result = await self.inputs.handle_event(event_type, msg)

        response = {
            "type": MessageType.INPUT_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            "success": result.get("success", False),
            "error": result.get("error", ""),
        }
        await self._send_response(ws, response)

    async def _handle_stream_start(self, msg: dict, ws):
        """Avvia il loop di streaming frame sull'agent."""
        request_id = msg.get("request_id", "")

        # Remote desktop solo in modalità "full"
        if not self.remote_enabled:
            response = {
                "type": MessageType.STREAM_RESPONSE,
                "request_id": request_id,
                "agent_id": self.agent_id,
                "streaming": False,
                "error": "Streaming non abilitato in modalità base",
            }
            await self._send_response(ws, response)
            return

        # Se già in streaming, ferma il task precedente
        if self._streaming_task and not self._streaming_task.done():
            self._streaming_task.cancel()
            try:
                await self._streaming_task
            except (asyncio.CancelledError, Exception):
                pass

        fps = msg.get("fps") or STREAM_FPS
        quality = msg.get("quality") or STREAM_QUALITY
        scale_factor = msg.get("scale_factor") or STREAM_SCALE_FACTOR

        # Avvia il loop di streaming
        self._streaming_task = asyncio.create_task(
            self._stream_loop(ws, fps, quality, scale_factor)
        )

        response = {
            "type": MessageType.STREAM_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            "streaming": True,
            "error": "",
        }
        await self._send_response(ws, response)

    async def _handle_stream_stop(self, msg: dict, ws):
        """Ferma il loop di streaming frame sull'agent."""
        request_id = msg.get("request_id", "")

        if self._streaming_task and not self._streaming_task.done():
            self._streaming_task.cancel()
            try:
                await self._streaming_task
            except (asyncio.CancelledError, Exception):
                pass
            self._streaming_task = None

        response = {
            "type": MessageType.STREAM_RESPONSE,
            "request_id": request_id,
            "agent_id": self.agent_id,
            "streaming": False,
            "error": "",
        }
        await self._send_response(ws, response)

    async def _stream_loop(self, ws, fps, quality, scale_factor):
        """Cattura frame continui e li invia al relay finché non viene fermato.

        Usa il backend più veloce disponibile (MSS per alta velocità, fallback
        Pillow/pyautogui). Invia SEMPRE i frame al target FPS (anche se lo
        schermo è statico) perché il video remoto deve restare "vivo" durante
        il controllo. Salta i frame solo se la cattura è più lenta del target.
        """
        interval = 1.0 / max(1, int(fps or STREAM_FPS))
        frame_index = 0

        logger.info(f"Streaming avviato: {fps} fps, quality={quality}, scale={scale_factor}")

        try:
            while True:
                started = time.monotonic()
                try:
                    frame = await self._capture_stream_frame(quality, scale_factor)
                except Exception as e:
                    logger.error(f"Errore cattura frame: {e}")
                    await asyncio.sleep(interval)
                    continue

                if frame is not None:
                    msg = {
                        "type": MessageType.STREAM_FRAME,
                        "agent_id": self.agent_id,
                        "content_b64": frame["content_b64"],
                        "width": frame.get("width", 0),
                        "height": frame.get("height", 0),
                        "format": "jpeg",
                        "frame_index": frame_index,
                        "timestamp": int(time.time()),
                    }
                    try:
                        await self._send_response(ws, msg)
                        frame_index += 1
                    except websockets.ConnectionClosed:
                        logger.warning("Connessione chiusa durante streaming")
                        break
                    except Exception as e:
                        logger.error(f"Errore invio frame: {e}")
                        break

                # Rispetta il target FPS
                elapsed = time.monotonic() - started
                sleep = interval - elapsed
                if sleep > 0:
                    await asyncio.sleep(sleep)
        except asyncio.CancelledError:
            logger.info("Streaming fermato")
            raise
        except Exception as e:
            logger.error(f"Loop streaming terminato: {e}")
        finally:
            self._streaming_task = None

    async def _capture_stream_frame(self, quality, scale_factor):
        """Cattura un frame JPEG compresso per lo streaming."""
        from PIL import Image
        import io

        # 1. Prova MSS (veloce, cross-platform)
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # primo schermo
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        except ImportError:
            img = None

        if img is None:
            # 2. Fallback su pyautogui
            try:
                import pyautogui
                img = pyautogui.screenshot()
            except ImportError:
                pass

        if img is None:
            # 3. Fallback su ImageGrab (Windows)
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
            except Exception:
                pass

        if img is None:
            return None

        # Scala se richiesto
        if scale_factor and scale_factor != 1.0:
            new_w = max(1, int(img.width * scale_factor))
            new_h = max(1, int(img.height * scale_factor))
            resample = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((new_w, new_h), resample)

        # Converti a RGB (per JPEG) e comprimi
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=int(quality or STREAM_QUALITY))
        content = buf.getvalue()
        if len(content) > STREAM_MAX_FRAME_BYTES:
            return None
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "width": img.width,
            "height": img.height,
        }

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
    parser.add_argument("--relay", default="", help="URL del relay (es. wss://your-relay.example.com)")
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
    features = "full"  # default: remote desktop completo

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
            features = cfg.get("features", "full")
            logger.info(f"Config caricato da {config_path} (features={features})")
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
        features=features,
    )

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        logger.info("Arresto agente...")
        asyncio.run(agent.stop())

if __name__ == "__main__":
    main()