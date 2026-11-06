#!/usr/bin/env bash
# ============================================================
#  Caduceo LOCAL Relay Installer (Ubuntu/Debian)
#  Installa un relay Caduceo indipendente su una VM nella rete
#  di dominio. Genera PSK/JWT/download_key NUOVI e separati.
#
#  Uso:
#    sudo ./install-relay-local.sh
#    sudo ./install-relay-local.sh --port 8443 --host 0.0.0.0
#
#  Dopo l'installazione:
#    - Config:  /etc/caduceo/relay.json
#    - Servizio: systemctl status caduceo-relay
#    - PSK:     /etc/caduceo/relay.json (campo psk_hex)
# ============================================================
set -euo pipefail

# Colori
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[CADUCEO-LOCAL]${NC} $*"; }
warn()  { echo -e "${YELLOW}[CADUCEO-LOCAL]${NC} $*"; }
error() { echo -e "${RED}[CADUCEO-LOCAL]${NC} $*" >&2; exit 1; }

# Default
RELAY_HOST="0.0.0.0"
RELAY_PORT="8443"
CADUCEO_DIR="/opt/caduceo"
CONFIG_DIR="/etc/caduceo"
SERVICE_NAME="caduceo-relay"
REPO_URL="https://github.com/fladnaG86/caduceo.git"
CONFIGURE_UFW=false
# Subnet autorizzate (imposta quelle reali della tua rete — NON usare /8 se le reti sono piu' ristrette)
ALLOWED_SUBNETS="192.168.0.0/24"
# Commit/tag del repo da usare (per produzione usa un commit SHA completo o tag firmato)
REPO_REF="main"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --host) RELAY_HOST="$2"; shift 2 ;;
        --port) RELAY_PORT="$2"; shift 2 ;;
        --configure-ufw) CONFIGURE_UFW=true; shift ;;
        --subnets) ALLOWED_SUBNETS="$2"; shift 2 ;;
        --repo-ref) REPO_REF="$2"; shift 2 ;;
        -h|--help)
            echo "Uso: $0 [--host 0.0.0.0] [--port 8443] [--configure-ufw] [--subnets '10.0.0.0/8,192.168.0.0/24'] [--repo-ref <commit|tag>]"
            echo ""
            echo "  --host           IP di ascolto (default: 0.0.0.0)"
            echo "  --port           Porta (default: 8443)"
            echo "  --configure-ufw  Abilita e configura UFW (default: off — non tocca il firewall)"
            echo "  --subnets        Subnet autorizzate, separate da virgola (default: 192.168.0.0/24)"
            echo "  --repo-ref       Commit SHA o tag del repo (default: main — usa un tag firmato in produzione)"
            exit 0 ;;
        *) error "Opzione sconosciuta: $1" ;;
    esac
done

# Root check
if [[ $EUID -ne 0 ]]; then
    error "Esegui come root: sudo $0"
fi

info "Caduceo LOCAL Relay Installer"
info "==============================="
info "Host: $RELAY_HOST | Porta: $RELAY_PORT"
info "Installazione in: $CADUCEO_DIR"

# 1. Install dependencies
info "[1/5] Installazione dipendenze di sistema..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git curl

# Crea utente di sistema dedicato (senza shell) per il relay
# — non eseguire il servizio come root (blast-radius ridotta)
if ! id -u caduceo &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin caduceo
    info "Utente di sistema 'caduceo' creato"
fi

# Configura UFW SOLO se richiesto esplicitamente (--configure-ufw)
# — non abilitare mai automaticamente un firewall che potrebbe bloccare SSH/management
if [[ "$CONFIGURE_UFW" == true ]]; then
    apt-get install -y ufw
    if command -v ufw &> /dev/null; then
        info "Configurazione UFW per la porta $RELAY_PORT/tcp (subnet: $ALLOWED_SUBNETS)..."
        # Rimuovi spazi (l'utente potrebbe passare "10.0.0.0/8, 192.168.0.0/24")
        ALLOWED_SUBNETS=$(echo "$ALLOWED_SUBNETS" | tr -d ' ')
        IFS=',' read -ra SUBNETS <<< "$ALLOWED_SUBNETS"
        for subnet in "${SUBNETS[@]}"; do
            ufw allow from "$subnet" to any port "$RELAY_PORT" proto tcp || warn "Impossibile aprire UFW per $subnet"
        done
        # Abilita UFW (disattivato di default su Ubuntu — senza enable le regole restano inerti)
        ufw --force enable || warn "Impossibile abilitare UFW (potrebbe essere gestito altrove)"
        ufw status verbose
    else
        warn "UFW non disponibile — la porta $RELAY_PORT resta aperta a tutti. Installa ufw per sicurezza."
    fi
else
    info "UFW non configurato (usa --configure-ufw per abilitarlo). Assicurati che la porta $RELAY_PORT sia aperta nel firewall di rete."
fi

# 2. Clone repo (pinned a un commit/tag stabile — non usare HEAD per produzione)
info "[2/5] Clonazione repository Caduceo (pinned a: $REPO_REF)..."
if [[ ! -d "$CADUCEO_DIR" ]]; then
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$CADUCEO_DIR"
else
    warn "Directory $CADUCEO_DIR esiste, aggiorno..."
    cd "$CADUCEO_DIR" && git fetch --depth 1 origin "$REPO_REF" && git checkout --detach FETCH_HEAD || warn "Impossibile aggiornare a $REPO_REF (verifica manualmente)"
fi

# 3. Create venv + install relay
info "[3/5] Creazione venv e installazione relay..."
cd "$CADUCEO_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e "./caduceo-common" -e "./caduceo-relay[relay]"

