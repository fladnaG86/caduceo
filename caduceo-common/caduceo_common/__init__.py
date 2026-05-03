# Caduceo Protocol Constants
# Versione protocollo
PROTOCOL_VERSION = "0.1.0"

# Porte default
DEFAULT_RELAY_PORT = 8443
DEFAULT_RELAY_HOST = "0.0.0.0"

# WebSocket path
WS_AGENT_PATH = "/agent"
WS_API_PATH = "/api"

# Heartbeat
HEARTBEAT_INTERVAL = 30  # secondi
HEARTBEAT_TIMEOUT = 90   # secondi (3x interval)

# Reconnect backoff (esponenziale)
RECONNECT_INITIAL = 1.0   # secondi
RECONNECT_MAX = 60.0      # secondi
RECONNECT_MULTIPLIER = 2.0

# Comandi
COMMAND_TIMEOUT_DEFAULT = 30    # secondi
COMMAND_TIMEOUT_MAX = 300        # 5 minuti max
COMMAND_STREAM_BUFFER = 4096    # bytes

# Coda comandi offline
QUEUE_TTL_DEFAULT = 86400       # 24 ore
QUEUE_MAX_SIZE = 100

# Crittografia
CRYPTO_ALGORITHM = "aes-256-gcm"
CRYPTO_KEY_LENGTH = 32           # bytes (256 bit)
CRYPTO_NONCE_LENGTH = 12        # bytes (96 bit)
CRYPTO_TAG_LENGTH = 16           # bytes (128 bit)

# JWT
JWT_ALGORITHM = "HS256"
JWT_EXPIRY = 3600  # 1 ora per token API

# File transfer
FILE_CHUNK_SIZE = 1024 * 1024  # 1 MB
FILE_MAX_SIZE = 50 * 1024 * 1024  # 50 MB max per singolo file

# Screenshot
SCREENSHOT_FORMAT = "png"
SCREENSHOT_QUALITY = 85  # per JPEG, se usato

# Sistema - nomi piattaforma
OS_WINDOWS = "windows"
OS_LINUX = "linux"
OS_MACOS = "macos"

# Tipi di messaggio
class MessageType:
    # Agent → Relay
    REGISTER = "register"
    HEARTBEAT = "heartbeat"
    COMMAND_RESPONSE = "command_response"
    FILE_RESPONSE = "file_response"
    SCREENSHOT_RESPONSE = "screenshot_response"
    INFO_RESPONSE = "info_response"
    ERROR = "error"

    # Relay → Agent
    COMMAND = "command"
    FILE_DOWNLOAD = "file_download"
    FILE_UPLOAD = "file_upload"
    SCREENSHOT = "screenshot"
    INFO = "info"
    PING = "ping"

    # Hermes → Relay (REST)
    # Gestito via HTTP, non WebSocket

# Tag default per agent
DEFAULT_TAGS = ["caduceo"]