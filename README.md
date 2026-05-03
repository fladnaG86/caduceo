# Caduceo

Il bastone di Hermes che raggiunge ogni mondo.

Sistema di accesso remoto per agente AI. Consente a Hermes di collegarsi a PC sparsi su reti diverse (dietro NAT/firewall) e di eseguire comandi shell, trasferire file, e ottenere informazioni di sistema.

## Architettura

```
[PC Remoto]                        [Tuo Server]                    [Tu]
┌──────────────┐   outbound WSS   ┌──────────────────┐            ┌──────────┐
│ caduceo-agent │ ───────────────→ │  caduceo-relay   │ ←───────── │  Hermes   │
│  (service)   │ ←─────────────── │  maulanhermes.uk │  REST API  │  Gateway  │
└──────────────┘   comand/response└──────────────────┘            └──────────┘
```

- **caduceo-agent**: Leggero client Python. Si installa sui PC remoti, si connette in outbound al relay via WebSocket. Supporta Windows, Linux, macOS.
- **caduceo-relay**: Server centrale sul tuo VPS. Accetta connessioni dagli agent, espone REST API per Hermes, instrada comandi.
- **caduceo-common**: Librerie condivise (protocollo, crittografia, serializzazione).
- **caduceo-hermes**: Skill/integrazione per Hermes Agent.

## Quick Start

```bash
# Relay (sul server)
pip install -e ./caduceo-relay
caduceo-relay --host 0.0.0.0 --port 8443

# Agent (sui PC remoti)
pip install -e ./caduceo-agent
caduceo-agent --relay wss://maulanhermes.uk:8443 --token SECRET
```

## Supporto Piattaforma

| Piattaforma | Shell | Service | Stato |
|-------------|-------|---------|-------|
| Linux       | bash  | systemd | ✅ |
| macOS       | zsh   | launchd | ✅ |
| Windows     | PowerShell | Win Service | ✅ |

## Licenza

MIT