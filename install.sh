#!/usr/bin/env bash
# Caduceo Agent Installer
# Usage: curl -sSL https://maulanhermes.uk:8443/install.sh | bash -s -- --id pc-ufficio --tags ufficio,windows
# Or:   ./install.sh --id pc-ufficio --tags ufficio,windows

set -euo pipefail

RELAY_URL="wss://maulanhermes.uk:8443"
PSK=""
AGENT_ID=""
TAGS="caduceo"
INSTALL_SERVICE=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[CADUCEO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[CADUCEO]${NC} $*"; }
error() { echo -e "${RED}[CADUCEO]${NC} $*" >&2; exit 1; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --id)        AGENT_ID="$2"; shift 2 ;;
        --tags)      TAGS="$2"; shift 2 ;;
        --psk)       PSK="$2"; shift 2 ;;
        --relay)     RELAY_URL="$2"; shift 2 ;;
        --install)   INSTALL_SERVICE=true; shift ;;
        -h|--help)
            echo "Usage: $0 --id AGENT_ID --tags tag1,tag2 [--psk HEX_KEY] [--relay URL] [--install]"
            echo ""
            echo "  --id        Agent ID (required, e.g. pc-ufficio)"
            echo "  --tags      Comma-separated tags (default: caduceo)"
            echo "  --psk       Pre-shared key hex (required for secured relay)"
            echo "  --relay     Relay URL (default: wss://maulanhermes.uk:8443)"
            echo "  --install   Install as system service (systemd/launchd)"
            exit 0
            ;;
        *) error "Unknown option: $1. Use --help for usage." ;;
    esac
done

info "Caduceo Agent Installer"
info "========================="

# Detect OS
OS_NAME="linux"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS_NAME="macos"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    OS_NAME="windows"
fi
info "Detected OS: $OS_NAME"

# Check Python
if command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    error "Python 3.10+ is required. Install it from https://python.org"
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python version: $PY_VERSION"

# Check Python version >= 3.10
$PYTHON -c "import sys; assert sys.version_info >= (3, 10)" 2>/dev/null || \
    error "Python 3.10+ required, found $PY_VERSION"

# Create installation directory
INSTALL_DIR="$HOME/.caduceo-agent"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

info "Installing to $INSTALL_DIR"

# Create venv if not exists
if [[ ! -d "venv" ]]; then
    info "Creating virtual environment..."
    $PYTHON -m venv venv
fi

# Install caduceo-agent
info "Installing caduceo-agent..."
source venv/bin/activate

# Install from PyPI when published, or from git
pip install --quiet --upgrade pip 2>/dev/null
pip install --quiet caduceo-agent 2>/dev/null || {
    # Fallback: install from git
    warn "PyPI package not found, installing from git..."
    pip install --quiet git+https://github.com/user/caduceo.git#subdirectory=caduceo-agent 2>/dev/null || {
        error "Could not install caduceo-agent. Install manually with: pip install caduceo-agent"
    }
}

# Generate agent ID if not provided
if [[ -z "$AGENT_ID" ]]; then
    AGENT_ID=$($PYTHON -c "import platform; print(platform.node().lower().replace(' ', '-'))")
    warn "No --id provided, using hostname: $AGENT_ID"
fi

# Create config file
CONFIG_FILE="$INSTALL_DIR/agent.conf"
cat > "$CONFIG_FILE" << EOF
CADUCEO_RELAY_URL=$RELAY_URL
CADUCEO_AGENT_ID=$AGENT_ID
CADUCEO_TAGS=$TAGS
EOF

if [[ -n "$PSK" ]]; then
    echo "CADUCEO_PSK=$PSK" >> "$CONFIG_FILE"
fi

info "Config saved to $CONFIG_FILE"
info "  Agent ID: $AGENT_ID"
info "  Tags: $TAGS"
info "  Relay: $RELAY_URL"

# Test connection
info "Testing connection to relay..."
$PYTHON -m caduceo_agent --relay "$RELAY_URL" --psk "${PSK:-test}" --agent-id "$AGENT_ID" --tags "$TAGS" &
TEST_PID=$!
sleep 5
kill $TEST_PID 2>/dev/null || true

echo ""
info "Installation complete!"
echo ""

if [[ "$INSTALL_SERVICE" == true ]]; then
    info "Installing as system service..."
    $PYTHON -m caduceo_agent --relay "$RELAY_URL" --psk "${PSK:?PSK is required for service installation}" --agent-id "$AGENT_ID" --tags "$TAGS" --install
else
    info "To start the agent manually:"
    info "  source $INSTALL_DIR/venv/bin/activate"
    info "  caduceo-agent --relay $RELAY_URL --psk YOUR_PSK --agent-id $AGENT_ID --tags $TAGS"
    echo ""
    info "To install as a system service (auto-start on boot):"
    info "  $0 --id $AGENT_ID --tags $TAGS --psk YOUR_PSK --install"
fi