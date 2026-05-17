<#
.SYNOPSIS
    Caduceo Agent - Script di installazione automatica per Windows
.DESCRIPTION
    Installa e configura il Caduceo Agent su un PC Windows remoto.
    Include: Git, Python, zrok, caduceo-agent.
    Alla fine offre la registrazione come servizio Windows.
.NOTES
    Eseguire come Amministratore per installare come servizio.
    Versione: 1.0
#>

# ── Configurazione ────────────────────────────────────────────────────────────
$RELAY_URL = "wss://533q08lvroip.shares.zrok.io/agent"
$PSK_HEX   = "0bccdc4cffb30e10eab32a41f43d7802763c6c3cd9ec1b4975a9b80aff96bdba"
$ZROK_TOKEN = "cehOJScTddhQ"
$TAGS       = "remoto"
$INSTALL_DIR = "$env:USERPROFILE\caduceo"

# ── Colori e helper ──────────────────────────────────────────────────────────
function Write-Step { param([string]$msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$msg) Write-Host "  [X] $msg" -ForegroundColor Red }

function Test-Command {
    param([string]$cmd)
    try { Get-Command $cmd -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

# ── Banner ───────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "  ║       CADUCEO AGENT - Windows Setup      ║" -ForegroundColor Magenta
Write-Host "  ║       Installazione automatica            ║" -ForegroundColor Magenta
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host ""

# ── Step 1: Git ──────────────────────────────────────────────────────────────
Write-Step "Verifica Git..."
if (Test-Command git) {
    $gitVer = git --version
    Write-Ok "Git $gitVer gia' installato"
} else {
    Write-Warn "Git non trovato. Installazione..."
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements 2>$null
    if (-not $?) {
        Write-Err "Installazione Git fallita. Installa manualmente da https://git-scm.com/download/win"
        exit 1
    }
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Write-Ok "Git installato"
}

# ── Step 2: Python ───────────────────────────────────────────────────────────
Write-Step "Verifica Python 3.11+..."
if (Test-Command python) {
    $pyVer = python --version 2>&1
    $pyMajor = [int]($pyVer -replace "Python (\d+)\..*", '$1')
    $pyMinor = [int]($pyVer -replace "Python \d+\.(\d+).*", '$1')
    if ($pyMajor -ge 3 -and $pyMinor -ge 11) {
        Write-Ok "Python $pyVer gia' installato"
    } else {
        Write-Warn "Python $pyVer trovato ma serve 3.11+. Aggiornamento..."
        winget install Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements 2>$null
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    }
} else {
    Write-Warn "Python non trovato. Installazione Python 3.12..."
    winget install Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements 2>$null
    if (-not $?) {
        Write-Err "Installazione Python fallita. Installa manualmente da https://www.python.org/downloads/"
        exit 1
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Write-Ok "Python installato"
}

# Assicurati che python e pip siano accessibili
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# ── Step 3: zrok ────────────────────────────────────────────────────────────
Write-Step "Installazione zrok..."
$zrokPath = "$env:USERPROFILE\AppData\Local\zrok\zrok.exe"
if (Test-Path $zrokPath) {
    Write-Ok "zrok gia' installato in $zrokPath"
} else {
    Write-Warn "Download zrok v2.0.3 per Windows..."
    $zrokDir = Split-Path $zrokPath
    New-Item -ItemType Directory -Force -Path $zrokDir | Out-Null
    
    $url = "https://github.com/openziti/zrok/releases/download/v2.0.3/zrok_2.0.3_windows_amd64.zip"
    $zip = "$env:TEMP\zrok.zip"
    
    # Usa TLS 1.2
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $env:TEMP\zrok-extract -Force
        Copy-Item "$env:TEMP\zrok-extract\zrok.exe" $zrokPath -Force
        Remove-Item $zip -Force
        Remove-Item "$env:TEMP\zrok-extract" -Recurse -Force
    } catch {
        Write-Err "Download zrok fallito: $_"
        Write-Warn "Scarica manualmente da https://github.com/openziti/zrok/releases"
        exit 1
    }
    
    # Aggiungi al PATH se non presente
    $userPath = [System.Environment]::GetEnvironmentVariable("Path","User")
    if ($userPath -notlike "*zrok*") {
        [System.Environment]::SetEnvironmentVariable("Path", "$userPath;$zrokDir", "User")
        $env:Path += ";$zrokDir"
    }
    Write-Ok "zrok installato"
}

# ── Step 4: Abilita zrok ────────────────────────────────────────────────────
Write-Step "Configurazione zrok..."
$zrokBin = if (Test-Command zrok) { "zrok" } else { $zrokPath }
try {
    $status = & $zrokBin status 2>&1
    if ($status -match "EnvZId") {
        Write-Ok "zrok gia' abilitato"
    } else {
        Write-Warn "Abilitazione zrok con il token..."
        & $zrokBin enable --headless $ZROK_TOKEN 2>&1 | Out-Null
        Write-Ok "zrok abilitato"
    }
} catch {
    & $zrokBin enable --headless $ZROK_TOKEN 2>&1 | Out-Null
    Write-Ok "zrok abilitato"
}

# ── Step 5: Clone Caduceo ────────────────────────────────────────────────────
Write-Step "Download Caduceo..."
if (Test-Path "$INSTALL_DIR\.git") {
    Write-Ok "Repository gia' presente in $INSTALL_DIR"
} else {
    if (Test-Path $INSTALL_DIR) {
        Remove-Item $INSTALL_DIR -Recurse -Force
    }
    git clone https://github.com/fladnaG86/caduceo.git $INSTALL_DIR
    Write-Ok "Repository clonato in $INSTALL_DIR"
}

# ── Step 6: Ambiente virtuale Python ─────────────────────────────────────────
Write-Step "Configurazione ambiente Python..."
Set-Location $INSTALL_DIR

if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Ok "Ambiente virtuale creato"
} else {
    Write-Ok "Ambiente virtuale gia' esistente"
}

# Attiva venv per i comandi successivi
& .\.venv\Scripts\Activate.ps1

Write-Step "Installazione dipendenze..."
pip install --upgrade pip --quiet
pip install -e "./caduceo-common" -e "./caduceo-agent" --quiet 2>&1 | Out-Null
Write-Ok "Dipendenze installate"

# ── Step 7: Config del Caduceo Agent ─────────────────────────────────────────
Write-Step "Configurazione Caduceo Agent..."
$configDir = "$env:USERPROFILE\.caduceo"
$configFile = "$configDir\agent.json"

if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
}

