"""Caduceo Relay - Server principale con FastAPI + WebSocket."""

import asyncio
import json
import logging
import time
from pathlib import Path

import jwt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from caduceo_common.constants import (
    WS_AGENT_PATH,
    HEARTBEAT_TIMEOUT,
    QUEUE_TTL_DEFAULT,
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
        self.jwt_secret = "change-me-in-production"
        self.psk_hex = ""  # generata se vuota
        self.queue_ttl = QUEUE_TTL_DEFAULT
        self.db_path = Path.home() / ".caduceo" / "relay.db"

        # Inizializza crittografia
        if not self.psk_hex:
            self.psk_hex = Crypto.generate_key_hex(Crypto())
        self.crypto = Crypto.from_hex(self.psk_hex)

    @classmethod
    def from_file(cls, path: Path) -> "RelayConfig":
        """Carica configurazione da file JSON."""
        config = cls()
        if path.exists():
            data = json.loads(path.read_text())
            for k, v in data.items():
                if hasattr(config, k):
                    setattr(config, k, v)
            if config.psk_hex:
                config.crypto = Crypto.from_hex(config.psk_hex)
        return config


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

    def register(self, agent: AgentInfo):
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


# ── Coda Comandi Offline ────────────────────────────────────────────────────

class CommandQueue:
    """Coda comandi per agent offline. Persiste su SQLite."""
    def __init__(self, db_path: Path, ttl: int = QUEUE_TTL_DEFAULT):
        self.db_path = db_path
        self.ttl = ttl
        self._ensure_db()

    def _ensure_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def enqueue(self, agent_id: str, command: dict):
        """Mette in coda un comando per agent offline."""
        # TODO: Implementare persistenza SQLite
        logger.info(f"Comando in coda per {agent_id}: {command.get('type', 'unknown')}")

    async def dequeue(self, agent_id: str) -> list[dict]:
        """Recupera comandi in coda per agent appena riconnesso."""
        # TODO: Implementare
        return []

    async def cleanup(self):
        """Rimuove comandi scaduti."""
        # TODO: Implementare
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    registry = AgentRegistry()
    queue = CommandQueue(config.db_path, config.queue_ttl)

    # ── JWT Auth ─────────────────────────────────────────────────────────

    def verify_token(authorization: str = "") -> dict:
        """Verifica JWT token per API Hermes."""
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Token mancante")
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
        """Endpoint WebSocket per agent remoti."""
        await ws.accept()
        agent_info = None

        try:
            # Attendi registrazione
            raw = await ws.receive_text()
            msg = parse_message(json.loads(raw))

            if msg.type != MessageType.REGISTER:
                await ws.close(code=4001, reason="Registrazione richiesta")
                return

            agent_info = AgentInfo(agent_id=msg.agent_id, websocket=ws)
            agent_info.hostname = msg.hostname
            agent_info.os = msg.os
            agent_info.os_version = msg.os_version
            agent_info.arch = msg.arch
            agent_info.python_version = msg.python_version
            agent_info.tags = getattr(msg, "tags", [])

            registry.register(agent_info)

            # Invia conferma registrazione
            await ws.send_json({"type": "register_ack", "agent_id": msg.agent_id, "status": "ok"})

            # Recapita comandi in coda
            pending = await queue.dequeue(msg.agent_id)
            for cmd in pending:
                await ws.send_json(cmd)

            # Loop principale
            while True:
                raw = await ws.receive_text()
                msg = parse_message(json.loads(raw))

                if msg.type == MessageType.HEARTBEAT:
                    agent_info.last_heartbeat = time.time()
                    agent_info.stats = getattr(msg, "stats", {})
                    await ws.send_json({"type": "heartbeat_ack", "timestamp": int(time.time())})

                elif msg.type == MessageType.COMMAND_RESPONSE:
                    # Risolve la Future corrispondente
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
            # Metti in coda
            await queue.enqueue(agent_id, cmd.model_dump())
            return {"status": "queued", "agent_id": agent_id}

        # Invia comando e attendi risposta
        message = {
            "type": MessageType.COMMAND,
            "request_id": f"req-{asyncio.get_event_loop().time():.0f}",
            "command": cmd.command,
            "args": cmd.args,
            "timeout": cmd.timeout,
            "interactive": cmd.interactive,
            "shell": cmd.shell,
        }
        message["request_id"] = f"req-{time.time_ns()}"

        future = asyncio.get_event_loop().create_future()
        registry.pending_responses[message["request_id"]] = future

        try:
            await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=cmd.timeout)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            del registry.pending_responses[message["request_id"]]
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
        future = asyncio.get_event_loop().create_future()
        registry.pending_responses[request_id] = future

        try:
            await agent.websocket.send_json({
                "type": MessageType.FILE_DOWNLOAD,
                "request_id": request_id,
                "path": req.path,
            })
            response = await asyncio.wait_for(future, timeout=60)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            del registry.pending_responses[request_id]
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
        future = asyncio.get_event_loop().create_future()
        registry.pending_responses[request_id] = future

        try:
            await agent.websocket.send_json({
                "type": MessageType.FILE_UPLOAD,
                "request_id": request_id,
                "path": req.path,
                "content_b64": req.content_b64,
                "overwrite": req.overwrite,
            })
            response = await asyncio.wait_for(future, timeout=60)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            del registry.pending_responses[request_id]
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
        future = asyncio.get_event_loop().create_future()
        registry.pending_responses[request_id] = future

        try:
            await agent.websocket.send_json({
                "type": MessageType.SCREENSHOT,
                "request_id": request_id,
            })
            response = await asyncio.wait_for(future, timeout=30)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            del registry.pending_responses[request_id]
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
        future = asyncio.get_event_loop().create_future()
        registry.pending_responses[request_id] = future

        try:
            await agent.websocket.send_json({
                "type": MessageType.INFO,
                "request_id": request_id,
            })
            response = await asyncio.wait_for(future, timeout=15)
            return response.to_dict() if hasattr(response, "to_dict") else response.__dict__
        except asyncio.TimeoutError:
            del registry.pending_responses[request_id]
            return agent.to_dict()

    @app.get("/api/health")
    async def health():
        """Health check."""
        return {
            "status": "ok",
            "version": PROTOCOL_VERSION,
            "agents_online": len(registry.list_online()),
            "agents_total": len(registry.agents),
        }

    @app.post("/api/token")
    async def create_token(user: dict = Depends(verify_token)):
        """Genera un nuovo JWT token per API access."""
        token = jwt.encode({"sub": "hermes", "role": "admin"}, config.jwt_secret, algorithm="HS256")
        return {"token": token}

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

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.jwt_secret:
        config.jwt_secret = args.jwt_secret
    if args.psk:
        config.psk_hex = args.psk
        config.crypto = Crypto.from_hex(args.psk)

    logger.info(f"Avvio Caduceo Relay su {config.host}:{config.port}")
    logger.info(f"PSK: {config.psk_hex[:8]}...")
    logger.info(f"Agent endpoint: ws://{config.host}:{config.port}{WS_AGENT_PATH}")

    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()