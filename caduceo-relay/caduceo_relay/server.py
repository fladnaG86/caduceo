"""Caduceo Relay - Server principale con FastAPI + WebSocket."""

import asyncio
import json
import logging
import os
import stat
import time
from pathlib import Path

import jwt
import aiosqlite
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from caduceo_common.constants import (
    WS_AGENT_PATH,
    HEARTBEAT_TIMEOUT,
    QUEUE_TTL_DEFAULT,
    QUEUE_MAX_SIZE,
    PROTOCOL_VERSION,
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
        self.queue_ttl = QUEUE_TTL_DEFAULT
        self.db_path = Path.home() / ".caduceo" / "relay.db"
        self.config_path = Path.home() / ".caduceo" / "relay.json"
        self.allowed_origins: list[str] = []

        # Genera PSK e JWT secret se non presenti
        if not self.psk_hex:
            self.psk_hex = Crypto().generate_key_hex()
        if not self.jwt_secret:
            import secrets
            self.jwt_secret = secrets.token_hex(32)
        self.crypto = Crypto.from_hex(self.psk_hex)

    def save(self):
        """Salva la configurazione su file JSON con permessi restrittivi."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "host": self.host,
            "port": self.port,
            "jwt_secret": self.jwt_secret,
            "psk_hex": self.psk_hex,
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
            import secrets
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
            "note": "JWT token can be generated from jwt_secret. For production use WSS and HTTPS on maulanhermes.uk",
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

class TokenRequest(BaseModel):
    subject: str = "hermes"
    role: str = "admin"


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
        """Mette in coda un comando per agent offline."""
        db = await self._get_db()
        now = time.time()
        expires_at = now + self.ttl
        command_json = json.dumps(command)

        # Limita la coda per agent (con transazione)
        async with db.execute(
            "SELECT COUNT(*) FROM command_queue WHERE agent_id = ?",
            (agent_id,)
        ) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0
        if count >= QUEUE_MAX_SIZE:
            logger.warning(f"Coda piena per {agent_id} ({count} comandi)")
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
    crypto = config.crypto

    # Inizializza DB all'avvio
    @app.on_event("startup")
    async def startup():
        await queue._get_db()
        logger.info("CommandQueue DB inizializzato")

    @app.on_event("shutdown")
    async def shutdown():
        await queue.close()

    # ── JWT Auth ─────────────────────────────────────────────────────────

    def verify_token(authorization: str = Header(default="", alias="Authorization")) -> dict:
        """Verifica JWT token per API Hermes."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Token mancante o formato errato. Usa: Bearer <token>")
        token = authorization[7:]
        try:
            payload = jwt.decode(token, config.jwt_secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Token scaduto")
        except jwt.InvalidTokenError:
            raise HTTPException(401, "Token non valido")

    # ── WebSocket: Agent ─────────────────────────────────────────────────

    @app.websocket(WS_AGENT_PATH)
    async def agent_endpoint(ws: WebSocket):
        """Endpoint WebSocket per agent remoti. Richiede autenticazione PSK."""
        await ws.accept()
        agent_info = None

        try:
            # Fase 1: attesa auth con timeout di 10 secondi
            try:
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

            # Verifica autenticazione PSK: il primo messaggio deve essere register con psk_challenge
            msg_type = msg_data.get("type", "")
            psk_challenge = msg_data.get("psk_challenge", "")

            if msg_type != MessageType.REGISTER:
                await ws.close(code=4001, reason="Registrazione richiesta")
                return

            # Verifica PSK: l'agent deve dimostrare di conoscere la chiave
            # Invia un nonce e verifica che l'agent lo crittografi correttamente
            # Metodo semplificato: l'agent invia il PSK hash come challenge
            import hashlib
            expected_challenge = hashlib.sha256(
                (config.psk_hex + msg_data.get("agent_id", "")).encode()
            ).hexdigest()

            if psk_challenge != expected_challenge:
                await ws.close(code=4003, reason="Autenticazione PSK fallita")
                logger.warning(f"Tentativo di connessione non autorizzato da agent_id={msg_data.get('agent_id', '?')}")
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
                # Se il messaggio ha nonce_b64 + ciphertext_b64, decritta
                if "nonce_b64" in msg_data and "ciphertext_b64" in msg_data:
                    try:
                        plaintext = crypto.decrypt(
                            msg_data["nonce_b64"],
                            msg_data["ciphertext_b64"],
                        )
                        msg_data = json.loads(plaintext)
                    except Exception as e:
                        logger.error(f"Decryption fallita: {e}")
                        continue

                msg = parse_message(msg_data)

                if msg.type == MessageType.HEARTBEAT:
                    agent_info.last_heartbeat = time.time()
                    agent_info.stats = getattr(msg, "stats", {})
                    await ws.send_json({"type": "heartbeat_ack", "timestamp": int(time.time())})

                elif msg.type == MessageType.COMMAND_RESPONSE:
                    request_id = msg.request_id
                    if request_id in registry.pending_responses:
                        registry.pending_responses[request_id].set_result(msg)
                        del registry.pending_responses[request_id]

                elif msg.type in (MessageType.FILE_RESPONSE, MessageType.SCREENSHOT_RESPONSE, MessageType.INFO_RESPONSE):
                    request_id = msg.request_id
                    if request_id in registry.pending_responses:
                        registry.pending_responses[request_id].set_result(msg)
                        del registry.pending_responses[request_id]

                else:
                    logger.debug(f"Messaggio non gestito da {msg.agent_id}: {msg.type}")

        except WebSocketDisconnect:
            if agent_info:
                registry.unregister(agent_info.agent_id)
        except Exception as e:
            logger.error(f"Errore agent WebSocket: {e}")
            if agent_info:
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
    async def send_command(agent_id: str, cmd: CommandRequest, user: dict = Depends(verify_token)):
        """Invia un comando a un agent."""
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
            encrypted = crypto.encrypt_message(message)
            await agent.websocket.send_json(encrypted)
            response = await asyncio.wait_for(future, timeout=cmd.timeout)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout comando su {agent_id}")

    @app.post("/api/agents/{agent_id}/file/download")
    async def download_file(agent_id: str, req: FileDownloadRequest, user: dict = Depends(verify_token)):
        """Richiedi download di un file dall'agent."""
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
            encrypted = crypto.encrypt_message(message)
            await agent.websocket.send_json(encrypted)
            response = await asyncio.wait_for(future, timeout=60)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout download file da {agent_id}")

    @app.post("/api/agents/{agent_id}/file/upload")
    async def upload_file(agent_id: str, req: FileUploadRequest, user: dict = Depends(verify_token)):
        """Upload di un file verso l'agent."""
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
            encrypted = crypto.encrypt_message(message)
            await agent.websocket.send_json(encrypted)
            response = await asyncio.wait_for(future, timeout=60)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout upload file su {agent_id}")

    @app.post("/api/agents/{agent_id}/screenshot")
    async def take_screenshot(agent_id: str, user: dict = Depends(verify_token)):
        """Richiedi screenshot dall'agent."""
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
            encrypted = crypto.encrypt_message(message)
            await agent.websocket.send_json(encrypted)
            response = await asyncio.wait_for(future, timeout=30)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            raise HTTPException(504, f"Timeout screenshot su {agent_id}")

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
            encrypted = crypto.encrypt_message(message)
            await agent.websocket.send_json(encrypted)
            response = await asyncio.wait_for(future, timeout=15)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            async with registry._lock:
                registry.pending_responses.pop(request_id, None)
            return agent.to_dict()

    @app.get("/api/health")
    async def health():
        """Health check (no auth required)."""
        return {
            "status": "ok",
            "version": PROTOCOL_VERSION,
            "agents_online": len(registry.list_online()),
            "agents_total": len(registry.agents),
        }

    # ── Token: sola generazione da localhost ────────────────────────────

    @app.post("/api/token")
    async def create_token(req: TokenRequest = TokenRequest(), request: Request = None):
        """
        Genera un nuovo JWT token.
        Accessibile SOLO da localhost per bootstrap.
        In produzione, usare il CLI tool o la credentials file.
        """
        # Verifica che la richiesta arrivi da localhost
        if request:
            client_host = request.client.host if request.client else "unknown"
            if client_host not in ("127.0.0.1", "::1", "localhost"):
                raise HTTPException(403, "Token generation consentita solo da localhost. Usa il CLI tool per generare token remoti.")

        if not config.jwt_secret:
            raise HTTPException(500, "JWT secret non configurato")

        token = jwt.encode(
            {"sub": req.subject, "role": req.role, "exp": int(time.time()) + 86400 * 30},
            config.jwt_secret,
            algorithm="HS256"
        )
        return {"token": token, "expires_in": 86400 * 30}

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
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()