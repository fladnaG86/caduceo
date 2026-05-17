# Caduceo Agent - Windows installer
# Version: 1.1 - ASCII only, no special chars
# Run: powershell -ExecutionPolicy Bypass -File install-agent-windows.ps1

# -- Config -----------------------------------------------------------
$RELAY_URL  = 'wss://533q08lvroip.shares.zrok.io/agent'
$PSK_HEX    = '0bccdc4cffb30e10eab32a41f43d7802763c6c3cd9ec1b4975a9b80aff96bdba'
$ZROK_TOKEN  = 'cehOJScTddhQ'
$TAGS        = 'remoto'
$INSTALL_DIR = Join-Path $env:USERPROFILE 'caduceo'

# -- Helpers ----------------------------------------------------------
function Write-Step { param([string]$msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host '  [OK] ' -NoNewline -ForegroundColor Green; Write-Host $msg }
function Write-Warn { param([string]$msg) Write-Host '  [!] ' -NoNewline -ForegroundColor Yellow; Write-Host $msg }
function Write-Err  { param([string]$msg) Write-Host '  [X] ' -NoNewline -ForegroundColor Red; Write-Host $msg }

function Test-Command {
    param([string]$cmd)
    try { Get-Command $cmd -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
}

# -- Banner -----------------------------------------------------------
Write-Host ''
Write-Host '  ========================================' -ForegroundColor Magenta
Write-Host '  |    CADUCEO AGENT - Windows Setup     |' -ForegroundColor Magenta
Write-Host '  |    Installazione automatica          |' -ForegroundColor Magenta
Write-Host '  ========================================' -ForegroundColor Magenta
Write-Host ''

# -- Step 1: Git ------------------------------------------------------
Write-Step 'Verifica Git...'
if (Test-Command git) {
    $gitVer = git --version
    Write-Ok "Git $gitVer gia installato"
} else {
    Write-Warn 'Git non trovato. Installazione...'
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
    Write-Ok 'Git installato'
}

# -- Step 2: Python ---------------------------------------------------
Write-Step 'Verifica Python 3.11+...'
if (Test-Command python) {
    $pyVer = (python --version 2>&1).ToString()
    try {
        $parts = $pyVer -replace 'Python ','' -split '\.'
        $pyMajor = [int]$parts[0]
        $pyMinor = [int]$parts[1]
    } catch {
        $pyMajor = 0; $pyMinor = 0
    }
    if ($pyMajor -ge 3 -and $pyMinor -ge 11) {
        Write-Ok "Python $pyVer gia installato"
    } else {
        Write-Warn "Python $pyVer trovato ma serve 3.11+. Aggiornamento..."
        winget install Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
        Refresh-Path
    }
} else {
    Write-Warn 'Python non trovato. Installazione Python 3.12...'
    winget install Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
    Write-Ok 'Python installato'
}

# -- Step 3: zrok -----------------------------------------------------
Write-Step 'Installazione zrok...'
$zrokPath = Join-Path $env:USERPROFILE 'AppData\Local\zrok\zrok.exe'
if (Test-Path $zrokPath) {
    Write-Ok "zrok gia installato in $zrokPath"
} else {
    Write-Warn 'Download zrok v2.0.3 per Windows...'
    $zrokDir = Split-Path $zrokPath
    New-Item -ItemType Directory -Force -Path $zrokDir | Out-Null

    $url = 'https://github.com/openziti/zrok/releases/download/v2.0.3/zrok_2.0.3_windows_amd64.tar.gz'
    $archiveExt = '.tar.gz'
    $archiveFile = Join-Path $env:TEMP 'zrok.tar.gz'

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    try {
        Invoke-WebRequest -Uri $url -OutFile $archiveFile -UseBasicParsing -UserAgent 'Mozilla/5.0'
        $extractDir = Join-Path $env:TEMP 'zrok-extract'
        if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

        # Extract .tar.gz: first gunzip, then tar extract
        $gzFile = $archiveFile
        $tarFile = $archiveFile -replace '\.gz$', ''
        $stream = [System.IO.File]::OpenRead($gzFile)
        $decompress = New-Object System.IO.Compression.GZipStream($stream, [System.IO.Compression.CompressionMode]::Decompress)
        $outStream = [System.IO.File]::Create($tarFile)
        $decompress.CopyTo($outStream)
        $outStream.Close()
        $decompress.Close()
        $stream.Close()

        # Now extract tar
        tar xf $tarFile -C $extractDir 2>$null
        # zrok v2 ships as zrok2.exe
        $zrokExeSrc = Join-Path $extractDir 'zrok2.exe'
        if (-not (Test-Path $zrokExeSrc)) {
            # check subdirectory or old name zrok.exe
            $zrokExe = Get-ChildItem -Path $extractDir -Filter 'zrok2.exe' -Recurse | Select-Object -First 1
            if (-not $zrokExe) {
                $zrokExe = Get-ChildItem -Path $extractDir -Filter 'zrok.exe' -Recurse | Select-Object -First 1
            }
            if ($zrokExe) {
                $zrokExeSrc = $zrokExe.FullName
            } else {
                Write-Err 'zrok2.exe non trovato nell archivio'
                exit 1
            }
        }
        Copy-Item $zrokExeSrc $zrokPath -Force
        Remove-Item $archiveFile -Force
        Remove-Item $tarFile -Force
        Remove-Item $extractDir -Recurse -Force
    } catch {
        Write-Err "Download zrok fallito: $_"
        Write-Warn 'Scarica manualmente da https://github.com/openziti/zrok/releases'
        exit 1
    }

    $userPath = [System.Environment]::GetEnvironmentVariable('Path','User')
    if ($userPath -notlike '*zrok*') {
        [System.Environment]::SetEnvironmentVariable('Path', "$userPath;$zrokDir", 'User')
        $env:Path += ";$zrokDir"
    }
    Write-Ok 'zrok installato'
}

# -- Step 4: Abilita zrok ---------------------------------------------
Write-Step 'Configurazione zrok...'
$zrokBin = if (Test-Command zrok) { 'zrok' } else { $zrokPath }
try {
    $status = & $zrokBin status 2>&1
    if ($status -match 'EnvZId') {
        Write-Ok 'zrok gia abilitato'
    } else {
        Write-Warn 'Abilitazione zrok con il token...'
        & $zrokBin enable --headless $ZROK_TOKEN 2>&1 | Out-Null
        Write-Ok 'zrok abilitato'
    }
} catch {
    & $zrokBin enable --headless $ZROK_TOKEN 2>&1 | Out-Null
    Write-Ok 'zrok abilitato'
}

# -- Step 5: Clone Caduceo -------------------------------------------
Write-Step 'Download Caduceo...'
if (Test-Path (Join-Path $INSTALL_DIR '.git')) {
    Write-Ok "Repository gia presente in $INSTALL_DIR"
} else {
    if (Test-Path $INSTALL_DIR) {
        Remove-Item $INSTALL_DIR -Recurse -Force
    }
    git clone https://github.com/fladnaG86/caduceo.git $INSTALL_DIR
    Write-Ok "Repository clonato in $INSTALL_DIR"
}

# -- Step 6: Ambiente Python ------------------------------------------
Write-Step 'Configurazione ambiente Python...'
Set-Location $INSTALL_DIR

if (-not (Test-Path '.venv')) {
    python -m venv .venv
    Write-Ok 'Ambiente virtuale creato'
} else {
    Write-Ok 'Ambiente virtuale gia esistente'
}

& (Join-Path $INSTALL_DIR '.venv\Scripts\Activate.ps1')

Write-Step 'Installazione dipendenze...'
pip install --upgrade pip --quiet
pip install -e './caduceo-common' -e './caduceo-agent' --quiet
Write-Ok 'Dipendenze installate'

# -- Step 7: Config agent ---------------------------------------------
Write-Step 'Configurazione Caduceo Agent...'
$configDir = Join-Path $env:USERPROFILE '.caduceo'
$configFile = Join-Path $configDir 'agent.json'

if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
}

$config = @{
    relay_url = $RELAY_URL
    psk_hex   = $PSK_HEX
    tags      = @($TAGS)
} | ConvertTo-Json -Depth 3

Set-Content -Path $configFile -Value $config -Encoding UTF8
Write-Ok "Config salvato in $configFile"

# -- Step 8: Test connessione -----------------------------------------
Write-Step 'Test connessione al relay...'
try {
    $response = Invoke-RestMethod -Uri 'https://533q08lvroip.shares.zrok.io/api/health' -SkipCertificateCheck
    if ($response.status -eq 'ok') {
        Write-Ok "Relay raggiungibile! Versione: $($response.version)"
    } else {
        Write-Warn 'Risposta inattesa dal relay'
    }
} catch {
    Write-Err "Impossibile raggiungere il relay: $_"
    Write-Warn 'Verifica che il relay e zrok share siano attivi su homeubuntu'
}

# -- Step 9: Servizio (opzionale) -------------------------------------
Write-Host ''
Write-Host '  ========================================' -ForegroundColor Yellow
Write-Host '  INSTALLAZIONE COMPLETATA!' -ForegroundColor Green
Write-Host '  ========================================' -ForegroundColor Yellow
Write-Host ''
Write-Host '  Per avviare l agent manualmente:' -ForegroundColor White
Write-Host "    cd $INSTALL_DIR" -ForegroundColor Gray
Write-Host '    .\.venv\Scripts\Activate.ps1' -ForegroundColor Gray
Write-Host "    python -m caduceo_agent --config $configFile --verbose" -ForegroundColor Gray
Write-Host ''

$installService = Read-Host '  Vuoi installare l agent come servizio Windows? (s/N)'
if ($installService -eq 's' -or $installService -eq 'S') {
    Write-Step 'Installazione come servizio Windows...'

    $startBat = "@echo off`ncall `"$INSTALL_DIR\.venv\Scripts\activate.bat`"`npython -m caduceo_agent --config `"$configFile`""
    Set-Content -Path (Join-Path $INSTALL_DIR 'start_agent.bat') -Value $startBat -Encoding ASCII

    if (Test-Command nssm) {
        nssm install CaduceoAgent (Join-Path $INSTALL_DIR 'start_agent.bat')
        nssm set CaduceoAgent AppDirectory $INSTALL_DIR
        nssm set CaduceoAgent DisplayName 'Caduceo Agent'
        nssm set CaduceoAgent Description 'Caduceo Remote Agent'
        nssm set CaduceoAgent Start SERVICE_AUTO_START
        nssm start CaduceoAgent
        Write-Ok 'Servizio installato e avviato (nssm)'
    } else {
        $action = New-ScheduledTaskAction -Execute (Join-Path $INSTALL_DIR 'start_agent.bat') -WorkingDirectory $INSTALL_DIR
        $trigger = New-ScheduledTaskTrigger -AtLogon
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName 'CaduceoAgent' -Action $action -Trigger $trigger -Settings $settings -Description 'Caduceo Remote Agent' -Force | Out-Null
        Start-ScheduledTask -TaskName 'CaduceoAgent'
        Write-Ok 'Avviato come Scheduled Task (auto al login)'
        Write-Warn 'Per un servizio vero: winget install NSSM.NSSM'
    }
}

Write-Host ''
Write-Host '  Fine! L agent Caduceo e pronto.' -ForegroundColor Green
Write-Host "  Config: $configFile" -ForegroundColor Gray
Write-Host "  Relay:  $RELAY_URL" -ForegroundColor Gray
Write-Host ''