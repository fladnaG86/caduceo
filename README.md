# Caduceo

Il bastone di Hermes che raggiunge ogni mondo.

Sistema di accesso remoto per agente AI. Consente a Hermes di collegarsi a PC sparsi su reti diverse (dietro NAT/firewall) e di eseguire comandi shell, trasferire file, e ottenere informazioni di sistema.

## Architettura

```
[PC Remoto]                         [Tuo Server]                        [Tu]
┌──────────────┐  outbound WSS   ┌───────────────────┐   Zrok        ┌──────────┐
│ caduceo-agent │ ─────────────→  │                   │   Tunnel      │  Hermes   │
│  (service)   │ ──────────────  │   caduceo-relay   │ ←───────────  │  Gateway  │
└──────────────┘  comandi/risp.   └───────────────────┘  REST API    └──────────┘
                                          :8443
                                    (localhost only)
```

Il relay ascolta solo su `0.0.0.0:8443`. Il traffico pubblico passa tramite **Zrok** con reserved name `caduceo` (URL stabile `caduceo.shares.zrok.io`). Nessuna porta aperta sul router, nessun port forwarding necessario. TLS e' gestito automaticamente da Zrok.

- **caduceo-agent**: Leggero client Python. Si installa sui PC remoti, si connette in outbound al relay via WebSocket. Supporta Windows, Linux, macOS.
- **caduceo-relay**: Server centrale con FastAPI + WebSocket. Accetta connessioni dagli agent, espone REST API per Hermes, instrada comandi. Ascolta su `0.0.0.0:8443` (locale).
- **caduceo-common**: Librerie condivise (protocollo, crittografia AES-256-GCM, serializzazione).
- **caduceo-hermes**: Skill/integrazione per Hermes Agent.

## Deployment

### Servizi Systemd Attivi

| Servizio | Descrizione | Porta |
|----------|-------------|-------|
| `caduceo-relay.service` | Relay server (FastAPI + WS) | 0.0.0.0:8443 |
| `zrok-caduceo.service` | Zrok public share (reserved: caduceo.shares.zrok.io) | N/A (outbound) |

### Endpoints Pubblici

| URL | Descrizione |
|-----|-------------|
| `https://caduceo.shares.zrok.io/api/health` | Health check |
| `https://caduceo.shares.zrok.io/api/agents` | Lista agent (richiede auth) |
| `https://caduceo.shares.zrok.io/api/agents/{id}/command` | Invio comandi |
| `wss://caduceo.shares.zrok.io/agent` | WebSocket agent |
| `https://caduceo.shares.zrok.io/download/install-agent.ps1` | Installer PowerShell |
| `https://caduceo.shares.zrok.io/download/install-agent.bat` | Installer batch |
| `https://caduceo.shares.zrok.io/download/quick-install.ps1` | Quick installer PS1 |
| `https://caduceo.shares.zrok.io/download/quick-install.bat` | Quick installer batch |
| `https://caduceo.shares.zrok.io/download/quick-install.sh` | Quick installer shell |

### Configurazione Zrok (Reserved Name)

- **Reserved name**: `caduceo` nel namespace `public` → URL stabile `caduceo.shares.zrok.io`
- **Systemd service**: `zrok-caduceo.service` con `--name-selection public:caduceo --headless`
- **Nessun port forwarding** sul router: Zrok tunnel e' in uscita (outbound)
- **URL stabile**: il reserved name persiste across restarts, non cambia mai

### Credenziali

| File | Contenuto | Permessi |
|------|-----------|----------|
| `~/.caduceo/relay.json` | Config relay (porta, JWT secret, PSK hex) | 0600 |
| `~/.caduceo/credentials.json` | URL, PSK e secret per agent auth | 0600 |
| `~/.caduceo/agent.psk` | PSK per agenti remoti | 0600 |

### Installazione Relay

```bash
# Il relay gira come servizio systemd
sudo systemctl status caduceo-relay

# Lo Zrok share gira come servizio dedicato
sudo systemctl status zrok-caduceo

# Log
journalctl -u caduceo-relay -f

# Per sviluppo locale senza Zrok
curl http://localhost:8443/api/health
```

### Installazione Agent (PC rimoti)

```bash
pip install -e ./caduceo-agent
caduceo-agent --relay wss://caduceo.shares.zrok.io/agent --psk <PSK_FILE>
```

Oppure come servizio systemd:
```bash
caduceo-agent install --relay wss://caduceo.shares.zrok.io/agent --psk ~/.caduceo/agent.psk
```

Oppure con quick-install dal browser:
```powershell
# Su Windows (da PowerShell)
irm https://caduceo.shares.zrok.io/download/quick-install.ps1 | iex
```

## Sicurezza

- **Crittografia end-to-end**: AES-256-GCM su ogni payload WebSocket tra agent e relay
- **Autenticazione PSK**: challenge SHA256(psk_hex + agent_id + nonce) per WebSocket
- **JWT HS256**: autenticazione REST API per Hermes
- **Token endpoint**: `/api/token` limitato a localhost (127.0.0.1, ::1)
- **CORS ristretto**: origins specifiche, non `*`
- **Path traversal protection**: sanificazione path nei comandi shell
- **Nessuna esposizione diretta**: relay su localhost, Zrok tunnel per traffico pubblico
- **File permissions**: credenziali con permessi 0600
- **Audit logging**: tutte le azioni registrate in SQLite (`~/.caduceo/audit.db`)

## Supporto Piattaforma

| Piattaforma | Shell | Service | Stato |
|-------------|-------|---------|-------|
| Linux       | bash  | systemd | ✅ |
| macOS       | zsh   | launchd | ✅ |
| Windows     | PowerShell | Win Service | ✅ |

## Test

```bash
cd /home/manutenzione/caduceo
python -m pytest tests/test_caduceo.py -v
# 21 test (crypto, messages, queue, security)
```

## Licenza

MIT