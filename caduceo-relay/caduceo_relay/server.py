"""Caduceo Relay - Server principale con FastAPI + WebSocket."""

import asyncio
import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import secrets
import stat
import time
from pathlib import Path
from urllib.parse import quote
import urllib.request
import urllib.error

import jwt
import aiosqlite
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from caduceo_relay.oidc_validator import OIDCConfig, OIDCValidator

from caduceo_common.constants import (
    WS_AGENT_PATH,
    HEARTBEAT_TIMEOUT,
    QUEUE_TTL_DEFAULT,
    QUEUE_MAX_SIZE,
    PROTOCOL_VERSION,
    JWT_EXPIRY,
    AUTH_NONCE_LENGTH,
    HEARTBEAT_ENCRYPTED,
    WS_MAX_SIZE,
    MessageType,
)
from caduceo_common.crypto import Crypto
from caduceo_common.messages import parse_message

logger = logging.getLogger("caduceo.relay")


# ── Config ──────────────────────────────────────────────────────────────────

class RelayConfig:
    def __init__(self):
        self.host = "0.0.0.0"
        self.port = 8443
        self.jwt_secret = ""
        self.psk_hex = ""
        self.download_key = ""  # chiave per accedere ai /download (protegge il PSK pubblico)
        self.queue_ttl = QUEUE_TTL_DEFAULT
        self.db_path = Path.home() / ".caduceo" / "relay.db"
        self.config_path = Path.home() / ".caduceo" / "relay.json"
        self.allowed_origins: list[str] = []

        # Genera PSK e JWT secret se non presenti
        if not self.psk_hex:
            self.psk_hex = Crypto.generate_key_hex()
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_hex(32)
        if not self.download_key:
            self.download_key = secrets.token_hex(16)  # chiave per proteggere i /download
        self.crypto = Crypto.from_hex(self.psk_hex)

    def save(self):
        """Salva la configurazione su file JSON con permessi restrittivi."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "host": self.host,
            "port": self.port,
            "jwt_secret": self.jwt_secret,
            "psk_hex": self.psk_hex,
            "download_key": self.download_key,
            "queue_ttl": self.queue_ttl,
        }
        self.config_path.write_text(json.dumps(data, indent=2))
        # Permessi restrittivi: solo il proprietario puo' leggere
        os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        logger.info(f"Config salvata in {self.config_path}")

    @classmethod
    def from_file(cls, path: Path | None = None) -> "RelayConfig":
        """Carica configurazione da file JSON. Se non esiste, crea una nuova."""
        if path is None:
            path = Path.home() / ".caduceo" / "relay.json"
        config = cls()
        config.config_path = path
        if path.exists():
            data = json.loads(path.read_text())
            for k, v in data.items():
                if hasattr(config, k):
                    setattr(config, k, v)
            if config.psk_hex:
                config.crypto = Crypto.from_hex(config.psk_hex)
            logger.info(f"Config caricata da {path}")
        else:
            # Prima esecuzione: genera PSK e JWT secret, salva
            config.jwt_secret = secrets.token_hex(32)
            config.save()
            logger.info(f"Nuova config creata e salvata in {path}")
        return config

    def save_credentials(self):
        """Salva credentials separati per Hermes (con permessi restrittivi)."""
        creds_path = Path.home() / ".caduceo" / "credentials.json"
        creds_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "relay_url": f"http://127.0.0.1:{self.port}",
            "relay_ws_url": f"ws://127.0.0.1:{self.port}",
            "jwt_secret": self.jwt_secret,
            "psk_hex": self.psk_hex,
            "note": "JWT token can be generated from jwt_secret. For production, use WSS and HTTPS",
        }
        creds_path.write_text(json.dumps(data, indent=2))
        os.chmod(creds_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


# ── Modelli API ─────────────────────────────────────────────────────────────

class CommandRequest(BaseModel):
    command: str
    args: list[str] = []
    timeout: int = 30
    interactive: bool = False
    shell: str = ""

class FileDownloadRequest(BaseModel):
    path: str

class FileUploadRequest(BaseModel):
    path: str
    content_b64: str
    overwrite: bool = False

class ScreenshotRequest(BaseModel):
    pass

class InputEventRequest(BaseModel):
    event_type: str
    x: float = 0.0
    y: float = 0.0
    button: str = ""
    scroll_delta: float = 0.0
    key: str = ""
    keys: str = ""
    text: str = ""
    duration: float = 0.0

class StreamStartRequest(BaseModel):
    fps: int = 0
    quality: int = 0
    scale_factor: float = 0.0

class StreamStopRequest(BaseModel):
    pass

ALLOWED_ROLES = {"admin", "viewer"}

class TokenRequest(BaseModel):
    subject: str = "hermes"
    role: str = "admin"

    def validate_role(self):
        if self.role not in ALLOWED_ROLES:
            raise ValueError(f"Role '{self.role}' non valido. Ruoli permessi: {', '.join(sorted(ALLOWED_ROLES))}")
        return self


# ── Registry Agent ──────────────────────────────────────────────────────────

class AgentInfo:
    """Informazioni su un agent connesso."""
    def __init__(self, agent_id: str, websocket: WebSocket):
        self.agent_id = agent_id
        self.websocket = websocket
        self.hostname = ""
        self.os = ""
        self.os_version = ""
        self.arch = ""
        self.python_version = ""
        self.tags: list[str] = []
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.stats: dict = {}
        self.latest_frame: dict | None = None   # ultimo frame streaming (per MJPEG)
        self.streaming: bool = False

    @property
    def is_online(self) -> bool:
        return time.time() - self.last_heartbeat < HEARTBEAT_TIMEOUT

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "os": self.os,
            "os_version": self.os_version,
            "arch": self.arch,
            "python_version": self.python_version,
            "tags": self.tags,
            "online": self.is_online,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "stats": self.stats,
        }


class AgentRegistry:
    """Registry degli agent connessi."""
    def __init__(self):
        self.agents: dict[str, AgentInfo] = {}
        self.pending_responses: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def register(self, agent: AgentInfo):
        # Se l'agent era gia' registrato, chiudi la vecchia connessione
        if agent.agent_id in self.agents:
            old = self.agents[agent.agent_id]
            try:
                await old.websocket.close()
            except Exception:
                pass
        self.agents[agent.agent_id] = agent
        logger.info(f"Agent registrato: {agent.agent_id} ({agent.hostname} / {agent.os})")

    def unregister(self, agent_id: str):
        if agent_id in self.agents:
            logger.info(f"Agent disconnesso: {agent_id}")
            del self.agents[agent_id]

    def get(self, agent_id: str) -> AgentInfo | None:
        return self.agents.get(agent_id)

    def list_online(self) -> list[AgentInfo]:
        return [a for a in self.agents.values() if a.is_online]

    def list_all(self) -> list[AgentInfo]:
        return list(self.agents.values())


# ── Coda Comandi Offline (SQLite) ───────────────────────────────────────────

class CommandQueue:
    """Coda comandi per agent offline. Persiste su SQLite."""

    def __init__(self, db_path: Path, ttl: int = QUEUE_TTL_DEFAULT):
        self.db_path = db_path
        self.ttl = ttl
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(str(self.db_path))
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS command_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_agent_id ON command_queue(agent_id)
            """)
            await self._db.commit()
        return self._db

    async def enqueue(self, agent_id: str, command: dict):
        """Metti in coda un comando per agent offline. Operazione atomica."""
        db = await self._get_db()
        now = time.time()
        expires_at = now + self.ttl
        command_json = json.dumps(command)

        # Transazione atomica: count + insert
        async with db.execute("BEGIN IMMEDIATE"):
            async with db.execute(
                "SELECT COUNT(*) FROM command_queue WHERE agent_id = ?",
                (agent_id,)
            ) as cursor:
                row = await cursor.fetchone()
                count = row[0] if row else 0
            if count >= QUEUE_MAX_SIZE:
                logger.warning(f"Coda piena per {agent_id} ({count} comandi)")
                await db.commit()
                return

            await db.execute(
                "INSERT INTO command_queue (agent_id, command, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (agent_id, command_json, now, expires_at)
            )
            await db.commit()
        logger.info(f"Comando in coda per {agent_id}: {command.get('type', 'unknown')}")

    async def dequeue(self, agent_id: str) -> list[dict]:
        """Recupera comandi in coda per agent appena riconnesso."""
        db = await self._get_db()
        now = time.time()

        # Rimuovi comandi scaduti
        await db.execute("DELETE FROM command_queue WHERE expires_at < ?", (now,))
        await db.commit()

        cursor = await db.execute(
            "SELECT id, command FROM command_queue WHERE agent_id = ? ORDER BY created_at ASC",
            (agent_id,)
        )
        rows = await cursor.fetchall()

        commands = []
        for row_id, command_json in rows:
            commands.append(json.loads(command_json))
            await db.execute("DELETE FROM command_queue WHERE id = ?", (row_id,))

        await db.commit()

        if commands:
            logger.info(f"Recuperati {len(commands)} comandi in coda per {agent_id}")

        return commands

    async def cleanup(self):
        """Rimuove comandi scaduti."""
        db = await self._get_db()
        now = time.time()
        await db.execute("DELETE FROM command_queue WHERE expires_at < ?", (now,))
        await db.commit()

    async def close(self):
        """Chiudi connessione DB."""
        if self._db:
            await self._db.close()
            self._db = None


