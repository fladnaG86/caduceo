# I built an open-source remote control system that lets my AI agent access my PCs behind NAT — and it's 11KB

---

**TL;DR:** I got tired of my AI assistant not being able to run commands on my remote machines behind NAT/firewalls. So I built Caduceo — a self-hosted relay + lightweight agent that gives your AI (or yourself) secure API access to any PC, anywhere. The agent is 11KB.

---

## The problem

I run Hermes (a local AI agent) on my home server. I wanted it to be able to:

- Run commands on my work PC (behind a corporate VPN)
- Execute scripts on my server rack in the closet
- Manage machines on different networks without opening ports

Existing tools didn't fit:

- **TeamViewer/AnyDesk** — GUI-only, no API, made for humans
- **MeshCentral** — Great project, but I don't need a web UI. I need an API my AI agent can call.
- **Tactical RMM** — Heavy stack (Django + React + Docker), overkill for what I need
- **Ansible** — No good with NAT. Needs direct SSH access.
- **Teleport** — Excellent for SSH/RDP, but not designed for async command execution or AI integration

What I really wanted was: **an API that my AI can call to run a command on any machine, and get the result back.** With encryption. With an offline queue. Without opening any ports.

## How it works

The architecture is dead simple:

```
┌──────────┐     WebSocket      ┌──────────┐     WebSocket      ┌──────────┐
│  Agent   │◄──────────────────►│  Relay   │◄──────────────────►│  Agent   │
│ (Win PC) │    (outbound)      │ (Linux)  │    (outbound)      │ (Win PC) │
└──────────┘                    └──────────┘                    └──────────┘
                                       │
                                       │ REST API (JWT-authenticated)
                                       │
                                       ▼
                                ┌──────────────┐
                                │  Hermes / You │
                                │  (API client) │
                                └──────────────┘
```

**Key principle: agents connect OUTBOUND to the relay.** No ports to open, no NAT traversal hacks, no port forwarding. The relay sits on a server with a public URL (I use Zrok, could be any VPS/cloud).

### Step by step:

1. **Agent starts on remote PC** → Connects via WebSocket to the relay (outbound, so no firewall issues)
2. **Agent authenticates** → PSK challenge-response (HMAC-SHA256 with server-provided nonce). The PSK never crosses the wire.
3. **All messages are encrypted** → AES-256-GCM with random nonce per message. Even if TLS is stripped, the payload is encrypted.
4. **You (or your AI) send a command** → `POST /api/agents/{agent_id}/command` with a JWT token
5. **Agent executes it** → Gets the result, sends it back through the relay
6. **You get the response** → JSON with exit_code, stdout, stderr

### What happens if the agent is offline?

The relay has a **SQLite-backed offline queue** (24h TTL, max 100 commands per agent). You send a command, it gets queued. When the agent reconnects, the relay delivers all pending commands and returns the results.

This was critical for me — I want to tell my AI "reboot that server and install updates" at 2 AM, and get the result in the morning.

## The agent is actually 11KB

The compiled wheel for `caduceo_agent` is 11,763 bytes. No, that's not a typo.

```
caduceo_agent-0.1.0-py3-none-any.whl  11,763 bytes
caduceo_common-0.1.0-py3-none-any.whl   6,857 bytes
```

It runs on Python embeddable (no installation needed). The self-contained installer is a single PowerShell script that:

1. Downloads Python 3.12 embeddable (if not present)
2. Installs the two wheels from base64 encoded data embedded in the script
3. Writes the config (PSK, relay URL)
4. Creates a Scheduled Task for auto-start

You deploy a new machine by running **one command** in PowerShell:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
iex ((New-Object Net.WebClient).DownloadString('https://your-relay.com/download/caduceo-setup.ps1'))
```

That's it. No Docker, no Node.js, no dependencies to manage. The agent just works.

## Security (because I know r/selfhosted will ask)

- **PSK challenge-response** — Server sends a random 32-byte nonce. Agent computes `HMAC-SHA256(psk, nonce || agent_id)`. PSK never transmitted.
- **AES-256-GCM encryption** on all payloads after auth. Each message gets a fresh random nonce. Even if someone intercepts the WebSocket stream, they can't read your commands.
- **JWT authentication** on the REST API. Tokens expire in 1 hour. Token generation is localhost-only.
- **Encrypted heartbeats** — Even keepalive messages are encrypted. No metadata leakage.
- **Path guards** — Agents have an `ALLOWED_PATHS` whitelist for file operations. Can't read `/etc/shadow` or `C:\Windows\System32\config\SAM`.
- **Audit logging** — Every command, file transfer, and auth event is logged to SQLite.

## What I use it for

My AI agent (Hermes) now has full access to my machines:

```python
# Check system info on my work PC
POST /api/agents/O-MASSIMO/command
{"command": "systeminfo | findstr OS", "timeout": 15}

# Install software remotely
POST /api/agents/WIN-FOR-ACCESS/command  
{"command": "winget install VLC.VLC", "timeout": 60}

# Upload a file to a remote machine
POST /api/agents/O-MASSIMO/upload
{"destination": "C:\\Users\\user\\Desktop\\report.pdf", "content": "<base64>"}
```

From my AI assistant's perspective, it's just another API. From the machines' perspective, it's a tiny background process that phones home over WebSocket.

I currently have two Windows machines connected (one behind a corporate VPN, one on a different LAN) and I can run commands on both from my AI agent, my scripts, or curl.

## Current state

- **v0.1.0-alpha** — Works, in daily use, but still rough edges
- **Windows agent** — fully functional, production-tested
- **Linux agent** — code exists but not battle-tested yet  
- **Relay** — FastAPI + Uvicorn, ~930 lines, very stable
- **Common library** — Crypto, protocol, messages, ~860 lines
- **Total Python** — ~3,800 lines across all packages

## What's next

- Linux/ARM agent support (for Raspberry Pi, VPS, edge devices)
- File transfer improvements (progress tracking, resumable uploads)
- Screenshot capability
- Web dashboard (minimal, for humans who want to peek)
- Agent auto-update through the relay

---

**Would love feedback on:**
- What features would make you actually use this?
- Is the "AI agent control" angle interesting, or would you rather see this positioned as a lightweight RMM?
- Any security concerns with the architecture?

If anyone's interested, I can share the repo. It's MIT-licensed.

---

*Edit: Some people asked about the offline queue behavior. If you send a command while the agent is offline, the relay returns a `command_id` immediately. You can then poll `GET /api/commands/{command_id}` to check if the agent picked it up and get the result. Or you can configure webhooks to get notified.*

*Edit 2: For the "but what about SSH reverse tunnels?" people — yes, you can do `ssh -R` and it works. But then you need SSH keys, port management, you're piping raw shell, no encryption beyond TLS, no offline queue, no API, no multi-agent management. Caduceo gives you all of that in a single deploy command.*