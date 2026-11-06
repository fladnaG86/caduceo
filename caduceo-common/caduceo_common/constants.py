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

# Reconnect backoff (esponenziale con jitter)
RECONNECT_INITIAL = 1.0   # secondi
RECONNECT_MAX = 60.0      # secondi
RECONNECT_MULTIPLIER = 2.0
RECONNECT_JITTER_MAX = 1.0  # secondi di jitter random per thundering herd

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

# Auth challenge
AUTH_NONCE_LENGTH = 32           # bytes (256 bit) per challenge server

# JWT
JWT_ALGORITHM = "HS256"
JWT_EXPIRY = 3600  # 1 ora per token API

# Heartbeat encryption
HEARTBEAT_ENCRYPTED = False  # Crittografa heartbeat post-auth (disabled for debugging)

# File transfer
FILE_CHUNK_SIZE = 1024 * 1024  # 1 MB
FILE_MAX_SIZE = 50 * 1024 * 1024  # 50 MB max per singolo file

# WebSocket message size limit (prevents OOM from oversized messages)
WS_MAX_SIZE = 10 * 1024 * 1024  # 10 MB

# Screenshot
SCREENSHOT_FORMAT = "png"
SCREENSHOT_QUALITY = 85  # per JPEG, se usato

# Streaming remoto (remote control)
STREAM_FORMAT = "jpeg"
STREAM_QUALITY = 70           # qualità JPEG frame streaming (bilanciamento qualità/bandwidth)
STREAM_FPS = 10               # frame per secondo target
STREAM_INTERVAL = 1.0 / STREAM_FPS
STREAM_MAX_FRAME_BYTES = 512 * 1024  # 512 KB max per singolo frame streaming
STREAM_SCALE_FACTOR = 1.0     # riduzione scala (1.0 = originale, 0.5 = metà) per risparmiare bandwidth
STREAM_IDLE_FRAMES = 60       # max frame consecutivi senza cambiamenti prima di auto-stop (risparmio CPU)

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
    STREAM_FRAME = "stream_frame"        # frame JPEG continuo (streaming remoto)
    INPUT_RESPONSE = "input_response"    # ack dall'agent per evento input
    STREAM_RESPONSE = "stream_response"  # ack avvio/stop streaming
    ERROR = "error"

    # Relay → Agent
    AUTH_CHALLENGE = "auth_challenge"  # Server invia nonce per PSK challenge-response
    COMMAND = "command"
    HEARTBEAT_ACK = "heartbeat_ack"
    FILE_DOWNLOAD = "file_download"
    FILE_UPLOAD = "file_upload"
    SCREENSHOT = "screenshot"
    INFO = "info"
    STREAM_START = "stream_start"      # avvia streaming remoto
    STREAM_STOP = "stream_stop"        # ferma streaming remoto
    INPUT_EVENT = "input_event"        # evento input (mouse/tastiera) dall'operatore
    PING = "ping"

    # Hermes → Relay (REST)
    # Gestito via HTTP, non WebSocket


# Tag default per agent
DEFAULT_TAGS = ["caduceo"]