# ── Audit Log (SQLite) ──────────────────────────────────────────────────────

class AuditLog:
    """Registro di audit per tutti i comandi e operazioni file inviate tramite il relay."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(str(self.db_path))
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    action TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    source_ip TEXT NOT NULL DEFAULT ''
                )
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_agent ON audit_log(agent_id)
            """)
            await self._db.commit()
        return self._db

    async def log(self, action: str, agent_id: str, subject: str = "",
                  detail: str = "", source_ip: str = ""):
        """Registra un evento di audit."""
        db = await self._get_db()
        now = time.time()
        await db.execute(
            "INSERT INTO audit_log (timestamp, action, agent_id, subject, detail, source_ip) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now, action, agent_id, subject, detail[:2000], source_ip)
        )
        await db.commit()
        # Log strutturato anche su stdout per journald
        logger.info(f"AUDIT action={action} agent={agent_id} subject={subject!r} "
                     f"detail={detail[:200]!r} ip={source_ip}")

    async def query(self, agent_id: str = "", action: str = "",
                    since: float = 0, limit: int = 100) -> list[dict]:
        """Interroga il log di audit."""
        db = await self._get_db()
        conditions = []
        params = []
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if action:
            conditions.append("action = ?")
            params.append(action)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        cursor = await db.execute(
            f"SELECT * FROM audit_log WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            params
        )
        rows = await cursor.fetchall()
        columns = ["id", "timestamp", "action", "agent_id", "subject", "detail", "source_ip"]
        return [dict(zip(columns, row)) for row in rows]

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None


# ── Impostazioni persistenti (server-side) ────────────────────────────────

class SettingsStore:
    """Persiste le impostazioni dell'Assistente IA sul server (file JSON),
    così NON dipendono dalla cache del browser. La chiave API resta sul
    server e non viene mai esposta al client."""
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}

    def load(self) -> dict:
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text())
        except Exception:
            self._data = {}
        return self._data

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self._persist()

    def save(self, data: dict):
        self._data = dict(data)
        self._persist()

    def _persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2))
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        except Exception:
            pass


# ── App FastAPI ─────────────────────────────────────────────────────────────

