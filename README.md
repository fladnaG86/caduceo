<h1 align="center">⚕️ Caduceo</h1>

<p align="center"><strong>The AI-first remote agent control plane</strong></p>

<p align="center">
  <em>The staff of Hermes that reaches every world.</em>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#security">Security</a> •
  <a href="#api">API</a> •
  <a href="#contributing">Contributing</a>
</p>

---

Caduceo is a self-hosted system that lets you (or your AI agent) execute commands and transfer files on remote machines **behind NAT and firewalls** — through a single REST API.

No open ports. No VPN. No GUI required. Just an API.

```
┌──────────┐     WebSocket      ┌──────────┐     WebSocket      ┌──────────┐
│  Agent   │◄──────────────────►│  Relay   │◄──────────────────►│  Agent   │
│ (Win PC) │    (outbound)      │ (Linux)  │    (outbound)      │ (Mac)    │
└──────────┘                    └──────────┘                    └──────────┘
                                       │
                                       │ REST API (JWT)
                                       │
                                       ▼
                                ┌──────────────┐
                                │  You / Your   │
                                │  AI Agent     │
                                └──────────────┘
```

## Why Caduceo?

I built this because I needed my AI assistant to run commands on my machines across different networks — work PC behind a corporate VPN, home server, laptop on WiFi. Existing tools are built for humans clicking through GUIs. I needed **an API my AI can call**.

| | Caduceo | MeshCentral | TeamViewer | Ansible | Teleport |
|---|---|---|---|---|---|
| API-first design | ✅ | ❌ | ❌ | Partial | Partial |
| NAT traversal | ✅ (outbound WS) | ✅ | ✅ | ❌ | ✅ |
| Offline command queue | ✅ (SQLite) | Partial | ❌ | ❌ | ❌ |
| E2E encrypted payloads | ✅ (AES-256-GCM) | TLS only | TLS only | SSH only | mTLS |
| Agent size | **11 KB** | ~5 MB | ~20 MB | Agentless | ~30 MB |
| Self-hosted | ✅ | ✅ | ❌ | ✅ | ✅ |
| Zero dependencies | ✅ | Node.js | Binary | Python/SSH | Go |
| Designed for AI agents | ✅ | ❌ | ❌ | ❌ | ❌ |

## Features

- **🔌 NAT Traversal** — Agents connect outbound via WebSocket. No port forwarding, no VPN.
- **📡 REST API** — Send commands, get results. JWT-authenticated. Built for automation.
- **📨 Offline Queue** — Commands queue in SQLite when agents are offline. Delivered on reconnect. 24h TTL, 100 cmds/agent.
- **🔐 E2E Encryption** — AES-256-GCM with random nonce per message. Payloads are encrypted even if TLS is stripped.
- **🔑 PSK Authentication** — Challenge-response with HMAC-SHA256. The PSK never crosses the wire.
- **📦 11KB Agent** — The entire agent wheel is 11,763 bytes. Runs on Python embeddable. Zero system dependencies.
- **🖥️ Cross-Platform** — Windows, Linux, macOS agents. Relay runs on any Python 3.11+ host.
- **📋 Audit Logging** — Every command, file transfer, and auth event logged to SQLite.
- **⚡ File Transfer** — Upload and download files to/from remote agents.
- **🤖 AI Agent Ready** — Built to be controlled by AI systems (Hermes, LangChain, AutoGen, etc.)

## Quick Start

### 1. Start the relay

```bash
pip install -e ./caduceo-relay
caduceo-relay --host 0.0.0.0 --port 8443
```

The relay creates `~/.caduceo/relay.json` with a generated PSK and JWT secret on first run.

### 2. Install an agent on a remote machine

**Windows (one command, no Python needed):**

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
iex ((New-Object Net.WebClient).DownloadString('https://your-relay.com/download/caduceo-setup.ps1'))
```

The self-contained installer:
- Downloads Python 3.12 embeddable if not present
- Installs both wheels from embedded base64
- Configures PSK and relay URL
- Creates a Scheduled Task for auto-start

**Linux/macOS:**

```bash
pip install -e ./caduceo-agent
caduceo-agent --config agent.json
```

### 3. Send commands via API

```bash
# Get a JWT token (localhost only)
TOKEN=$(curl -s -X POST http://localhost:8443/api/token \
  -H "Content-Type: application/json" \
  -d '{"subject": "admin", "role": "admin"}' | jq -r .token)

