"""Caduceo Common - Protocollo e utilità condivise."""

from .constants import (
    PROTOCOL_VERSION,
    DEFAULT_RELAY_PORT,
    DEFAULT_RELAY_HOST,
    WS_AGENT_PATH,
    HEARTBEAT_INTERVAL,
    HEARTBEAT_TIMEOUT,
    RECONNECT_INITIAL,
    RECONNECT_MAX,
    RECONNECT_MULTIPLIER,
    COMMAND_TIMEOUT_DEFAULT,
    CRYPTO_ALGORITHM,
    CRYPTO_KEY_LENGTH,
    CRYPTO_NONCE_LENGTH,
    CRYPTO_TAG_LENGTH,
    JWT_ALGORITHM,
    JWT_EXPIRY,
    FILE_CHUNK_SIZE,
    FILE_MAX_SIZE,
    SCREENSHOT_FORMAT,
    SCREENSHOT_QUALITY,
    QUEUE_TTL_DEFAULT,
    QUEUE_MAX_SIZE,
    MessageType,
    OS_WINDOWS,
    OS_LINUX,
    OS_MACOS,
    DEFAULT_TAGS,
)
from .crypto import Crypto
from .messages import (
    Message,
    RegisterMessage,
    HeartbeatMessage,
    CommandMessage,
    CommandResponse,
    FileDownloadMessage,
    FileUploadMessage,
    FileResponse,
    ScreenshotMessage,
    ScreenshotResponse,
    InfoRequestMessage,
    InfoResponse,
    parse_message,
    MESSAGE_TYPES,
)
from .utils import detect_os, generate_agent_id, generate_request_id

__version__ = PROTOCOL_VERSION