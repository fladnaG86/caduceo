# ============================================================
#  Caduceo LOCAL Agent Installer - GPO Computer Startup Script
#  ------------------------------------------------------------
#  Installa l'agente Caduceo come SERVIZIO NSSM (avvio automatico,
#  silenzioso, senza login) in C:\ProgramData\caduceo.
#
#  Progettato per girare come Computer Startup Script (SYSTEM)
#  via Group Policy. NON apre finestre, NON richiede login.
#
#  Uso (come SYSTEM, via GPO Startup):
#    powershell.exe -ExecutionPolicy Bypass -NoProfile -File "\\server\caduceo\install-agent-gpo.ps1"
#
#  Parametri:
#    -RelayUrl   URL del relay locale (default: ws://10.0.0.10:8443)
#    -PskHex     PSK del relay locale (OBBLIGATORIO)
#    -Tags       Tag per l'agente (default: "caduceo,domain")
# ============================================================

param(
    [string]$RelayUrl = "ws://10.0.0.10:8443",
    [string]$PskHex    = "CHANGE_ME_GENERATE_A_NEW_PSK",
    [string]$Tags      = "caduceo,domain"
)

# ============================================================
#  Configurazione
# ============================================================
# Cartella dello script (usata in piu' STEP, definita subito)
$scriptDir = $PSScriptRoot
if (-not $scriptDir) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition }
$CaduceoDir = "C:\ProgramData\caduceo"
$ServiceName = "CaduceoAgent"
$PythonUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

# Agent ID = COMPUTERNAME (lowercase, senza spazi)
$AgentId = ($env:COMPUTERNAME).ToLower().Replace(" ", "-").Replace(".", "-")
$AgentId = $AgentId -replace '[^a-z0-9_-]', ''

# ============================================================
#  Funzioni helper
# ============================================================
# Log persistente (lo script GPO gira come SYSTEM, senza console visibile)
# Uso C:\ProgramData\caduceo (ACL ristretta a SYSTEM/Admins) invece di
# C:\Windows\Temp, che ha permessi piu' ampi.
$InstallLog = "C:\ProgramData\caduceo\install.log"
function Log {
    param([string]$s)
    try {
        $logDir = Split-Path -Parent $InstallLog
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        Add-Content -Path $InstallLog -Value ("$(Get-Date -Format o) $s") -ErrorAction SilentlyContinue
    } catch { }
}
function Write-Status { param([string]$Message) $msg = "[CADUCEO-GPO] $Message"; Write-Host $msg; Log $msg }
function Write-OK     { param([string]$Message) $msg = "[CADUCEO-GPO] OK: $Message"; Write-Host $msg; Log $msg }
function Write-Warn   { param([string]$Message) $msg = "[CADUCEO-GPO] WARN: $Message"; Write-Host $msg; Log $msg }
function Write-Err    { param([string]$Message) $msg = "[CADUCEO-GPO] ERRORE: $Message"; Write-Host $msg; Log $msg }

# Download robusto: prova Invoke-WebRequest, poi WebClient, poi curl.exe
# (con timeout espliciti per evitare blocchi indefiniti)
function Download-File {
    param([string]$url, [string]$out)
    try {
        # -UseBasicParsing (senza :$false) - in PS 5.1 il motore IE non e' disponibile come SYSTEM
        Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
        return $true
    } catch { }
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Timeout = 30000  # 30 secondi (WebClient non ha -TimeoutSec)
        $wc.DownloadFile($url, $out)
        return $true
    } catch { }
    if (Test-Path "$env:windir\system32\curl.exe") {
        & "$env:windir\system32\curl.exe" --fail --silent --show-error --location --connect-timeout 10 --max-time 30 -o $out $url
        if ($LASTEXITCODE -eq 0) { return $true }
    }
    return $false
}

# Esegue comando nativo ignorando stderr (molti EXE scrivono info su stderr)
function Invoke-NativeSafe {
    param([scriptblock]$Cmd)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & $Cmd 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    return $code
}