$config = @{
    relay_url  = $RELAY_URL
    psk_hex    = $PSK_HEX
    tags       = @($TAGS)
} | ConvertTo-Json -Depth 3

Set-Content -Path $configFile -Value $config -Encoding UTF8
Write-Ok "Config salvato in $configFile"

# ── Step 8: Test connessione ─────────────────────────────────────────────────
Write-Step "Test connessione al relay..."
try {
    $response = Invoke-RestMethod -Uri "https://533q08lvroip.shares.zrok.io/api/health" -SkipCertificateCheck
    if ($response.status -eq "ok") {
        Write-Ok "Relay raggiungibile! Versione: $($response.version)"
    } else {
        Write-Warn "Risposta inattesa dal relay: $($response | ConvertTo-Json)"
    }
} catch {
    Write-Err "Impossibile raggiungere il relay: $_"
    Write-Warn "Verifica che:
      1) Il server relay sia attivo su homeubuntu
      2) zrok share sia attivo su homeubuntu
      3) La connessione internet funzioni"
}

# ── Step 9: Servizio Windows (opzionale) ──────────────────────────────────────
Write-Host ""
Write-Host "  ══════════════════════════════════════════" -ForegroundColor Yellow
Write-Host "  INSTALLAZIONE COMPLETATA!" -ForegroundColor Green
Write-Host "  ══════════════════════════════════════════" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Per avviare l'agent manualmente:" -ForegroundColor White
Write-Host "    cd $INSTALL_DIR" -ForegroundColor Gray
Write-Host "    .\.venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "    python -m caduceo_agent --config $configFile --verbose" -ForegroundColor Gray
Write-Host ""

$installService = Read-Host "  Vuoi installare l'agent come servizio Windows? (s/N)"
if ($installService -eq "s" -or $installService -eq "S") {
    Write-Step "Installazione come servizio Windows..."
    
    # Crea lo script di avvio
    $startScript = @"
@echo off
call "$INSTALL_DIR\.venv\Scripts\activate.bat"
python -m caduceo_agent --config "$configFile"
"@
    Set-Content -Path "$INSTALL_DIR\start_agent.bat" -Value $startScript -Encoding ASCII
    
    # Registra come servizio usando sc (richiede Admin)
    $svcName = "CaduceoAgent"
    $svcExists = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    
    if ($svcExists) {
        Write-Warn "Servizio gia' esistente, aggiornamento..."
        sc.exe delete $svcName | Out-Null
        Start-Sleep -Seconds 2
    }
    
    # Usa NSSM se disponibile, altrimenti sc
    if (Test-Command nssm) {
        nssm install $svcName "$INSTALL_DIR\start_agent.bat"
        nssm set $svcName AppDirectory "$INSTALL_DIR"
        nssm set $svcName DisplayName "Caduceo Agent"
        nssm set $svcName Description "Caduceo Remote Agent - connessione sicura al relay"
        nssm set $svcName Start SERVICE_AUTO_START
        nssm start $svcName
        Write-Ok "Servizio installato e avviato (nssm)"
    } else {
        # Metodo alternativo: scheduled task all'avvio
        $action = New-ScheduledTaskAction -Execute "$INSTALL_DIR\start_agent.bat" -WorkingDirectory $INSTALL_DIR
        $trigger = New-ScheduledTaskTrigger -AtLogon
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        
        Register-ScheduledTask -TaskName "CaduceoAgent" -Action $action -Trigger $trigger -Settings $settings -Description "Caduceo Remote Agent" -Force | Out-Null
        
        # Avvia subito
        Start-ScheduledTask -TaskName "CaduceoAgent"
        Write-Ok "Avviato come Scheduled Task (avvio automatico al login)"
        Write-Warn "Per un servizio vero, installa NSSM: winget install NSSM.NSSM"
    }
}

Write-Host ""
Write-Host "  Fine! L'agent Caduceo e' pronto." -ForegroundColor Green
Write-Host "  Config: $configFile" -ForegroundColor Gray
Write-Host "  Relay:  $RELAY_URL" -ForegroundColor Gray
Write-Host ""