def create_app(config: RelayConfig | None = None) -> FastAPI:
    if config is None:
        config = RelayConfig()

    app = FastAPI(
        title="Caduceo Relay",
        description="Il bastone di Hermes che raggiunge ogni mondo",
        version=PROTOCOL_VERSION,
    )

    # CORS restrittivo: solo origins configurate, o disabilitato se vuoto
    allowed_origins = config.allowed_origins if config.allowed_origins else []
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    registry = AgentRegistry()
    queue = CommandQueue(config.db_path, config.queue_ttl)
    audit = AuditLog(config.db_path.parent / "audit.db")
    crypto = config.crypto
    settings = SettingsStore(config.db_path.parent / "ai_settings.json")
    settings.load()

    # ── OIDC (Authentik) ─────────────────────────────────────────────────────
    # OIDC è disattivato se issuer o client_id sono vuoti/placeholder. In
    # produzione impostare OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_REDIRECT_URI.
    oidc_cfg = OIDCConfig(
        issuer=os.environ.get(
            "OIDC_ISSUER",
            "http://127.0.0.1:9000/application/o/caduceo/",
        ),
        client_id=os.environ.get(
            "OIDC_CLIENT_ID",
            "",  # sostituire con il client_id reale di Authentik
        ),
        redirect_uri=os.environ.get(
            "OIDC_REDIRECT_URI",
            "http://localhost:8442/api/auth/oidc/callback",
        ),
    )
    oidc = OIDCValidator(oidc_cfg) if oidc_cfg.issuer and oidc_cfg.client_id else None
    oidc_sessions: dict[str, dict[str, str]] = {}

    def oidc_disabled() -> None:
        if oidc is None:
            raise HTTPException(501, "Login OIDC non configurato")

    def oidc_error_redirect(redirect_after: str, message: str) -> RedirectResponse:
        separator = "&" if "?" in redirect_after else "?"
        return RedirectResponse(
            url=f"{redirect_after}{separator}error={quote(message[:200])}",
            status_code=302,
        )


    def make_aad(message_type: str, request_id: str = "") -> bytes:
        """Genera AAD (Additional Authenticated Data) per legare il ciphertext al tipo messaggio."""
        return f"{message_type}:{request_id}".encode("utf-8")

    # Inizializza DB all'avvio
    @app.on_event("startup")
    async def startup():
        await queue._get_db()
        await audit._get_db()
        logger.info("CommandQueue + AuditLog DB inizializzati")

    @app.on_event("shutdown")
    async def shutdown():
        await queue.close()
        await audit.close()

    # ── Public Download Endpoint ────────────────────────────────────────────

    INSTALLER_DIR = Path("/mnt/nas_hermes/Cartella Agent Hermes")
    # DEPLOY_DIR: usa la dir di produzione se presente (dbcoemi /opt/caduceo/deploy),
    # altrimenti il default locale. Rende gli endpoint /download funzionanti su entrambi.
    DEPLOY_DIR = Path("/opt/caduceo/deploy") if Path("/opt/caduceo/deploy").exists() \
        else Path("/home/manutenzione/caduceo/deploy")
    WEB_DIR = Path(__file__).resolve().parent / "web"

    # ── Remote Control Web UI ───────────────────────────────────────────────

    @app.get("/")
    async def web_ui_root():
        """Pagina principale di controllo remoto."""
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/web/{filename}")
    async def web_ui_file(filename: str):
        """File statici della web UI (css, js)."""
        from fastapi.responses import FileResponse as FR
        # Protezione path traversal
        safe = Path(filename).name
        path = WEB_DIR / safe
        if not path.exists():
            raise HTTPException(404, "File non trovato")
        return FR(path)

    # ── Auth per download (protegge il PSK pubblico) ─────────────────────────

    def verify_download(key: str = Query(default="")):
        """Protegge gli endpoint /download/* con una chiave segreta.
        Senza la chiave corretta, i file (che contengono il PSK) NON sono scaricabili."""
        if not config.download_key:
            # Nessuna chiave configurata → blocca comunque (default sicuro)
            raise HTTPException(403, "Download protetto: chiave non configurata")
        if key != config.download_key:
            raise HTTPException(403, "Download protetti: chiave mancante o errata. Usa ?key=<download_key>")
        return True

    @app.get("/download/install.py")
    async def download_installer(platform: str = "windows", _auth: bool = Depends(verify_download)):
        """Download the Caduceo Agent installer. Protected by download key.
        ?platform=windows|linux|macos"""
        platform = platform.lower()
        if platform not in ("windows", "linux", "macos"):
            raise HTTPException(400, f"Platform non valida: {platform}. Usa: windows, linux, macos")
        # Prova prima sul NAS, poi fallback su deploy locale
        installer_path = INSTALLER_DIR / platform / "install.py"
        if not installer_path.exists():
            # Fallback: cerca nel deploy locale
            local_candidates = list(DEPLOY_DIR.glob("install*windows*.py"))
            if platform == "windows" and local_candidates:
                installer_path = local_candidates[0]
            else:
                raise HTTPException(404, f"Installer per {platform} non trovato (NAS non montato, nessun fallback locale)")
        return FileResponse(
            installer_path,
            filename=f"caduceo-agent-install-{platform}.py",
            media_type="text/x-python",
        )

    @app.get("/download/quick-install.ps1")
    async def download_quick_install_ps1(_auth: bool = Depends(verify_download)):
        """Download the Windows PowerShell quick-install script. Protected by download key."""
        path = DEPLOY_DIR / "quick-install.ps1"
        if not path.exists():
            raise HTTPException(404, "quick-install.ps1 non trovato")
        return FileResponse(path, filename="quick-install.ps1", media_type="text/plain")

    @app.get("/download/quick-install.bat")
    async def download_quick_install_bat(_auth: bool = Depends(verify_download)):
        """Download the Windows batch quick-install script. Protected by download key."""
        path = DEPLOY_DIR / "quick-install.bat"
        if not path.exists():
            raise HTTPException(404, "quick-install.bat non trovato")
        return FileResponse(path, filename="quick-install.bat", media_type="text/plain")

    @app.get("/download/quick-install.sh")
    async def download_quick_install_sh(_auth: bool = Depends(verify_download)):
        """Download the Linux/macOS quick-install script. Protected by download key."""
        path = DEPLOY_DIR / "quick-install.sh"
        if not path.exists():
            raise HTTPException(404, "quick-install.sh non trovato")
        return FileResponse(path, filename="quick-install.sh", media_type="text/x-shellscript")

    @app.get("/download/bootstrap-install.ps1")
    async def download_bootstrap_ps1(_auth: bool = Depends(verify_download)):
        """Download the Windows bootstrap installer (works without Python). Protected by download key."""
        path = DEPLOY_DIR / "bootstrap-install.ps1"
        if not path.exists():
            raise HTTPException(404, "bootstrap-install.ps1 non trovato")
        return FileResponse(path, filename="bootstrap-install.ps1", media_type="text/plain")

    @app.get("/download/bootstrap-install.bat")
    async def download_bootstrap_bat(_auth: bool = Depends(verify_download)):
        """Download the Windows bootstrap batch installer. Protected by download key."""
        path = DEPLOY_DIR / "bootstrap-install.bat"
        if not path.exists():
            raise HTTPException(404, "bootstrap-install.bat non trovato")
        return FileResponse(path, filename="bootstrap-install.bat", media_type="text/plain")

    @app.get("/download/install-agent.ps1")
    async def download_install_agent_ps1(_auth: bool = Depends(verify_download)):
        """Download the unified Windows installer script. Protected by download key."""
        path = DEPLOY_DIR / "install-agent.ps1"
        if not path.exists():
            raise HTTPException(404, "install-agent.ps1 non trovato")
        return FileResponse(path, filename="install-agent.ps1", media_type="text/plain")

    @app.get("/download/install-agent.bat")
    async def download_install_agent_bat(_auth: bool = Depends(verify_download)):
        """Download the unified Windows installer batch wrapper. Protected by download key."""
        path = DEPLOY_DIR / "install-agent.bat"
        if not path.exists():
            raise HTTPException(404, "install-agent.bat non trovato")
        return FileResponse(path, filename="install-agent.bat", media_type="text/plain")

    @app.get("/download/caduceo-setup.ps1")
    async def download_caduceo_setup_ps1(_auth: bool = Depends(verify_download)):
        """Download the self-contained Windows installer PS1 (no Python needed). Protected by download key."""
        path = DEPLOY_DIR / "caduceo-setup.ps1"
        if not path.exists():
            raise HTTPException(404, "caduceo-setup.ps1 non trovato")
        return FileResponse(path, filename="caduceo-setup.ps1", media_type="text/plain")

    @app.get("/download/caduceo-setup.bat")
    async def download_caduceo_setup_bat(_auth: bool = Depends(verify_download)):
        """Download the self-contained Windows installer BAT launcher. Protected by download key."""
        path = DEPLOY_DIR / "caduceo-setup.bat"
        if not path.exists():
            raise HTTPException(404, "caduceo-setup.bat non trovato")
        return FileResponse(path, filename="caduceo-setup.bat", media_type="text/plain")

    @app.get("/download/caduceo-agent.zip")
    async def download_agent_zip(_auth: bool = Depends(verify_download)):
        """Download the complete Windows agent package (ZIP). Protected by download key."""
        import glob
        candidates = list(DEPLOY_DIR.glob("caduceo-agent*.zip"))
        if not candidates:
            raise HTTPException(404, "caduceo-agent.zip non trovato")
        return FileResponse(candidates[-1], filename="caduceo-agent-windows.zip", media_type="application/zip")

    @app.get("/download/caduceo_common.whl")
    async def download_common_whl(_auth: bool = Depends(verify_download)):
        """Download caduceo-common wheel. Protected by download key."""
        import glob
        # Try deploy dir first, then dist dirs
        candidates = list(DEPLOY_DIR.glob("caduceo_common*.whl"))
        # Also check common/dist
        common_dist = Path("/home/manutenzione/caduceo/caduceo-common/dist")
        if common_dist.exists():
            candidates += list(common_dist.glob("caduceo_common*.whl"))
        if not candidates:
            raise HTTPException(404, "caduceo-common wheel non trovato")
        return FileResponse(candidates[-1], filename="caduceo_common-0.1.0-py3-none-any.whl", media_type="application/octet-stream")

    @app.get("/download/caduceo_agent.whl")
    async def download_agent_whl(_auth: bool = Depends(verify_download)):
        """Download caduceo-agent wheel. Protected by download key."""
        import glob
        candidates = list(DEPLOY_DIR.glob("caduceo_agent*.whl"))
        agent_dist = Path("/home/manutenzione/caduceo/caduceo-agent/dist")
        if agent_dist.exists():
            candidates += list(agent_dist.glob("caduceo_agent*.whl"))
        if not candidates:
            raise HTTPException(404, "caduceo-agent wheel non trovato")
        return FileResponse(candidates[-1], filename="caduceo_agent-0.1.0-py3-none-any.whl", media_type="application/octet-stream")

    # ── JWT Auth ─────────────────────────────────────────────────────────

    def verify_token(authorization: str = Header(default="", alias="Authorization"), token: str = Query(default="")) -> dict:
        """Verifica JWT token per API Hermes.

        Supporta sia l'header Authorization ("Bearer <token>") sia un query param
        `?token=<token>` (necessario per endpoint tipo MJPEG in <img> che non
        possono inviare header personalizzati).
        """
        auth = authorization or ""
        # Se manca l'header, usa il query param
        if not auth.startswith("Bearer "):
            if token:
                auth = f"Bearer {token}"
        if not auth.startswith("Bearer "):
            raise HTTPException(401, "Token mancante o formato errato. Usa: Bearer <token>")
        token_val = auth[7:]
        try:
            payload = jwt.decode(token_val, config.jwt_secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Token scaduto")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Token non valido")

    def verify_role(*allowed_roles: str):
        """Dependency che verifica il ruolo nel JWT token. Es: Depends(verify_role("admin"))"""
        def _check(user: dict = Depends(verify_token)):
            user_role = user.get("role", "")
            if user_role not in allowed_roles:
                raise HTTPException(403, f"Ruolo '{user_role}' non autorizzato. Richiesto: {', '.join(allowed_roles)}")
            return user
        return _check

    # ── WebSocket: Agent ─────────────────────────────────────────────────

    WS_MSG_MAX_SIZE = WS_MAX_SIZE  # Limite dimensione messaggi WebSocket

    @app.websocket(WS_AGENT_PATH)
    async def agent_endpoint(ws: WebSocket):
        """Endpoint WebSocket per agent remoti. Richiede autenticazione PSK con challenge-response."""
        await ws.accept()
        agent_info = None

        try:
            # Fase 1: invio nonce al server (challenge) e attesa risposta con HMAC
            auth_nonce = secrets.token_hex(AUTH_NONCE_LENGTH)
            try:
                # Invia il nonce all'agent
                await ws.send_json({"type": MessageType.AUTH_CHALLENGE, "nonce": auth_nonce})

                # Attendi la risposta di registrazione con timeout di 10 secondi
                raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            except asyncio.TimeoutError:
                await ws.close(code=4003, reason="Auth timeout")
                logger.warning("Agent disconnesso: auth timeout")
                return

            try:
                msg_data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.close(code=4002, reason="JSON non valido")
                return

            # Verifica autenticazione PSK: l'agent deve inviare HMAC-SHA256(psk, nonce||agent_id)
            msg_type = msg_data.get("type", "")
            psk_challenge = msg_data.get("psk_challenge", "")

            if msg_type != MessageType.REGISTER:
                await ws.close(code=4001, reason="Registrazione richiesta")
                return

            # Verifica HMAC-SHA256(psk_hex, nonce||agent_id)
            agent_id = msg_data.get("agent_id", "")
            expected_hmac = hmac_mod.new(
                config.psk_hex.encode(),
                f"{auth_nonce}{agent_id}".encode(),
                hashlib.sha256,
            ).hexdigest()

            if not hmac_mod.compare_digest(psk_challenge, expected_hmac):
                await ws.close(code=4003, reason="Autenticazione PSK fallita")
                logger.warning(f"Tentativo di connessione non autorizzato da agent_id={agent_id}")
                return

            msg = parse_message(msg_data)
            agent_info = AgentInfo(agent_id=msg.agent_id, websocket=ws)
            agent_info.hostname = msg.hostname
            agent_info.os = msg.os
            agent_info.os_version = msg.os_version
            agent_info.arch = msg.arch
            agent_info.python_version = msg.python_version
            agent_info.tags = getattr(msg, "tags", [])

            await registry.register(agent_info)

            # Audit: agent registrato
            await audit.log("agent_register", msg.agent_id,
                            subject=f"{msg.hostname} {msg.os}",
                            detail=f"os={msg.os} arch={msg.arch} python={msg.python_version}")

            # Invia conferma registrazione
            await ws.send_json({"type": "register_ack", "agent_id": msg.agent_id, "status": "ok"})

            # Recapita comandi in coda
            pending = await queue.dequeue(msg.agent_id)
            for cmd in pending:
                await ws.send_json(cmd)

            # Loop principale
            while True:
                raw = await ws.receive_text()
                # Decritta il messaggio se crittografato
                msg_data = json.loads(raw)
                logger.debug(f"WS received msg keys: {list(msg_data.keys())}, has_nonce={'nonce_b64' in msg_data}, has_ct={'ciphertext_b64' in msg_data}")
                # Se il messaggio ha nonce_b64 + ciphertext_b64, decritta
                if "nonce_b64" in msg_data and "ciphertext_b64" in msg_data:
                    try:
                        # Estrae AAD per verificare integrita' contestuale
                        aad = None
                        if "aad_b64" in msg_data:
                            aad = base64.b64decode(msg_data["aad_b64"])
                            logger.debug(f"Decrypting msg from agent: aad_b64 present, aad={aad}")
                        else:
                            logger.debug(f"Decrypting msg from agent: no aad_b64, decrypting without AAD")
                        plaintext = crypto.decrypt(
                            msg_data["nonce_b64"],
                            msg_data["ciphertext_b64"],
                            aad=aad,
                        )
                        msg_data = json.loads(plaintext)
                        logger.info(f"Decryption OK: msg_type={msg_data.get('type','?')}, keys={list(msg_data.keys())}")
                        logger.debug(f"Decrypted msg data: {json.dumps(msg_data, default=str)[:500]}")
                    except Exception as e:
                        logger.error(f"Decryption fallita: {type(e).__name__}: {e}")
                        logger.debug(f"  msg keys: {list(msg_data.keys())}")
                        logger.debug(f"  nonce_b64 len={len(msg_data.get('nonce_b64',''))}, ct_b64 len={len(msg_data.get('ciphertext_b64',''))}")
                        # Try without AAD to diagnose
                        try:
                            plaintext_no_aad = crypto.decrypt(
                                msg_data["nonce_b64"],
                                msg_data["ciphertext_b64"],
                                aad=None,
                            )
                            msg_data = json.loads(plaintext_no_aad)
                            logger.warning(f"Decryption works WITHOUT AAD - AAD mismatch! msg_type={msg_data.get('type','?')}")
                        except Exception as e2:
                            logger.error(f"Decryption without AAD also failed: {type(e2).__name__}: {e2}")
                        continue

                msg = parse_message(msg_data)

                if msg.type == MessageType.HEARTBEAT:
                    agent_info.last_heartbeat = time.time()
                    agent_info.stats = getattr(msg, "stats", {})
                    # Crittografa la risposta heartbeat_ack
                    ack_msg = {"type": MessageType.HEARTBEAT_ACK, "timestamp": int(time.time())}
                    if HEARTBEAT_ENCRYPTED and crypto:
                        encrypted_ack = crypto.encrypt_message(ack_msg, aad=make_aad(MessageType.HEARTBEAT_ACK))
                        logger.debug(f"Sending encrypted heartbeat_ack: keys={list(encrypted_ack.keys())}, nonce_len={len(encrypted_ack.get('nonce_b64',''))}, ct_len={len(encrypted_ack.get('ciphertext_b64',''))}, aad_len={len(encrypted_ack.get('aad_b64','')) if 'aad_b64' in encrypted_ack else 0}")
                        await ws.send_json(encrypted_ack)
                    else:
                        await ws.send_json(ack_msg)

                elif msg.type == MessageType.COMMAND_RESPONSE:
                    request_id = msg.request_id
                    async with registry._lock:
                        future = registry.pending_responses.pop(request_id, None)
                    if future and not future.done():
                        future.set_result(msg)

                elif msg.type in (MessageType.FILE_RESPONSE, MessageType.SCREENSHOT_RESPONSE, MessageType.INFO_RESPONSE):
                    request_id = msg.request_id
                    logger.info(f"Received {msg.type} for request {request_id}: path={getattr(msg, 'path', 'N/A')}, size={getattr(msg, 'size', 'N/A')}, keys={list(msg.__dict__.keys()) if hasattr(msg, '__dict__') else '?'}")
                    async with registry._lock:
                        future = registry.pending_responses.pop(request_id, None)
                    if future and not future.done():
                        logger.info(f"Resolved future for {request_id}")
                        future.set_result(msg)
                    else:
                        logger.warning(f"No pending future for {msg.type} request_id={request_id}, future={future}, done={future.done() if future else 'N/A'}")

                elif msg.type == MessageType.STREAM_FRAME:
                    # Frame continuo da streaming — aggiorna solo il buffer (no future)
                    if agent_info:
                        agent_info.latest_frame = {
                            "content_b64": getattr(msg, "content_b64", ""),
                            "width": getattr(msg, "width", 0),
                            "height": getattr(msg, "height", 0),
                            "format": getattr(msg, "format", "jpeg"),
                            "frame_index": getattr(msg, "frame_index", 0),
                            "timestamp": getattr(msg, "timestamp", int(time.time())),
                        }

                elif msg.type in (MessageType.INPUT_RESPONSE, MessageType.STREAM_RESPONSE):
                    request_id = msg.request_id
                    async with registry._lock:
                        future = registry.pending_responses.pop(request_id, None)
                    if future and not future.done():
                        future.set_result(msg)
                    if msg.type == MessageType.STREAM_RESPONSE and agent_info:
                        agent_info.streaming = getattr(msg, "streaming", False)

                else:
                    logger.debug(f"Messaggio non gestito da {msg.agent_id}: {msg.type}")

        except WebSocketDisconnect:
            if agent_info:
                await audit.log("agent_disconnect", agent_info.agent_id)
                registry.unregister(agent_info.agent_id)
        except Exception as e:
            logger.error(f"Errore agent WebSocket: {e}")
            if agent_info:
                await audit.log("agent_error", agent_info.agent_id, detail=str(e)[:200])
                registry.unregister(agent_info.agent_id)

    # ── REST API: Hermes ─────────────────────────────────────────────────

    @app.get("/api/agents")
    async def list_agents(user: dict = Depends(verify_token)):
        """Elenca agent connessi."""
        agents = registry.list_all()
        return {"agents": [a.to_dict() for a in agents]}

    @app.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: str, user: dict = Depends(verify_token)):
        """Info su un agent specifico."""
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        return agent.to_dict()

    @app.post("/api/agents/{agent_id}/command")
    async def send_command(agent_id: str, cmd: CommandRequest, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Invia un comando a un agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("command", agent_id, subject=cmd.command,
                        detail=f"args={cmd.args} timeout={cmd.timeout} shell={cmd.shell}",
                        source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")

        if not agent.is_online:
            # Metti in coda per quando ritornera' online
            await queue.enqueue(agent_id, cmd.model_dump())
            return {"status": "queued", "agent_id": agent_id}

        request_id = f"req-{time.time_ns()}"
        message = {
            "type": MessageType.COMMAND,
            "request_id": request_id,
            "command": cmd.command,
            "args": cmd.args,
            "timeout": cmd.timeout,
            "interactive": cmd.interactive,
            "shell": cmd.shell,
        }

        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            # Crittografa il messaggio prima di inviarlo
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.COMMAND, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=cmd.timeout)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout comando su {agent_id}")

    @app.post("/api/agents/{agent_id}/file/download")
    async def download_file(agent_id: str, req: FileDownloadRequest, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Richiedi download di un file dall'agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("file_download", agent_id, subject=req.path, source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            raise HTTPException(503, f"Agent {agent_id} offline")

        request_id = f"req-{time.time_ns()}"
        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            message = {
                "type": MessageType.FILE_DOWNLOAD,
                "request_id": request_id,
                "path": req.path,
            }
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.FILE_DOWNLOAD, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=60)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout download file da {agent_id}")

    @app.post("/api/agents/{agent_id}/file/upload")
    async def upload_file(agent_id: str, req: FileUploadRequest, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Upload di un file verso l'agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("file_upload", agent_id, subject=req.path,
                        detail=f"overwrite={req.overwrite} size={len(req.content_b64) if req.content_b64 else 0}",
                        source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            raise HTTPException(503, f"Agent {agent_id} offline")

        request_id = f"req-{time.time_ns()}"
        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            message = {
                "type": MessageType.FILE_UPLOAD,
                "request_id": request_id,
                "path": req.path,
                "content_b64": req.content_b64,
                "overwrite": req.overwrite,
            }
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.FILE_UPLOAD, request_id))
                logger.debug(f"Sending FILE_UPLOAD encrypted to {agent_id}")
                await agent.websocket.send_json(encrypted)
            else:
                logger.debug(f"Sending FILE_UPLOAD plaintext to {agent_id}: keys={list(message.keys())}, content_b64_len={len(message.get('content_b64',''))}")
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=60)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout upload file su {agent_id}")

    @app.post("/api/agents/{agent_id}/screenshot")
    async def take_screenshot(agent_id: str, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Richiedi screenshot dall'agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("screenshot", agent_id, source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            raise HTTPException(503, f"Agent {agent_id} offline")

        request_id = f"req-{time.time_ns()}"
        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            message = {
                "type": MessageType.SCREENSHOT,
                "request_id": request_id,
            }
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.SCREENSHOT, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=30)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout screenshot su {agent_id}")

    @app.post("/api/agents/{agent_id}/input")
    async def send_input(agent_id: str, req: InputEventRequest, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Invia un evento input (mouse/tastiera) all'agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("input", agent_id, subject=req.event_type, source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            raise HTTPException(409, f"Agent {agent_id} offline")

        request_id = f"req-{time.time_ns()}"
        message = {
            "type": MessageType.INPUT_EVENT,
            "request_id": request_id,
            "event_type": req.event_type,
            "x": req.x,
            "y": req.y,
            "button": req.button,
            "scroll_delta": req.scroll_delta,
            "key": req.key,
            "keys": req.keys,
            "text": req.text,
            "duration": req.duration,
        }

        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.INPUT_EVENT, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=10)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout input su {agent_id}")
        except Exception as e:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(502, f"Errore invio input: {e}")

    @app.post("/api/agents/{agent_id}/stream/start")
    async def start_stream(agent_id: str, req: StreamStartRequest, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Avvia lo streaming remoto sull'agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("stream_start", agent_id, source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            raise HTTPException(409, f"Agent {agent_id} offline")

        request_id = f"req-{time.time_ns()}"
        message = {
            "type": MessageType.STREAM_START,
            "request_id": request_id,
            "fps": req.fps,
            "quality": req.quality,
            "scale_factor": req.scale_factor,
        }

        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.STREAM_START, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=10)
            agent.streaming = getattr(response, "streaming", False)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout start streaming su {agent_id}")
        except Exception as e:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(502, f"Errore start streaming: {e}")

    @app.post("/api/agents/{agent_id}/stream/stop")
    async def stop_stream(agent_id: str, req: StreamStopRequest = None, user: dict = Depends(verify_role("admin")), request: Request = None):
        """Ferma lo streaming remoto sull'agent."""
        source_ip = request.client.host if request and request.client else ""
        await audit.log("stream_stop", agent_id, source_ip=source_ip)
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            raise HTTPException(409, f"Agent {agent_id} offline")

        request_id = f"req-{time.time_ns()}"
        message = {
            "type": MessageType.STREAM_STOP,
            "request_id": request_id,
        }

        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.STREAM_STOP, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=10)
            agent.streaming = getattr(response, "streaming", False)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout stop streaming su {agent_id}")
        except Exception as e:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(502, f"Errore stop streaming: {e}")

    @app.get("/api/agents/{agent_id}/stream.mjpeg")
    async def stream_mjpeg(agent_id: str, user: dict = Depends(verify_token), request: Request = None):
        """Streaming MJPEG multipart dei frame catturati dall'agent.

        Il browser mostra questo endpoint direttamente in un <img>, senza JS.
        Blocca finché ci sono frame, poi termina (auto-riconnessione del browser).
        """
        from fastapi.responses import StreamingResponse
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")

        async def frame_generator():
            last_index = -1
            while True:
                frame = agent.latest_frame
                if frame and frame.get("frame_index", -1) > last_index:
                    last_index = frame["frame_index"]
                    content = base64.b64decode(frame.get("content_b64", ""))
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(content)).encode() + b"\r\n\r\n"
                        + content + b"\r\n"
                    )
                else:
                    await asyncio.sleep(0.1)

        return StreamingResponse(
            frame_generator(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/agents/{agent_id}/stream/meta")
    async def stream_meta(agent_id: str, user: dict = Depends(verify_token)):
        """Ritorna metadati dell'ultimo frame streaming (dimensioni, stato)."""
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        f = agent.latest_frame
        return {
            "streaming": agent.streaming,
            "width": f.get("width", 0) if f else 0,
            "height": f.get("height", 0) if f else 0,
            "frame_index": f.get("frame_index", -1) if f else -1,
            "timestamp": f.get("timestamp", 0) if f else 0,
        }

    @app.get("/api/agents/{agent_id}/info")
    async def get_info(agent_id: str, user: dict = Depends(verify_token)):
        """Richiedi info di sistema dall'agent."""
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(404, f"Agent {agent_id} non trovato")
        if not agent.is_online:
            return agent.to_dict()

        request_id = f"req-{time.time_ns()}"
        future = asyncio.get_running_loop().create_future()
        async with registry._lock:
            registry.pending_responses[request_id] = future

        try:
            message = {
                "type": MessageType.INFO,
                "request_id": request_id,
            }
            if HEARTBEAT_ENCRYPTED:
                encrypted = crypto.encrypt_message(message, aad=make_aad(MessageType.INFO, request_id))
                await agent.websocket.send_json(encrypted)
            else:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=15)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            return agent.to_dict()

    @app.get("/api/audit")
    async def query_audit(agent_id: str = "", action: str = "",
                          since: float = 0, limit: int = 100,
                          user: dict = Depends(verify_token)):
        """Interroga il log di audit. Richiede autenticazione JWT."""
        records = await audit.query(agent_id=agent_id, action=action, since=since, limit=min(limit, 500))
        return {"records": records, "count": len(records)}

    # ── OIDC Login ───────────────────────────────────────────────────────────

    @app.get("/api/auth/oidc/start")
    async def oidc_start(redirect_after: str = "/"):
        """Avvia Authorization Code + PKCE e redirige ad Authentik."""
        oidc_disabled()
        assert oidc is not None
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        oidc_sessions[state] = {
            "verifier": code_verifier,
            "redirect_after": redirect_after,
        }
        try:
            authorization_url = oidc.authorization_url(state, code_verifier)
        except Exception as exc:
            oidc_sessions.pop(state, None)
            logger.exception("OIDC discovery fallita")
            return oidc_error_redirect(redirect_after, f"discovery_failed: {exc}")
        return RedirectResponse(url=authorization_url, status_code=302)

    @app.get("/api/auth/oidc/callback")
    async def oidc_callback(code: str = "", state: str = ""):
        """Scambia il code Authentik e crea il JWT HS256 interno del relay."""
        oidc_disabled()
        assert oidc is not None
        session = oidc_sessions.pop(state, None)
        redirect_after = session["redirect_after"] if session else "/"
        if not session:
            return oidc_error_redirect(redirect_after, "invalid_state")
        if not code:
            return oidc_error_redirect(redirect_after, "missing_code")

        try:
            tokens = oidc.exchange_code(code, session["verifier"])
            # Authentik normalmente restituisce entrambi. Il validator verifica
            # la firma RS256, issuer, audience e scadenza tramite le JWKS.
            provider_token = tokens.get("access_token") or tokens.get("id_token")
            if not provider_token:
                raise ValueError("token OIDC mancante nella risposta")
            claims = oidc.build_caduceo_claims(provider_token)
            subject = claims.get("sub") or claims.get("username")
            if not subject:
                raise ValueError("subject OIDC mancante")
            # Estrae username/email leggibili: prima da userinfo (affidabile),
            # poi dall'id_token.
            id_claims = {}
            try:
                id_claims = oidc.get_userinfo(tokens.get("access_token") or provider_token)
            except Exception:
                logger.warning("userinfo non disponibile, provo id_token", exc_info=True)
                if tokens.get("id_token"):
                    try:
                        id_claims = oidc.decode_id_token(tokens["id_token"])
                    except Exception:
                        logger.warning("id_token firma non validata, decodifica loose", exc_info=True)
                        id_claims = oidc.decode_id_token_loose(tokens["id_token"])
            username = (
                id_claims.get("preferred_username")
                or id_claims.get("name")
                or id_claims.get("username")
                or claims.get("sub")
                or subject
            )
            email = id_claims.get("email") or claims.get("email", "")
            internal_token = jwt.encode(
                {
                    "sub": subject,
                    "role": "admin",
                    "username": username,
                    "email": email,
                    "exp": int(time.time()) + JWT_EXPIRY,
                },
                config.jwt_secret,
                algorithm="HS256",
            )
            separator = "&" if "?" in redirect_after else "?"
            return RedirectResponse(
                url=f"{redirect_after}{separator}token={quote(internal_token)}",
                status_code=302,
            )
        except Exception as exc:
            logger.exception("Login OIDC fallito")
            return oidc_error_redirect(redirect_after, f"authentication_failed: {exc}")

    # ── Assistente IA: impostazioni persistenti (server-side) ────────────
    # La chiave API resta sul server, non nella cache del browser. Il client
    # non riceve mai la chiave: invia richieste LLM al relay che le inoltra.

    @app.get("/api/ai/settings")
    async def ai_get_settings(user: dict = Depends(verify_role("admin"))):
        """Ritorna le impostazioni dell'Assistente IA (senza chiave API)."""
        return {
            "endpoint": "https://openrouter.ai/api/v1",
            "model": settings.get("model", "openai/gpt-5.6-luna"),
            "api_key_configured": bool(settings.get("api_key")),
        }

    @app.post("/api/ai/settings")
    async def ai_set_settings(payload: dict, user: dict = Depends(verify_role("admin"))):
        """Salva modello e (opzionalmente) API key sul server."""
        if "model" in payload:
            settings.set("model", payload["model"])
        if "api_key" in payload and isinstance(payload["api_key"], str) and payload["api_key"].strip():
            key = payload["api_key"].strip()
            # Ignora chiavi placeholder o non-ASCII (protezione contro errori client)
            if "••••" in key or not key.isascii() or len(key) < 10:
                return {"status": "ok", "api_key_configured": bool(settings.get("api_key"))}
            settings.set("api_key", key)
        return {"status": "ok", "api_key_configured": bool(settings.get("api_key"))}

    @app.post("/api/ai/chat")
    async def ai_chat(payload: dict, user: dict = Depends(verify_role("admin"))):
        """Proxy LLM: il relay chiama OpenRouter con la chiave salvata sul
        server (mai nel browser). Il client invia solo messages + tools."""
        api_key = settings.get("api_key")
        if not api_key:
            raise HTTPException(400, "API Key OpenRouter non configurata.")
        model = payload.get("model") or settings.get("model", "openai/gpt-5.6-luna")

        body = {
            "model": model,
            "messages": payload.get("messages", []),
            "temperature": payload.get("temperature", 0.2),
            "tools": payload.get("tools"),
        }
        # Rimuovi chiavi None per evitare errori
        body = {k: v for k, v in body.items() if v is not None}

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            logger.error(f"OpenRouter HTTP {e.code}: {detail}")
            raise HTTPException(502, f"OpenRouter error {e.code}: {detail}")
        except Exception as e:
            logger.error(f"OpenRouter request failed: {e}", exc_info=True)
            raise HTTPException(502, f"OpenRouter request failed: {e}")
        return data

    @app.get("/api/health")
    async def health():
        """Health check (no auth required)."""
        return {
            "status": "ok",
            "version": PROTOCOL_VERSION,
            "agents_online": len(registry.list_online()),
            "agents_total": len(registry.agents),
        }

    # ── Download client (protetto da JWT) ────────────────────────────────
    # Serve il pacchetto client (agent) scaricabile dalla dashboard.
    # Usa l'autenticazione JWT dell'operatore (non la download_key pubblica).

    CLIENT_ZIP = "caduceo-local.zip"

    @app.get("/api/client/download")
    async def client_download(user: dict = Depends(verify_token)):
        """Scarica il pacchetto client da installare su altri PC. Richiede JWT."""
        path = DEPLOY_DIR / CLIENT_ZIP
        if not path.exists():
            raise HTTPException(404, f"Pacchetto client non trovato: {CLIENT_ZIP}")
        return FileResponse(path, filename=CLIENT_ZIP, media_type="application/zip")

    # ── Token: sola generazione da localhost ────────────────────────────

    @app.post("/api/token")
    async def create_token(req: TokenRequest = TokenRequest(), request: Request = None):
        """
        Genera un nuovo JWT token.
        Accessibile SOLO da localhost per bootstrap.
        In produzione, usare il CLI tool o la credentials file.
        """
        # Valida il role richiesto
        try:
            req.validate_role()
        except ValueError as e:
            raise HTTPException(400, str(e))

        # Verifica che la richiesta arrivi da localhost
        if request:
            client_host = request.client.host if request.client else "unknown"
            if client_host not in ("127.0.0.1", "::1", "localhost"):
                raise HTTPException(403, "Token generation consentita solo da localhost. Usa il CLI tool per generare token remoti.")

        if not config.jwt_secret:
            raise HTTPException(500, "JWT secret non configurato")

        token = jwt.encode(
            {"sub": req.subject, "role": req.role, "exp": int(time.time()) + JWT_EXPIRY},
            config.jwt_secret,
            algorithm="HS256"
        )
        return {"token": token, "expires_in": JWT_EXPIRY}

    return app


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Caduceo Relay Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8443, help="Bind port (default: 8443)")
    parser.add_argument("--config", default="~/.caduceo/relay.json", help="Config file path")
    parser.add_argument("--jwt-secret", default=None, help="JWT secret (overrides config)")
    parser.add_argument("--psk", default=None, help="Pre-shared key hex (overrides config)")

    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    config = RelayConfig.from_file(config_path)

    if args.host != "0.0.0.0":
        config.host = args.host
    if args.port != 8443:
        config.port = args.port
    if args.jwt_secret:
        config.jwt_secret = args.jwt_secret
    if args.psk:
        config.psk_hex = args.psk
        config.crypto = Crypto.from_hex(args.psk)

    # Salva config se e' nuova
    if not config_path.exists():
        config.config_path = config_path
        config.save()
        config.save_credentials()

    # Non stampare la PSK intera! Solo ultimi 8 caratteri per verifica
    psk_tail = config.psk_hex[-8:] if len(config.psk_hex) > 8 else "****"
    jwt_tail = config.jwt_secret[-8:] if len(config.jwt_secret) > 8 else "****"

    logger.info(f"Avvio Caduceo Relay su {config.host}:{config.port}")
    logger.info(f"Config: {config_path}")

    print(f"\n{'='*60}")
    print(f"  Caduceo Relay Server v{PROTOCOL_VERSION}")
    print(f"{'='*60}")
    print(f"  Host:       {config.host}:{config.port}")
    print(f"  Config:     {config_path}")
    print(f"  PSK:        ...{psk_tail}")
    print(f"  JWT Secret: ...{jwt_tail}")
    print(f"  Agent WS:   ws://{config.host}:{config.port}{WS_AGENT_PATH}")
    print(f"  REST API:   http://{config.host}:{config.port}/api")
    print(f"{'='*60}\n")

    app = create_app(config)

    # Explicit WebSocket + server config for reliable WS handshakes.
    # - ws="websockets": force the websockets library (not wsproto) for WS upgrades
    # - ws_max_size: match protocol WS_MAX_SIZE (10 MB) so large messages don't get rejected
    # - ws_ping_interval/ws_ping_timeout: keep idle WS connections alive through proxies/NAT
    # - timeout_keep_alive=0: disable HTTP keep-alive timeout that can close idle TCP
    #   connections before the WS upgrade completes on slow networks
    # - proxy_headers=True: trust X-Forwarded-* headers (Tailscale, reverse proxies)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        ws="websockets",
        ws_max_size=WS_MAX_SIZE,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_keep_alive=0,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()