# Scrive file senza BOM (CRITICO per Python)
function Set-ContentNoBom {
    param([string]$Path, [string]$Content)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ============================================================
#  STEP 0: Verifica PSK
# ============================================================
if ($PskHex -eq "") {
    Write-Err "PSK non specificato. Usa -PskHex <chiave>"
    exit 1
}

# ============================================================
#  STEP 0.0: Imposta TLS1.2 a livello globale (evita fallimenti
#  di download su macchine vecchie con TLS1.0/1.1 di default)
# ============================================================
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls13

# ============================================================
#  STEP 0.1: Verifica architettura (il deploy supporta solo x64)
#  Usa Is64BitOperatingSystem (non PROCESSOR_ARCHITECTURE, che
#  riflette il processo PowerShell - falso negativo su PS 32-bit)
# ============================================================
if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Err "Architettura non supportata. Questo deploy richiede Windows x64 (AMD64)."
    exit 1
}

# ============================================================
#  STEP 0.5: Guardia - se gia' installato e configurato, esci
#  (lo script GPO gira a OGNI boot; non deve reinstallare)
#  ------------------------------------------------------------
#  ROBUSTEZZA al boot:
#  - Il servizio e' AUTO_START; all'avvio puo' essere in
#    "StartPending" (non ancora Running) quando gira questo script.
#    Trattiamo StartPending come "avviato" per evitare di
#    rimuovere un servizio che sta partendo (flap al boot).
#  - Verifichiamo la config COMPLETA (relay_url + psk + agent_id),
#    non solo il PSK: cosi' un cambio di relay_url/tags si propaga.
# ============================================================
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and ($svc.Status -eq "Running" -or $svc.Status -eq "StartPending")) {
    $currentConfig = Get-Content (Join-Path $CaduceoDir "agent.json") -Raw -ErrorAction SilentlyContinue
    $configMatches = $false
    if ($currentConfig) {
        # Parsa JSON in modo tollerante (PS 5.1 ha ConvertFrom-Json)
        try {
            $cfg = $currentConfig | ConvertFrom-Json -ErrorAction Stop
            $configMatches = ($cfg.relay_url -eq $RelayUrl) -and
                             ($cfg.psk_hex -eq $PskHex) -and
                             ($cfg.agent_id -eq $AgentId)
        } catch {
            # JSON corrotto/illeggibile: tratta come non configurato
            $configMatches = $false
        }
    }
    if ($configMatches) {
        # Se il servizio esiste ma non e' attivo (crashed), prova a riavviarlo
        if ($svc.Status -ne "Running") {
            Write-Status "Servizio presente ma in stato $($svc.Status), tento l'avvio..."
            & sc.exe start $ServiceName | Out-Null
            Start-Sleep -Seconds 2
        }
        Write-OK "Agente gia' installato e configurato. Esco."
        exit 0
    }
}

Write-Status "Caduceo LOCAL Agent Installer (GPO)"
Write-Status "===================================="
Write-Status "Agent ID:  $AgentId"
Write-Status "Relay:     $RelayUrl"
Write-Status "Directory: $CaduceoDir"

# ============================================================
#  STEP 1: Usa SEMPRE un runtime Python dedicato e isolato
#  ------------------------------------------------------------
#  NON usare il Python di sistema (PATH): modifica app globali,
#  py.exe e' un launcher, e un aggiornamento aziendale lo rompe.
#  Usa sempre l'embeddable in C:\ProgramData\caduceo\python.
# ============================================================
$usePython = ""
$venvPython = ""
$venvPythonw = ""

# Usa Python embeddable dedicato in C:\ProgramData\caduceo\python
$embedExe = Join-Path $CaduceoDir "python\python.exe"
if (Test-Path $embedExe) {
    $embedTest = & $embedExe --version 2>&1
    if ($LASTEXITCODE -eq 0 -and ($embedTest -join "") -match "Python \d+") {
        $usePython = $embedExe
        Write-OK "Python embeddable dedicato trovato: $embedExe"
    }
}