# List connected agents
curl -s http://localhost:8443/api/agents \
  -H "Authorization: Bearer $TOKEN"

# Run a command on a remote agent
curl -s -X POST http://localhost:8443/api/agents/my-laptop/command \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"command": "whoami", "timeout": 10}'

# Upload a file
curl -s -X POST http://localhost:8443/api/agents/my-laptop/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"destination": "/home/user/report.pdf", "content": "<base64>"}'
```

### 4. Expose via public URL (optional)

For agents on different networks, expose the relay with [Zrok](https://zrok.io/), Cloudflare Tunnel, or any reverse proxy:

```bash
zrok share public http://localhost:8443 --headless --name-selection public:caduceo
```

## Architecture

### Components

| Component | Purpose | Lines of Code |
|-----------|---------|---------------|
| `caduceo-relay` | FastAPI + WebSocket server | ~930 |
| `caduceo-agent` | Lightweight remote client | ~1040 |
| `caduceo-common` | Protocol, crypto, utilities | ~860 |
| **Total** | | **~2830** |

### How it works

1. **Agent connects** — The agent opens an outbound WebSocket to the relay. Since it's an outbound connection, it works behind any NAT/firewall.
2. **PSK handshake** — Server sends a random 32-byte nonce. Agent computes `HMAC-SHA256(psk, nonce || agent_id)`. The PSK is never transmitted.
3. **Encrypted channel** — All subsequent messages use AES-256-GCM with a fresh random nonce per message.
4. **REST API** — You (or your AI) send commands through the relay's JWT-authenticated REST API.
5. **Command routing** — The relay forwards the encrypted command to the target agent via WebSocket.
6. **Response** — Agent executes the command, encrypts the result, and sends it back through the relay.
7. **Offline support** — If the agent is disconnected, commands are queued in SQLite. When the agent reconnects, pending commands are delivered.

### Message Flow

```
Client                  Relay                  Agent
  │                       │                       │
  │── POST /command ────►│                       │
  │                       │── WS: CMD_ENCRYPTED ─►│
  │                       │                       │── executes
  │                       │◄─ WS: RESULT_ENCRYPTED─│
  │◄── JSON response ─────│                       │
  │                       │                       │
```

## Security

| Layer | Mechanism |
|-------|-----------|
| Authentication | PSK challenge-response (HMAC-SHA256 with server nonce) |
| Payload encryption | AES-256-GCM with random 96-bit nonce per message |
| Transport | TLS (via Zrok/reverse proxy) |
| API auth | JWT HS256, 1-hour expiry, tokens issued localhost-only |
| Agent file access | `ALLOWED_PATHS` whitelist, `DENIED_PATTERNS` blocklist |
| Heartbeat | Encrypted, not plaintext |
| Audit | All actions logged to SQLite (`~/.caduceo/audit.db`) |

The PSK is **never transmitted**. The server sends a random challenge nonce, and the agent proves knowledge of the PSK by computing HMAC-SHA256 over `(nonce || agent_id)`. Even if an attacker intercepts the WebSocket stream, they cannot decrypt payload content (AES-256-GCM) or replay messages (nonce-based).

## API

### Authentication

```bash
# Get JWT token (localhost-only endpoint)
POST /api/token
Body: {"subject": "hermes", "role": "admin"}
Response: {"token": "eyJ...", "expires_at": "2025-01-01T00:00:00Z"}
```

### Commands

```bash
# List connected agents
GET /api/agents

# Get agent details
GET /api/agents/{agent_id}

# Execute a command
POST /api/agents/{agent_id}/command
Body: {"command": "whoami", "timeout": 10}
# or with args:
Body: {"command": "cmd", "args": ["/c", "dir C:\\Users"], "timeout": 10}

# Upload a file
POST /api/agents/{agent_id}/upload
Body: {"destination": "C:\\Users\\user\\file.txt", "content": "<base64>"}

