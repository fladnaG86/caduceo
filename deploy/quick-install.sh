#!/bin/bash
# Caduceo Agent - Quick Install (Linux/macOS)
# Usage: curl -sSL https://your-relay.example.com/download/install.py?platform=linux | python3 -
# Or:   python3 quick-install.sh
# 
# Non-interactive: pass AGENT_ID and TAGS as environment variables
#   AGENT_ID=my-pc TAGS="ufficio,linux" python3 quick-install.sh

set -e

AGENT_ID="${AGENT_ID:-}"
TAGS="${TAGS:-caduceo}"
RELAY_URL="wss://your-relay.example.com"
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
INSTALLER_URL="https://your-relay.example.com/download/install.py?platform=${PLATFORM}"

echo "=== Caduceo Agent Quick Install ==="
echo "Platform: $PLATFORM"
echo "Relay:    $RELAY_URL"

# Download installer
echo "[1/3] Downloading installer..."
curl -sSL "$INSTALLER_URL" -o /tmp/caduceo-install.py

# If AGENT_ID is set, create agent.json and run non-interactively
if [ -n "$AGENT_ID" ]; then
    echo "[2/3] Creating config for agent: $AGENT_ID"
    mkdir -p ~/.caduceo
    cat > ~/.caduceo/agent.json << EOF
{
    "relay_url": "$RELAY_URL",
    "psk_hex": "CHANGE_ME_GENERATE_A_NEW_PSK",
    "agent_id": "$AGENT_ID",
    "tags": "$TAGS"
}
EOF
    chmod 600 ~/.caduceo/agent.json
    echo "[3/3] Running installer..."
    python3 /tmp/caduceo-install.py
else
    echo "[2/3] Running interactive installer..."
    python3 /tmp/caduceo-install.py
fi

echo ""
echo "=== Installazione completata ==="