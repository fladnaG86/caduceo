# Caduceo LOCAL — Deploy su PC di Dominio

Pacchetto per installare **Caduceo** su tutti i PC di dominio in modo **automatico e silenzioso**, usando un **relay locale** nella rete di dominio (senza Tailscale, senza tunnel pubblico).

## Architettura

```
┌─────────────────────────────────────────────────────────┐
│  RETE DI DOMINIO (10.0.0.x / 10.0.10.x / 10.0.20.x) │
│                                                         │
│  ┌──────────────┐    ws://<IP-VM>:8443    ┌───────────┐ │
│  │  VM Ubuntu   │◄────────────────────────│ PC Dominio│ │
│  │  (relay loc.)│                         │ (agente)  │ │
│  └──────────────┘                         └───────────┘ │
│        ▲                                              ▲  │
│        │ GPO Startup Script (SYSTEM)                  │  │
│        └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

- **Relay locale**: VM Ubuntu nella rete di dominio, raggiungibile dai PC 10.0.0.x/10.0.10.x/10.0.20.x
- **Agenti**: installati come **servizio NSSM** (avvio automatico, silenzioso, senza login)
- **Distribuzione**: **GPO Computer Startup Script** — gira come SYSTEM all'avvio di ogni PC

## Componenti

| File | Scopo |
|------|-------|
| `install-relay-local.sh` | Installa il relay locale su una VM Ubuntu (genera PSK/JWT nuovi) |
| `install-agent-gpo.ps1` | Installa l'agente come servizio NSSM su un PC Windows (per GPO) |
| `nssm.exe` | NSSM win64 (da copiare nella share — evita download da internet) |
| `deps/` | **Dipendenze Python vendorizzate** (offline): wheel Windows di websockets, psutil, pywin32, Pillow, **cryptography** (+ cffi, pycparser) + get-pip.py |
| `caduceo_common-0.1.0-py3-none-any.whl` | Libreria condivisa (da copiare) |
| `caduceo_agent-0.1.0-py3-none-any.whl` | Pacchetto agente (da copiare) |

---

## FASE 1 — Installa il relay locale (VM Ubuntu)

Su una VM Ubuntu nella rete di dominio:

```bash
sudo ./install-relay-local.sh --configure-ufw --subnets "10.0.0.0/24,10.0.10.0/24,10.0.20.0/24" --repo-ref <commit-o-tag>
```

- `--configure-ufw` abilita e configura UFW (opzionale — non tocca il firewall se omesso)
- `--subnets` limita l'accesso alla porta 8443 alle subnet indicate (usa i CIDR reali della tua rete, non /8)
- `--repo-ref` fissa il commit/tag del repo (default `main` — per produzione usa un commit SHA completo o tag firmato)

Al termine, le chiavi complete sono in `/etc/caduceo/relay.json` (permessi 640, owner root:caduceo). **Le chiavi sono preservate sui rerun** — un aggiornamento dello script non invalida gli agenti già installati.

**Verifica:**
```bash
systemctl status caduceo-relay
curl -s http://localhost:8443/api/health
```

**Relay URL per gli agenti:** `ws://<IP-DI-QUESTA-VM>:8443`

---

## FASE 2 — Prepara la condivisione GPO

1. Crea una condivisione di rete sul Domain Controller (o su un server raggiungibile):
   ```
   \\server\caduceo$
   ```
2. Copia in quella condivisione:
   - `install-agent-gpo.ps1`
   - `nssm.exe` (win64 — evita il download da internet su ogni PC)
   - `deps/` (intera cartella — dipendenze offline per pip)
   - `caduceo_common-0.1.0-py3-none-any.whl`
   - `caduceo_agent-0.1.0-py3-none-any.whl`