# Download a file
GET /api/agents/{agent_id}/download?path=C:\\Users\\user\\file.txt
```

### Download endpoints (public, no auth)

```bash
GET /download/caduceo-setup.ps1      # Self-contained Windows installer
GET /download/caduceo-setup.bat      # Batch launcher for the PS1
GET /download/install.py?platform=windows  # Python installer
GET /download/caduceo_common.whl     # Common library wheel
GET /download/caduceo_agent.whl      # Agent wheel
```

## Configuration

### Relay (`~/.caduceo/relay.json`)

```json
{
  "host": "0.0.0.0",
  "port": 8443,
  "psk_hex": "<auto-generated>",
  "jwt_secret": "<auto-generated>",
  "queue_ttl": 86400
}
```

### Agent (`~/.caduceo/agent.json`)

```json
{
  "relay_url": "wss://your-relay.example.com/agent",
  "psk_hex": "<shared-psk>",
  "agent_id": "my-laptop",
  "tags": ["caduceo"]
}
```

## Platform Support

| Platform | Shell | Service Manager | Status |
|----------|-------|----------------|--------|
| Windows 10/11, Server 2019+ | cmd.exe / PowerShell | Scheduled Task | ✅ Production |
| Linux (systemd) | bash | systemd | ✅ Tested |
| macOS | zsh | launchd | ✅ Tested |

## Project Structure

```
caduceo/
├── caduceo-relay/          # FastAPI + WebSocket server
│   └── caduceo_relay/
│       └── server.py       # ~930 lines
├── caduceo-agent/          # Lightweight remote agent
│   └── caduceo_agent/
│       └── client.py       # ~1040 lines
├── caduceo-common/         # Shared libraries
│   └── caduceo_common/
│       ├── constants.py    # Protocol constants
│       ├── crypto.py      # AES-256-GCM, HMAC
│       ├── messages.py    # Message dataclasses
│       ├── protocol.py    # WebSocket protocol
│       └── utils.py       # Helpers
├── deploy/                # Installer scripts & wheels
│   ├── caduceo-setup.ps1  # Self-contained Windows installer
│   ├── caduceo-setup.bat  # Batch launcher
│   └── ...
├── docs/
│   └── architecture.md
├── tests/
│   └── test_caduceo.py    # 43 tests
├── pyproject.toml
└── README.md
```

## Running Tests

```bash
python -m pytest tests/test_caduceo.py -v
# 43 tests: crypto, messages, security (PSK challenge, path traversal, 
# overwrite protection, falsy values), command queue, audit logging
```

## Comparison with Alternatives

### vs. SSH Reverse Tunnels
`ssh -R` works, but you need SSH keys, port management, raw shell (no encryption beyond TLS), no offline queue, no multi-agent management, no API. Caduceo gives you all of that in a single deploy command.

### vs. MeshCentral
MeshCentral is excellent for GUI-based remote management. Caduceo is API-first — designed for programmatic access, scripting, and AI agent integration. If you need a web dashboard, MeshCentral is great. If you need a REST API, Caduceo is the answer.

### vs. Tactical RMM
Tactical RMM is a full-featured RMM with monitoring, patching, and scripting. It requires Django, React, and Docker. Caduceo is 2,800 lines of Python with zero heavy dependencies. Different scope — Caduceo focuses on the API/command layer.

### vs. Teleport
Teleport is a zero-trust access plane for infrastructure (SSH, Kubernetes, databases). Caduceo is for lightweight remote command execution on endpoint machines, with an offline command queue and AI-first design. Different use case.

## Use Cases

- **AI Agent Control** — Let your AI assistant (Hermes, LangChain, AutoGen) manage remote machines
- **Home Lab Management** — Run commands on PCs, servers, and VMs across different networks
- **Remote Support** — Deploy the 11KB agent instantly on a customer's machine
- **Edge/IoT Management** — Lightweight enough for Raspberry Pi and thin clients
- **CI/CD Pipeline** — Trigger remote builds, deployments, and health checks
- **Incident Response** — Execute remediation commands on machines behind corporate firewalls

## Roadmap

- [ ] Linux ARM support (Raspberry Pi, Jetson)
- [ ] Screenshot capability
- [ ] Minimal web dashboard
- [ ] Agent auto-update through relay
- [ ] File transfer progress and resumable uploads
- [ ] MCP (Model Context Protocol) integration
- [ ] Webhook notifications for agent events
- [ ] Multi-tenancy (multiple organizations)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <em>Caduceo — the staff of Hermes, reaching every world.</em>
</p>