# 4. Create config dir + generate fresh secrets
info "[4/5] Generazione configurazione (PSK/JWT NUOVI e separati)..."
mkdir -p "$CONFIG_DIR"

# Se la config esiste gia', PRESERVA le chiavi (un rerun non deve
# invalidare gli agenti gia' installati). Genera nuove solo al primo avvio.
if [[ -f "$CONFIG_DIR/relay.json" ]]; then
    info "Config esistente trovata — preservo le chiavi esistenti."
    PSK_HEX=$(python3 -c "import json; print(json.load(open('$CONFIG_DIR/relay.json'))['psk_hex'])")
    JWT_SECRET=$(python3 -c "import json; print(json.load(open('$CONFIG_DIR/relay.json'))['jwt_secret'])")
    DOWNLOAD_KEY=$(python3 -c "import json; print(json.load(open('$CONFIG_DIR/relay.json'))['download_key'])")
else
    # Genera PSK (64 hex) e JWT secret (64 hex) e download_key (32 hex)
    PSK_HEX=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    DOWNLOAD_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
fi

cat > "$CONFIG_DIR/relay.json" << EOF
{
    "host": "$RELAY_HOST",
    "port": $RELAY_PORT,
    "jwt_secret": "$JWT_SECRET",
    "psk_hex": "$PSK_HEX",
    "queue_ttl": 86400,
    "download_key": "$DOWNLOAD_KEY"
}
EOF
# CRITICO: il servizio gira come utente 'caduceo' (gruppo caduceo).
# chmod 600 con owner root:caduceo impedirebbe al servizio di leggere il file!
# Serve 640 (owner root legge, gruppo caduceo legge) + 750 sulla dir.
# IMPORTANTE: la DIRECTORY deve essere owner root:caduceo, altrimenti il
# gruppo caduceo non puo' nemmeno attraversarla (chmod 750 su root:root
# blocca l'accesso a chi non e' nel gruppo root).
chown root:caduceo "$CONFIG_DIR"
chown root:caduceo "$CONFIG_DIR/relay.json"
chmod 750 "$CONFIG_DIR"
chmod 640 "$CONFIG_DIR/relay.json"

# NON stampare le chiavi integrali su stdout (journald potrebbe catturarle)
info "       Config: $CONFIG_DIR/relay.json (permessi 640, owner root:caduceo)"
info "       PSK:    ...${PSK_HEX: -8} (intero nel file config)"
info "       JWT:    ...${JWT_SECRET: -8} (intero nel file config)"

# 5. Create systemd service (hardened, utente dedicato)
info "[5/5] Registrazione servizio systemd (hardened)..."
cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=Caduceo LOCAL Relay Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=caduceo
Group=caduceo
WorkingDirectory=$CADUCEO_DIR/caduceo-relay
Environment=PYTHONPATH=$CADUCEO_DIR/caduceo-relay
ExecStart=$CADUCEO_DIR/.venv/bin/python -m caduceo_relay --host $RELAY_HOST --port $RELAY_PORT --config $CONFIG_DIR/relay.json
Restart=on-failure
RestartSec=5
StartLimitBurst=10
# Hardening: limita blast-radius in caso di compromissione
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
# Il relay deve LEGGERE la config, non modificarla — /etc/caduceo in sola lettura
ReadWritePaths=/var/log/caduceo
ReadOnlyPaths=$CONFIG_DIR
RestrictAddressFamilies=AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
EOF

# Ownership e permessi del codice/venv
chown -R root:caduceo "$CADUCEO_DIR"
chmod -R o-rwx "$CADUCEO_DIR"
mkdir -p /var/log/caduceo
chown caduceo:caduceo /var/log/caduceo

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

# Verify con retry (cold start puo' richiedere piu' di 2s)
info "Attesa avvio servizio (retry fino a 30s)..."
for i in $(seq 1 30); do
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        break
    fi
    sleep 1
done
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Servizio $SERVICE_NAME ATTIVO!"
else
    error "Servizio $SERVICE_NAME non attivo. Controlla: journalctl -u $SERVICE_NAME -n 50"
fi

# Health check con retry e validazione JSON (non basta != "{}")
HEALTH=""
for i in $(seq 1 10); do
    HEALTH=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
        "http://127.0.0.1:$RELAY_PORT/api/health" 2>/dev/null || echo "")
    if [[ -n "$HEALTH" ]] && echo "$HEALTH" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
        break
    fi
    HEALTH=""
    sleep 1
done
if [[ -n "$HEALTH" ]]; then
    info "Health check: $HEALTH"
else
    error "Health check fallito. Controlla: journalctl -u $SERVICE_NAME -n 50"
fi

echo ""
echo "============================================================"
echo "  Caduceo LOCAL Relay installato!"
echo "============================================================"
echo "  Relay URL (per agenti): ws://<IP-DI-QUESTA-VM>:$RELAY_PORT"
echo "  Config:                $CONFIG_DIR/relay.json"
echo "  PSK (per agenti):      ...${PSK_HEX: -8} (intero nel file config)"
echo "  JWT secret:            ...${JWT_SECRET: -8} (intero nel file config)"
echo "  download_key:          ...${DOWNLOAD_KEY: -8} (intero nel file config)"
echo ""
echo "  IMPORTANTE: le chiavi complete sono in $CONFIG_DIR/relay.json"
echo "  (permessi 640, owner root:caduceo). Servono per:"
echo "   - PSK: configurare gli agenti (install-agent-gpo.ps1)"
echo "   - JWT: generare token per l'API (curl /api/token)"
echo "============================================================"
