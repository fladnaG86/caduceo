# Caduceo - Architecture

## Overview

Caduceo è composto da 3 componenti principali che comunicano tramite un protocollo binario su WebSocket crittografato.

```
┌─────────────────────────────────────────────────────────────────┐
│                        CADUCEO ARCHITECTURE                     │
│                                                                  │
│  ┌──────────┐         ┌──────────────┐         ┌──────────┐   │
│  │  HERMES   │  HTTP/  │     RELAY     │  WSS/   │  AGENT   │   │
│  │  Gateway  │ ←REST──│   SERVER      │ ←──────│  (PC)    │   │
│  │          │         │              │         │          │   │
│  │ - Invia   │         │ - Registry   │         │ - Shell  │   │
│  │   cmd     │ ──→     │ - Routing    │   ──→   │ - File   │   │
│  │ - Riceve  │         │ - Auth       │         │ - Info   │   │
│  │   output  │ ←─      │ - Queue      │   ←─    │ - Heartb │   │
│  └──────────┘         └──────────────┘         └──────────┘   │
│                                                                  │
│  Comunicazione: JSON frames su WebSocket (agent) e REST API (hermes) │
│  Crittografia: AES-256-GCM con chiave pre-condivisa (PSK)       │
│  Autenticazione: Token-based (HS256 JWT)                         │
└─────────────────────────────────────────────────────────────────┘
```

## Protocollo di Comunicazione

### Agent → Relay (WebSocket)

Il client si connette in outbound a `wss://relay:8443/agent` e mantiene la connessione aperta con heartbeat.

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

## Crittografia

```
Chiave pre-condivisa (PSK) generata durante setup relay
  │
  ├── AES-256-GCM per payload WebSocket (agent ↔ relay)
  ├── TLS 1.3 per trasporto (WSS)
  └── JWT HS256 per autenticazione API (Hermes ↔ relay)
```

Ogni payload WebSocket è crittografato con AES-256-GCM. Il nonce è incluso nel frame. La PSK è configurata sia nell'agent che nel relay.

## Installsione come Servizio

### Linux (systemd)
```bash
caduceo-agent install --relay wss://maulanhermes.uk:8443 --token SECRET
# Crea /etc/systemd/system/caduceo-agent.service
```

### macOS (launchd)
```bash
caduceo-agent install --relay wss://maulanhermes.uk:8443 --token SECRET
# Crea ~/Library/LaunchAgents/com.caduceo.agent.plist
```

### Windows (Windows Service)
```powershell
caduceo-agent install --relay wss://maulanhermes.uk:8443 --token SECRET
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
Hermes → invia inputsuccessivo
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

## Reconnect e Affidabilità

- Agent: reconnect con backoff esponenziale (1s → 60s max)
- Heartbeat ogni 30 secondi
- Se il relay cade, agent bufferizza localmente (SQLite) e recapita alla riconnessione
- Se l'agent cade, relay contrassegna come offline e notifica Hermes