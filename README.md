# Caduceo

Il bastone di Hermes che raggiunge ogni mondo.

Sistema di accesso remoto per agente AI. Consente a Hermes di collegarsi a PC sparsi su reti diverse (dietro NAT/firewall) e di eseguire comandi shell, trasferire file, e ottenere informazioni di sistema.

## Architettura

```
[PC Remoto]                         [Tuo Server]                        [Tu]
┌──────────────┐  outbound WSS   ┌───────────────────┐  Cloudflare   ┌──────────┐
│ caduceo-agent │ ─────────────→  │   cloudflared     │   Tunnel     │  Hermes   │
│  (service)   │ ──────────────  │   caduceo-relay   │ ←─────────── │  Gateway  │
└──────────────┘  comandi/risp.   └───────────────────┘  REST API    └──────────┘
                                          :8443
                                    (localhost only)
```

Il relay ascolta solo su `localhost:8443`. Il traffico pubblico passa tramite **Cloudflare Tunnel** (nessuna porta aperta sul router,nessun port forwarding necessario). TLS e' gestito automaticamente da Cloudflare.

- **caduceo-agent**: Leggero client Python. Si installa sui PC remoti, si connette in outbound al relay via WebSocket. Supporta Windows, Linux, macOS.
- **caduceo-relay**: Server centrale con FastAPI + WebSocket. Accetta connessioni dagli agent, espone REST API per Hermes, instrada comandi. Ascolta solo su localhost.
- **caduceo-common**: Librerie condivise (protocollo, crittografia AES-256-GCM, serializzazione).
- **caduceo-hermes**: Skill/integrazione per Hermes Agent.

## Deployment

### Servizi Systemd Attivi

| Servizio | Descrizione | Porta |
|----------|-------------|-------|
| `caduceo-relay.service` | Relay server (FastAPI + WS) | localhost:8443 |
| `cloudflared-caduceo.service` | Cloudflare Tunnel verso pubblico | N/A (outbound) |
| `caddy.service` | Reverse proxy locale (dev) | localhost:8400 |

### Endpoints Pubblici

| URL | Descrizione |
|-----|-------------|
| `https://caduceo.maulanhermes.uk/api/health` | Health check |
| `https://caduceo.maulanhermes.uk/api/agents` | Lista agent (richiede auth) |
| `https://caduceo.maulanhermes.uk/api/agents/{id}/command` | Invio comandi |
| `wss://caduceo.maulanhermes.uk/ws` | WebSocket agent |

### Configurazione Cloudflare

- **Tunnel**: creato via Zero Trust Dashboard, punta `caduceo.maulanhermes.uk` → `http://localhost:8443`
- **SSL**: gestito da Cloudflare (Flexible mode)
- **Nessun port forwarding** sul router: il tunnel e' in uscita (outbound)

### Credenziali

| File | Contenuto | Permessi |
|------|-----------|----------|
| `~/.caduceo/relay.json` | Config relay (porta, JWT secret) | 0600 |
| `~/.caduceo/credentials.json` | PSK e secret per agent auth | 0600 |

### Installazione Relay

```bash
# Il relay gira come servizio systemd
sudo systemctl status caduceo-relay

# Log
journalctl -u caduceo-relay -f

# Il tunnel Cloudflare gira come servizio dedicato
sudo systemctl status cloudflared-caduceo

# Per sviluppo locale senza tunnel
# Il relay e' accessibile su localhost:8400 via Caddy
curl http://localhost:8400/api/health
```

### Installazione Agent (PC rimoti)

```bash
pip install -e ./caduceo-agent
caduceo-agent --relay wss://caduceo.maulanhermes.uk/ws --psk <PSK_FILE>
```

Oppure come servizio systemd:
```bash
caduceo-agent install --relay wss://caduceo.maulanhermes.uk/ws --psk ~/.caduceo/agent.psk
```

## Sicurezza

- **Crittografia end-to-end**: AES-256-GCM su ogni payload WebSocket tra agent e relay
- **Autenticazione PSK**: challenge SHA256(psk_hex + agent_id + nonce) per WebSocket
- **JWT HS256**: autenticazione REST API per Hermes
- **Token endpoint**: `/api/token` limitato a localhost (127.0.0.1, ::1)
- **CORS ristretto**: origins specifiche, non `*`
- **Path traversal protection**: sanificazione path nei comandi shell
- **Nessuna esposizione diretta**: relay su localhost, tunnel Cloudflare per traffico pubblico
- **File permissions**: credenziali con permessi 0600

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