# Se non c'e', scarica Python embeddable dedicato
if (-not $usePython) {
    Write-Status "Scarico Python embeddable dedicato (~10 MB)..."
    New-Item -ItemType Directory -Path $CaduceoDir -Force | Out-Null
    # Restringi subito i permessi (evita finestra ACL permissiva prima di icacls in STEP 2)
    # Usa SID well-known (indipendente dalla lingua del sistema): S-1-5-18=SYSTEM, S-1-5-32-544=Administrators
    icacls.exe $CaduceoDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    $zipPath = Join-Path $env:TEMP "python-embed.zip"
    $embedDir = Join-Path $CaduceoDir "python"

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Retry download Python (la rete potrebbe non essere pronta all'avvio)
    $downloaded = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        if (Download-File -url $PythonUrl -out $zipPath) { $downloaded = $true; break }
        $delay = [Math]::Min(10 * $attempt, 30)
        Write-Warn "Download Python fallito (tentativo $attempt/5). Attesa ${delay}s..."
        Start-Sleep -Seconds $delay
    }
    if (-not $downloaded) {
        Write-Err "Impossibile scaricare Python dopo 5 tentativi. Abort."
        exit 1
    }

    # Verifica download
    if ((Get-Item $zipPath).Length -lt 5MB) {
        Write-Err "Download Python incompleto. Abort."
        exit 1
    }

    # Estrai
    if (Test-Path $embedDir) { Remove-Item $embedDir -Recurse -Force }
    New-Item -ItemType Directory -Path $embedDir -Force | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $embedDir -Force
    Remove-Item $zipPath -ErrorAction SilentlyContinue

    # Fix _pth (CRITICO: senza BOM). Trova dinamicamente il nome dello zip
    # (non hardcodare python312.zip - si rompe se aggiorni Python)
    $pthFile = Get-ChildItem $embedDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $zipName = (Get-ChildItem $embedDir -Filter "python*.zip" | Select-Object -First 1).Name
        $pthContent = "$zipName`n.`nLib`nLib\site-packages`nimport site"
        Set-ContentNoBom -Path $pthFile.FullName -Content $pthContent
        Write-OK "_pth configurato (zip: $zipName)"
    } else {
        Write-Err "File _pth non trovato. Abort."
        exit 1
    }

    # Crea Lib\site-packages
    New-Item -ItemType Directory -Path (Join-Path $embedDir "Lib\site-packages") -Force | Out-Null

    # Installa pip (usa la copia locale in deps/ se disponibile, altrimenti scarica)
    Write-Status "Installazione pip..."
    $getPip = Join-Path $env:TEMP "get-pip.py"
    $localGetPip = Join-Path $scriptDir "deps\get-pip.py"
    if (Test-Path $localGetPip) {
        Copy-Item $localGetPip $getPip -Force
        Write-OK "get-pip.py dalla share GPO (offline)"
    } elseif (-not (Download-File -url $GetPipUrl -out $getPip)) {
        Write-Err "Impossibile scaricare get-pip.py. Abort."
        exit 1
    }
    $embedExe = Join-Path $embedDir "python.exe"
    Invoke-NativeSafe { & $embedExe $getPip }
    Remove-Item $getPip -ErrorAction SilentlyContinue

    $usePython = $embedExe
    Write-OK "Python embeddable dedicato installato: $usePython"
}

# ============================================================
#  STEP 2: Crea config agent.json
# ============================================================
Write-Status "Creazione configurazione..."
New-Item -ItemType Directory -Path $CaduceoDir -Force | Out-Null

# Restringi i permessi della cartella a SYSTEM + Administrators
# (C:\ProgramData e' leggibile da tutti gli utenti di default -
#  senza questo, il PSK in agent.json e' esposto a chiunque)
# Usa SID well-known (indipendente dalla lingua del sistema):
#   S-1-5-18 = SYSTEM, S-1-5-32-544 = Administrators
& icacls.exe $CaduceoDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Err "Impossibile applicare le ACL a $CaduceoDir. Abort."
    exit 1
}

