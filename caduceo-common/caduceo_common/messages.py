"""Caduceo Common - Modelli messaggi."""

import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .constants import MessageType, PROTOCOL_VERSION
from .utils import generate_request_id, detect_os


@dataclass
class Message:
    """Messaggio base del protocollo Caduceo."""
    type: str
    version: str = PROTOCOL_VERSION
    timestamp: int = 0
    request_id: str = ""

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(datetime.now(timezone.utc).timestamp())
        if not self.request_id:
            self.request_id = generate_request_id()

    def to_dict(self) -> dict:
        """Serializza il messaggio. NON filtra valori falsy (0, False) che sono validi."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RegisterMessage(Message):
    """Messaggio di registrazione agent."""
    type: str = MessageType.REGISTER
    agent_id: str = ""
    hostname: str = ""
    os: str = ""
    os_version: str = ""
    arch: str = ""
    python_version: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        if not self.os:
            self.os = detect_os()
        if not self.hostname:
            self.hostname = platform.node()
        if not self.arch:
            self.arch = platform.machine()


@dataclass
class HeartbeatMessage(Message):
    """Heartbeat periodico dell'agent."""
    type: str = MessageType.HEARTBEAT
    agent_id: str = ""
    stats: dict[str, float] = field(default_factory=dict)


@dataclass
class CommandMessage(Message):
    """Comando inviato dal relay all'agent."""
    type: str = MessageType.COMMAND
    command: str = ""
    args: list[str] = field(default_factory=list)
    timeout: int = 30
    interactive: bool = False
    shell: str = ""  # auto-detect se vuoto


@dataclass
class CommandResponse(Message):
    """Risposta dell'agent a un comando."""
    type: str = MessageType.COMMAND_RESPONSE
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


@dataclass
class FileDownloadMessage(Message):
    """Richiesta download file dall'agent."""
    type: str = MessageType.FILE_DOWNLOAD
    path: str = ""


@dataclass
class FileUploadMessage(Message):
    """Richiesta upload file verso l'agent."""
    type: str = MessageType.FILE_UPLOAD
    path: str = ""
    content_b64: str = ""
    overwrite: bool = False


@dataclass
class FileResponse(Message):
    """Risposta transfer file dall'agent."""
    type: str = MessageType.FILE_RESPONSE
    path: str = ""
    size: int = 0
    content_b64: str = ""
    checksum_sha256: str = ""


@dataclass
class ScreenshotMessage(Message):
    """Richiesta screenshot dall'agent."""
    type: str = MessageType.SCREENSHOT


@dataclass
class ScreenshotResponse(Message):
    """Risposta screenshot dall'agent."""
    type: str = MessageType.SCREENSHOT_RESPONSE
    content_b64: str = ""
    width: int = 0
    height: int = 0
    format: str = "png"


@dataclass
class InfoRequestMessage(Message):
    """Richiesta info di sistema dall'agent."""
    type: str = MessageType.INFO


@dataclass
class InfoResponse(Message):
    """Risposta info di sistema dall'agent."""
    type: str = MessageType.INFO_RESPONSE
    hostname: str = ""
    os: str = ""
    os_version: str = ""
    arch: str = ""
    cpu_count: int = 0
    memory_total: int = 0
    memory_percent: float = 0.0
    disk_total: int = 0
    disk_percent: float = 0.0
    uptime_seconds: int = 0
    network: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthChallengeMessage(Message):
    """Challenge nonce inviato dal relay per autenticazione PSK."""
    type: str = MessageType.AUTH_CHALLENGE
    nonce: str = ""


@dataclass
class HeartbeatAckMessage(Message):
    """Ack del heartbeat inviato dal relay all'agent."""
    type: str = MessageType.HEARTBEAT_ACK


@dataclass
class PingMessage(Message):
    """Ping dal relay per verificare connessione agent."""
    type: str = MessageType.PING


# Mappa tipo messaggio → classe
MESSAGE_TYPES: dict[str, type] = {
    MessageType.REGISTER: RegisterMessage,
    MessageType.HEARTBEAT: HeartbeatMessage,
    MessageType.AUTH_CHALLENGE: AuthChallengeMessage,
    MessageType.HEARTBEAT_ACK: HeartbeatAckMessage,
    MessageType.PING: PingMessage,
    MessageType.COMMAND: CommandMessage,
    MessageType.COMMAND_RESPONSE: CommandResponse,
    MessageType.FILE_DOWNLOAD: FileDownloadMessage,
    MessageType.FILE_UPLOAD: FileUploadMessage,
    MessageType.FILE_RESPONSE: FileResponse,
    MessageType.SCREENSHOT: ScreenshotMessage,
    MessageType.SCREENSHOT_RESPONSE: ScreenshotResponse,
    MessageType.INFO: InfoRequestMessage,
    MessageType.INFO_RESPONSE: InfoResponse,
    MessageType.ERROR: Message,
}


def parse_message(data: dict) -> Message:
    """Deserializza un dizionario nel tipo di messaggio corretto. Rifiuta versioni non compatibili."""
    msg_version = data.get("version", "")
    if msg_version and msg_version != PROTOCOL_VERSION:
        from .constants import PROTOCOL_VERSION as PV
        raise ValueError(
            f"Versione protocollo non compatibile: ricevuto {msg_version}, atteso {PV}"
        )
    msg_type = data.get("type", "")
    msg_class = MESSAGE_TYPES.get(msg_type, Message)
    known_fields = {f for f in msg_class.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return msg_class(**filtered)