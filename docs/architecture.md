# Caduceo - Architecture

## Overview

Caduceo e' composto da 3 componenti principali che comunicano tramite un protocollo binario su WebSocket crittografato. Il deployment usa Cloudflare Tunnel per evitare l'esposizione diretta del server.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        CADUCEO ARCHITECTURE                              │
│                                                                          │
│  ┌──────────┐        ┌────────────────────────────────┐        ┌────────┐│
│  │  HERMES   │ HTTPS  │        CLOUDFLARE              │  WSS   │ AGENT  ││
│  │  Gateway  │←───────│  caduceo.maulanhermes.uk       │←──────│  (PC)  ││
│  │          │        │          │                       │        │        ││
│  │ - Invia   │        │    cloudflared                 │        │- Shell ││
│  │   cmd    │ ──→    │       │                         │  ──→   │- File  ││
│  │ - Riceve  │        │  ┌────┴─────┐                  │        │- Info  ││
│  │   output │ ←─     │  │  relay    │ localhost:8443   │   ←─   │- Heartb││
│  └──────────┘        │  │  FastAPI  │                  │        └────────┘│
│                       │  └──────────┘                  │                  │
│                       └────────────────────────────────┘                  │
│                                                                          │
│  Sicurezza:                                                              │
│  - TLS gestito da Cloudflare (client ↔ CF)                              │
│  - Cloudflare Tunnel (CF ↔ relay, outbound only)                        │
│  - AES-256-GCM sui payload WebSocket (agent ↔ relay)                    │
│  - JWT HS256 per REST API (Hermes ↔ relay)                              │
│  - PSK challenge per autenticazione WebSocket                            │
│  - /api/token limitato a localhost                                       │
│  - CORS ristretto, path traversal protection                             │
└──────────────────────────────────────────────────────────────────────────┘
```

## Topologia di Rete

```
Internet                          Server (192.168.0.249)
─────────                        ─────────────────────────
                                  ┌─────────────────────┐
                                  │ cloudflared          │ ← outbound to CF
                                  │ (systemd service)    │
                                  └─────────┬───────────┘
                                            │
Client ── HTTPS ──→ Cloudflare ──→ Tunnel ──→│ localhost:8443
                                            │
                                  ┌─────────┴───────────┐
                                  │ caduceo-relay        │
                                  │ (FastAPI + WS)       │
                                  └─────────┬───────────┘
                                            │
                                  ┌─────────┴───────────┐
                                  │ Caddy (dev only)     │
                                  │ localhost:8400       │
                                  └─────────────────────┘

- NESSUNA porta aperta sul router
- NESSUN port forwarding necessario
- ISP non puo' bloccare (connessione outbound)
```

## Servizi Systemd

| Servizio | Command | Note |
|----------|---------|------|
| `caduceo-relay.service` | `python -m caduceo_relay --config ~/.caduceo/relay.json` | Ascolta su localhost:8443 |
| `cloudflared-caduceo.service` | `cloudflared tunnel run --token <TOKEN>` | Tunnel verso Cloudflare |
| `caddy.service` | Caddy reverse proxy | Dev locale, porta 8400 |

File di deploy in `/home/manutenzione/caduceo/deploy/`:
- `caduceo-relay.service`
- `cloudflared-caduceo.service`
- `Caddyfile`

## Protocollo di Comunicazione

### Agent → Relay (WebSocket)

Il client si connette in outbound a `wss://caduceo.maulanhermes.uk/ws` e mantiene la connessione aperta con heartbeat.

**Registrazione:**
```json
{
  "type": "register",
  "agent_id": "pc-ufficio-01",
  "hostname": "DESKTOP-A1B2C3",
  "os": "windows",
  "os_version": "10.0.19045",
  "arch": "amd64",
  "python_version": "3.11.5",
  "tags": ["ufficio", "windows"]
}
```

**Heartbeat:**
```json
{
  "type": "heartbeat",
  "agent_id": "pc-ufficio-01",
  "timestamp": 1714723200,
  "stats": {
    "cpu_percent": 45.2,
    "memory_percent": 62.1,
    "disk_percent": 73.0
  }
}
```

**Risposta comando:**
```json
{
  "type": "command_response",
  "request_id": "req-abc123",
  "agent_id": "pc-ufficio-01",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "duration_ms": 1250
}
```

**Risposta file:**
```json
{
  "type": "file_response",
  "request_id": "req-def456",
  "agent_id": "pc-ufficio-01",
  "filename": "report.pdf",
  "size": 1048576,
  "content_b64": "...",
  "checksum_sha256": "abc123..."
}
```

### Hermes → Relay (REST API)

Base URL: `https://caduceo.maulanhermes.uk`

Tutte le API richiedono header `Authorization: Bearer <JWT_TOKEN>`.

**Elenca agent connessi:**
```
GET /api/agents
```