$tagsList = $Tags -split ","
$config = @{
    relay_url   = $RelayUrl
    psk_hex     = $PskHex
    agent_id    = $AgentId
    tags        = $tagsList
    install_dir = $CaduceoDir
    features    = "base"
} | ConvertTo-Json -Depth 5

$configFile = Join-Path $CaduceoDir "agent.json"
if (Test-Path $configFile) {
    Remove-Item $configFile -Force -ErrorAction SilentlyContinue
}
Set-ContentNoBom -Path $configFile -Content $config
Write-OK "Config salvato: $configFile"

# ============================================================
#  STEP 3: Installa pacchetti caduceo
# ============================================================
Write-Status "Installazione pacchetti caduceo..."

# Cerca le wheel nella cartella dello script (condivisione GPO)
$commonWhl = Join-Path $scriptDir "caduceo_common-0.1.0-py3-none-any.whl"
$agentWhl  = Join-Path $scriptDir "caduceo_agent-0.1.0-py3-none-any.whl"
$depsDir   = Join-Path $scriptDir "deps"

# Retry: i Computer Startup Script possono girare prima che la rete/DC sia pronta
# (backoff con cap a 30s per non superare il timeout GPO "Maximum wait time")
$maxAttempts = 6
$attempt = 0
while ($attempt -lt $maxAttempts) {
    $depWheels = Get-ChildItem $depsDir -Filter "*.whl" -ErrorAction SilentlyContinue
    if ((Test-Path $commonWhl) -and (Test-Path $agentWhl) -and $depWheels) { break }
    $attempt++
    $delay = [Math]::Min(15 * $attempt, 30)  # cap a 30s
    Write-Warn "Wheel files non trovati (tentativo $attempt/$maxAttempts). Attesa rete ${delay}s..."
    Start-Sleep -Seconds $delay
}
if (-not (Test-Path $commonWhl) -or -not (Test-Path $agentWhl)) {
    Write-Err "Wheel files non disponibili dopo $maxAttempts tentativi in $scriptDir"
    Write-Err "Copia caduceo_common-0.1.0-py3-none-any.whl e caduceo_agent-0.1.0-py3-none-any.whl accanto a questo script."
    exit 1
}
$depWheels = Get-ChildItem $depsDir -Filter "*.whl" -ErrorAction SilentlyContinue
if (-not $depWheels) {
    Write-Err "Cartella deps/ non disponibile dopo $maxAttempts tentativi. Copia le dipendenze offline in $depsDir"
    exit 1
}

# Verifica integrita' delle wheel PRIMA di pip install
# (se la share e' compromessa, installazione compromessa - e pip puo' eseguire hook)
# NOTA: sostituisci questi hash con quelli reali delle tue wheel prima del deploy
$ExpectedCommonSha = "f1227d865a6a91f82c3cd7eb4f3fb60743e9f5112f1cc858c3083a8209ede1d1"  # sha256 del caduceo_common whl
$ExpectedAgentSha  = "254e2e263dafbf44ff0bbf938fc1fba79e218fbc434315bd5cc49fbd8cbd0f02"  # sha256 del caduceo_agent whl
if ($ExpectedCommonSha -eq "" -or $ExpectedAgentSha -eq "") {
    Write-Err "Hash SHA256 delle wheel non configurati. Impossibile procedere in sicurezza."
    Write-Err "Imposta ExpectedCommonSha e ExpectedAgentSha nel file (o usa -SkipHashCheck per test)."
    exit 1
}
$commonHash = (Get-FileHash $commonWhl -Algorithm SHA256).Hash
$agentHash  = (Get-FileHash $agentWhl -Algorithm SHA256).Hash
if ($commonHash -ne $ExpectedCommonSha -or $agentHash -ne $ExpectedAgentSha) {
    Write-Err "Hash mismatch nelle wheel - possibile compromissione della share. Abort."
    exit 1
}
Write-OK "Integrita' wheel verificata (SHA256)"