3. **IMPORTANTE — Permessi share:** lo script gira come **SYSTEM** (account computer `DOMINIO\PC$`), NON come utente. Assicurati che la share e la cartella NTFS diano **accesso in lettura ai Computer del Dominio** (gruppo "Domain Computers"), altrimenti lo script non troverà i file e fallirà silenziosamente.
4. **Modifica** `install-agent-gpo.ps1` e imposta:
   - `$RelayUrl = "ws://<IP-DELLA-VM-RELAY>:8443"` (IP del relay locale)
   - `$PskHex = "<PSK-DEL-RELAY-LOCALE>"` (PSK generato in FASE 1)

> **⚠️ IMPORTANTE — NON passare il PSK come parametro GPO:** se metti `-PskHex <chiave>` nel campo "Script Parameters" della GPO, il PSK finisce **in chiaro in SYSVOL** (`\\dominio\SysVol\...\Scripts\`), leggibile da tutti gli Authenticated Users. Invece, **imposta `$PskHex` direttamente dentro il file** `install-agent-gpo.ps1` (che sta nella share con permessi ristretti a Domain Computers).

---

## FASE 3 — Configura la GPO (Computer Startup Script)

1. Apri **Group Policy Management** sul Domain Controller
2. Crea una nuova GPO (es. "Caduceo Agent Deploy")
3. Collega la GPO all'OU che contiene i PC di dominio
4. Modifica la GPO:
   - **Computer Configuration → Windows Settings → Scripts (Startup/Shutdown) → Startup**
   - Aggiungi → Script Name: `install-agent-gpo.ps1`
   - Script Parameters: (lascia vuoto — i parametri sono dentro il file)
   - **IMPORTANTE:** nella scheda "Scripts", assicurati che PowerShell sia selezionato (non CMD)
5. **Computer Configuration → Administrative Templates → System → Scripts → Run startup scripts asynchronously** → Enabled (opzionale, accelera il boot)

> **Nota:** gli Startup Script GPO girano come **SYSTEM**, prima del login. Lo script installa l'agente come servizio NSSM che parte da solo.

---

## FASE 4 — Verifica

Dopo che i PC si sono riavviati (o con `gpupdate /force` + riavvio):

1. **Dal relay locale:**
   ```bash
   # Genera token JWT
   curl -s -X POST http://localhost:8443/api/token \
     -H "Content-Type: application/json" \
     -d '{"subject":"admin","role":"admin"}'
   # Elenca agenti
   curl -s http://localhost:8443/api/agents -H "Authorization: Bearer <TOKEN>"
   ```

2. **Su un PC di dominio:**
   ```powershell
   Get-Service CaduceoAgent
   # Deve essere: Running, Automatic
   ```

---

## Risoluzione problemi

| Problema | Causa probabile | Soluzione |
|----------|-----------------|-----------|
| Servizio non creato | GPO non applicata | `gpresult /r` sul PC, verifica collegamento GPO |
| Servizio non parte | PSK errato | Verifica `$PskHex` nel file condiviso |
| Agente offline | Relay non raggiungibile | `Test-NetConnection <IP-VM> -Port 8443` dal PC |
| Errore "non autorizzato" | PSK diverso da relay | Confronta PSK in `agent.json` e `relay.json` |
| Script non eseguito | Execution Policy | GPO Startup usa `-ExecutionPolicy Bypass` |

## Debug

Lo script GPO gira come SYSTEM in background — non mostra output all'utente. Per il debug:

1. **Log dell'installatore** (ogni step critico è loggato):
   ```
   C:\Windows\Temp\caduceo_install.log
   ```
2. **Log dell'agente** (se il servizio è partito):
   ```
   C:\ProgramData\caduceo\agent.log
   C:\ProgramData\caduceo\agent.err.log
   ```
3. **Verifica GPO applicata** sul PC:
   ```powershell
   gpresult /r
   ```
4. **Test manuale dello script** (come SYSTEM, da PowerShell admin):
   ```powershell
   powershell.exe -ExecutionPolicy Bypass -NoProfile -File "\\server\caduceo$\install-agent-gpo.ps1"
   ```

> **Nota:** se i Computer Startup Script girano prima che la rete/DC sia pronta, lo script fa **retry automatico** (fino a 8 tentativi con backoff). Se il problema persiste, abilita la GPO "Always wait for the network at computer startup and logon".

---

## Sicurezza

- **PSK e JWT secret** sono generati **nuovi e separati** dal relay principale — i due sistemi sono indipendenti
- **Il relay gira come utente dedicato `caduceo`** (senza shell) con systemd hardening (`NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`) — blast-radius ridotta in caso di compromissione
- **`relay.json` ha permessi 640** (owner root, gruppo caduceo) — il servizio può leggerlo ma non è esposto a tutti
- **Le chiavi NON sono stampate integralmente** su stdout (journald potrebbe catturarle) — solo gli ultimi 8 caratteri
- **Le chiavi sono preservate sui rerun** — un aggiornamento dello script non invalida gli agenti già installati
- **Firewall UFW** è **opzionale** (`--configure-ufw`) e limitato alle subnet specificate (`--subnets`) — non abilita mai automaticamente un firewall che potrebbe bloccare SSH/management
- **`agent.json` contiene il PSK** — la cartella `C:\ProgramData\caduceo` è ristretta a **SYSTEM + Administrators** (icacls con SID well-known, indipendenti dalla lingua)
- **Windows Defender** esclude **solo la cartella** `C:\ProgramData\caduceo` — NON esclude `python.exe` a livello globale
- **Verifica integrità** — lo script GPO verifica SHA256 delle wheel **PRIMA** di pip install, ed è **obbligatoria** (fallisce se gli hash non sono configurati)
- **Runtime Python dedicato** — lo script usa SEMPRE l'embeddable in `C:\ProgramData\caduceo\python`, mai il Python di sistema
- **Retry download** — Python e NSSM hanno retry con backoff (5 tentativi) per gestire la rete non pronta all'avvio
- **Verifica architettura** — usa `[Environment]::Is64BitOperatingSystem` (affidabile anche con PowerShell 32-bit)
- La condivisione `\\server\caduceo$` usa il `$` per nasconderla dalla navigazione, e dà accesso in lettura solo ai **Computer del Dominio**

## Raccomandazioni per produzione

- **Usa TLS (wss)**: il relay locale gira su HTTP (rete interna). PSK e JWT autenticano ma **non cifrano il payload** — su reti flat, un endpoint compromesso potrebbe sniffare il traffico. Per produzione, metti un reverse proxy (nginx/traefik) con certificati interni davanti al relay, e usa `wss://` negli agenti
- **Pinning del codice**: lo script relay clona il repo da GitHub. Per produzione, imposta `REPO_REF` a un **commit SHA completo o tag firmato** (non un branch mobile)
- **Rotazione chiavi**: documenta e pianifica la rotazione periodica di PSK e JWT secret
- **Policy Defender centralizzate**: se l'ambiente usa EDR gestito centralmente, le esclusioni locali potrebbero essere ignorate — coordina con il team security
- **Compatibilità PowerShell**: lo script richiede **PowerShell ≥ 5.0** (usa `Expand-Archive`). Su Windows 7/Server 2008R2/2012 (PS 2.0-4.0) aggiorna WMF/PowerShell prima del deploy
- **Timeout GPO**: il retry sulle wheel è limitato a ~2 minuti per non superare la policy "Maximum wait time for Group Policy scripts". Se la tua GPO ha un timeout più restrittivo, verificalo
- **Architettura**: il deploy supporta **solo Windows x64** (AMD64) — lo script verifica e termina con errore su ARM64/32-bit
- **Test pilota**: prima del rollout completo, testa su Windows 10/11 e sulle versioni Windows Server supportate, includendo sistema localizzato, boot senza rete, aggiornamento da versione precedente e reinstallazione idempotente