**Invia comando:**
```
POST /api/agents/{agent_id}/command
{
  "command": "powershell",
  "args": ["Get-Process | Where-Object {$_.CPU -gt 100}"],
  "timeout": 30
}
```

**Download file:**
```
POST /api/agents/{agent_id}/file/download
{
  "path": "C:/Users/Maurizio/Documents/report.pdf"
}
```

**Upload file:**
```
POST /api/agents/{agent_id}/file/upload
{
  "path": "C:/Users/Maurizio/Desktop/script.ps1",
  "content_b64": "...",
  "overwrite": true
}
```

**Info di sistema:**
```
GET /api/agents/{agent_id}/info
```

**Screenshot:**
```
POST /api/agents/{agent_id}/screenshot
```

**Ottieni token (localhost only):**
```
GET /api/token
# Solo accessibile da 127.0.0.1 / ::1
```

## Crittografia

```
Chiave pre-condivisa (PSK)
  │
  ├── AES-256-GCM per payload WebSocket (agent ↔ relay)
  │   ├── PSK letta da env CADUCEO_PSK o ~/.caduceo/agent.psk
  │   ├── Challenge: SHA256(psk_hex + agent_id + nonce)
  │   └── Ogni frame: nonce(12B) + ciphertext + tag(16B)
  │
  ├── TLS 1.3 per trasporto (gestito da Cloudflare)
  │
  └── JWT HS256 per autenticazione API (Hermes ↔ relay)
      ├── Token generato da /api/token (localhost only)
      └── Scadenza configurabile in relay.json
```

## Autenticazione WebSocket (PSK Challenge)

1. Agent si connette a `/ws` e invia `{"type": "auth_challenge", "agent_id": "...", "nonce": "..."}`
2. Relay risponde con `{"type": "auth_challenge", "nonce": "..."}`
3. Agent calcola `response = SHA256(psk_hex + agent_id + relay_nonce + agent_nonce)`
4. Relay verifica la response
5. Se valida, agent e' registrato e autenticato

## Installsione come Servizio

### Linux (systemd)
```bash
caduceo-agent install --relay wss://caduceo.maulanhermes.uk/ws --psk ~/.caduceo/agent.psk
# Crea /etc/systemd/system/caduceo-agent.service
```

### macOS (launchd)
```bash
caduceo-agent install --relay wss://caduceo.maulanhermes.uk/ws --psk ~/.caduceo/agent.psk
# Crea ~/Library/LaunchAgents/com.caduceo.agent.plist
```

### Windows (Windows Service)
```powershell
caduceo-agent install --relay wss://caduceo.maulanhermes.uk/ws --psk %USERPROFILE%\.caduceo\agent.psk
# Registra servizio Windows via pywin32
```

## Coda dei Comandi

Quando Hermes invia un comando a un agent offline, il relay lo mette in coda. Quando l'agent si riconnette, il relay recapita i comandi in sospeso (con TTL configurabile).

```
Hermes → "esegui aggiornamento su pc-officina"
         │
         ↓
    [relay queue] → pc-officina offline, metti in coda
         │
         ↓ (dopo 2 ore)
    pc-officina si riconnette
         │
         ↓
    relay recapita il comando
         │
         ↓
    agent esegue e restituisce output a Hermes
```

## Flussi Principali

### 1. Shell Interattiva
```
Hermes → POST /api/agents/{id}/command { "command": "bash", "interactive": true }
Relay → inoltra ad agent
Agent → apre PTY, stream output
Relay → streama output a Hermes
Hermes → invia input successivo
... fino a EOF o timeout
```

### 2. File Transfer
```
Hermes → POST /api/agents/{id}/file/download { "path": "/etc/hosts" }
Relay → inoltra ad agent
Agent → legge file, codifica base64, invia
Relay → recapita a Hermes
```

### 3. Screenshot
```
Hermes → POST /api/agents/{id}/screenshot
Relay → inoltra ad agent
Agent → cattura schermo (Pillow/pyautogui)
Agent → invia immagine base64
Relay → recapita a Hermes
Hermes → analizza con vision (gemma4)
```

## Reconnect e Affidabilita'

- Agent: reconnect con backoff esponenziale (1s → 60s max)
- Heartbeat ogni 30 secondi
- Se il relay cade, agent bufferizza localmente (SQLite) e recapita alla riconnessione
- Se l'agent cade, relay contrassegna come offline e notifica Hermes
- Cloudflare Tunnel: riconnessione automatica con retry

## Note sul Deployment

- **Cloudflare Tunnel**: il traffico passa da `caduceo.maulanhermes.uk` → Cloudflare → `cloudflared` → `localhost:8443`
- **Nessuna porta aperta** sul router: ISP non puo' bloccare, zero superficie di attacco
- **Caddy**: usato solo per sviluppo locale sulla porta 8400
- **JWT Token**: ottenibile solo da localhost tramite `/api/token`
- **PSK**: memorizzata in `~/.caduceo/agent.psk` o env `CADUCEO_PSK`, mai da CLI arg