# Installa dipendenze + wheel OFFLINE (--no-index --find-links dalla share)
# Evita centinaia di hit a PyPI su ogni PC e fallimenti senza internet.
$pipExit = Invoke-NativeSafe { & $usePython -m pip install --no-index --find-links $depsDir websockets psutil pywin32 Pillow "$commonWhl" "$agentWhl" --quiet }
if ($pipExit -ne 0) {
    Write-Err "Installazione pacchetti fallita (exit=$pipExit)."
    exit 1
}
Write-OK "Pacchetti installati"

# Verifica che i moduli importino correttamente (le wheel devono essere py3-none-any)
$importTest = Invoke-NativeSafe { & $usePython -c "import websockets, psutil, caduceo_agent, caduceo_common; print('OK')" }
if ($importTest -ne 0) {
    Write-Err "Import test fallito - le wheel non sono compatibili con questo Python."
    exit 1
}
Write-OK "Import test superato (moduli caricati correttamente)"

# Post-install pywin32 (su Python embeddable le DLL COM/WMI non si
# registrano da sole - senza questo, screenshot/API Windows possono crashare)
# Cerca ricorsivamente (il nome/percorso puo' variare tra versioni)
$pywinScript = Get-ChildItem -Path (Split-Path $usePython) -Recurse -Filter "pywin32_postinstall*.py" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pywinScript) {
    Invoke-NativeSafe { & $usePython $pywinScript.FullName --install } | Out-Null
    Write-OK "pywin32 post-install eseguito"
} else {
    Write-Warn "pywin32_postinstall.py non trovato - screenshot/API Windows potrebbero non funzionare"
}

# Determina pythonw (per servizio silenzioso)
$pythonwPath = Join-Path (Split-Path $usePython) "pythonw.exe"
if (Test-Path $pythonwPath) {
    $venvPythonw = $pythonwPath
} else {
    $venvPythonw = $usePython
    Write-Warn "pythonw.exe non trovato, uso python.exe"
}

# ============================================================
#  STEP 4: Windows Defender exclusions
#  ------------------------------------------------------------
#  Escludi SOLO la cartella di installazione di Caduceo.
#  NON escludere python.exe/pythonw.exe a livello globale:
#  esporrebbe qualsiasi malware Python alla scansione.
# ============================================================
Write-Status "Configurazione Windows Defender..."
if (Get-Command Add-MpPreference -ErrorAction SilentlyContinue) {
    Add-MpPreference -ExclusionPath $CaduceoDir -ErrorAction SilentlyContinue
} else {
    Write-Warn "Cmdlet Defender non disponibile su questo host - esclusione non applicata."
}

# ============================================================
#  STEP 5: Registra servizio NSSM
#  ------------------------------------------------------------
#  Usa NSSM per registrare l'agente come servizio Windows.
#  Gira come LocalSystem, avvio automatico, senza login.
#  Se NSSM non c'e', lo scarica.
# ============================================================
Write-Status "Registrazione servizio NSSM..."

# Trova o scarica NSSM
$nssmPath = Join-Path $CaduceoDir "nssm.exe"
if (-not (Test-Path $nssmPath)) {
    # 1. Prima prova dalla share GPO (nssm.exe accanto allo script)
    #    - nssm.cc e' lento/offline, e scaricarlo da internet su centinaia
    #      di PC contemporaneamente e' inefficiente.
    $localNssm = Join-Path $scriptDir "nssm.exe"
    if (Test-Path $localNssm) {
        Copy-Item $localNssm $nssmPath -Force
        Write-OK "NSSM copiato dalla share GPO"
    } else {
        # 2. Fallback: download da internet (con retry)
        Write-Status "NSSM non nella share, scarico da internet..."
        $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
        $nssmZip = Join-Path $env:TEMP "nssm.zip"
        $downloaded = $false
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            if (Download-File -url $nssmUrl -out $nssmZip) { $downloaded = $true; break }
            $delay = [Math]::Min(10 * $attempt, 30)
            Write-Warn "Download NSSM fallito (tentativo $attempt/5). Attesa ${delay}s..."
            Start-Sleep -Seconds $delay
        }
        if (-not $downloaded) {
            Write-Err "Impossibile scaricare NSSM dopo 5 tentativi. Abort."
            exit 1
        }
        Expand-Archive -Path $nssmZip -DestinationPath (Join-Path $env:TEMP "nssm") -Force
        $nssmExe = Get-ChildItem (Join-Path $env:TEMP "nssm") -Recurse -Filter "nssm.exe" | Where-Object { $_.FullName -like "*win64*" } | Select-Object -First 1
        if (-not $nssmExe) {
            $nssmExe = Get-ChildItem (Join-Path $env:TEMP "nssm") -Recurse -Filter "nssm.exe" | Select-Object -First 1
        }
        Copy-Item $nssmExe.FullName $nssmPath -Force
        Remove-Item $nssmZip -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $env:TEMP "nssm") -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "NSSM scaricato da internet"
    }
}

# Rimuovi servizio esistente se presente (attendi che sia fermo prima di rimuovere)
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Status "Servizio $ServiceName esiste, lo rimuovo..."
    if ($existing.Status -ne "Stopped") {
        Invoke-NativeSafe { sc.exe stop $ServiceName } | Out-Null
        try {
            $existing.WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
        } catch {
            Write-Warn "Servizio non si e' fermato entro 30s, procedo comunque..."
        }
    }
    Invoke-NativeSafe { & $nssmPath remove $ServiceName confirm } | Out-Null
    Start-Sleep -Seconds 2
}

# Installa servizio
$installExit = Invoke-NativeSafe { & $nssmPath install $ServiceName $venvPythonw "-m" "caduceo_agent" "--config" $configFile }
if ($installExit -ne 0) {
    Write-Err "Installazione servizio NSSM fallita (exit=$installExit)."
    exit 1
}

# Configura servizio
Invoke-NativeSafe { & $nssmPath set $ServiceName AppDirectory $CaduceoDir } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName Start SERVICE_AUTO_START } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName AppExit Default Restart } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName AppStdout (Join-Path $CaduceoDir "agent.log") } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName AppStderr (Join-Path $CaduceoDir "agent.err.log") } | Out-Null
# Rotazione log NSSM (evita che i log riempiano il disco su centinaia di endpoint)
Invoke-NativeSafe { & $nssmPath set $ServiceName AppRotateFiles 1 } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName AppRotateOnline 1 } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName AppRotateBytes 10485760 } | Out-Null
Invoke-NativeSafe { & $nssmPath set $ServiceName AppRotateSeconds 86400 } | Out-Null

Write-OK "Servizio $ServiceName configurato (AUTO_START, log rotation)"

# ============================================================
#  STEP 6: Avvia servizio
# ============================================================
Write-Status "Avvio servizio..."
$startExit = Invoke-NativeSafe { & $nssmPath start $ServiceName }
if ($startExit -ne 0) {
    Write-Warn "Avvio servizio fallito (exit=$startExit). Provo con sc start..."
    Invoke-NativeSafe { sc.exe start $ServiceName } | Out-Null
}

# Verifica con retry (il servizio puo' impiegare qualche secondo)
$svcRunning = $false
for ($i = 0; $i -lt 6; $i++) {
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Running") {
        $svcRunning = $true
        break
    }
}
if ($svcRunning) {
    Write-OK "Servizio $ServiceName ATTIVO!"
} else {
    Write-Err "Servizio $ServiceName non attivo dopo retry. Stato: $($svc.Status)"
    Write-Err "Controlla: C:\ProgramData\caduceo\agent.err.log"
    exit 1
}

# ============================================================
#  DONE
# ============================================================
Write-Status "===================================="
Write-OK "Caduceo Agent installato su $AgentId"
Write-Status "  Directory:  $CaduceoDir"
Write-Status "  Config:     $configFile"
Write-Status "  Relay:      $RelayUrl"
Write-Status "  Servizio:   $ServiceName (AUTO_START)"
Write-Status "  L'agente si avvia automaticamente all'accensione."
Write-Status "===================================="
