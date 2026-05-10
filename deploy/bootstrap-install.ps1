<#
.SYNOPSIS
Caduceo Agent - Bootstrap Installer for Windows
Works WITHOUT Python pre-installed. Downloads a portable Python if needed.

.PARAMETER AgentId
Unique identifier for this agent (e.g. "mio-pc", "ufficio", "laptop")

.PARAMETER Tags
Comma-separated tags (default: "caduceo")

.EXAMPLE
.\bootstrap-install.ps1 -AgentId mio-pc
.\bootstrap-install.ps1 -AgentId ufficio -Tags "caduceo,office"
#>
param(
    [string]$AgentId = "",
    [string]$Tags = "caduceo"
)

$ErrorActionPreference = "Stop"
$CaduceoDir = Join-Path $env:USERPROFILE ".caduceo"
$EmbedDir = Join-Path $CaduceoDir "python"
$EmbedExe = Join-Path $EmbedDir "python.exe"
$EmbedPip = Join-Path $EmbedDir "Scripts\pip.exe"
$RelayUrl = "wss://caduceo.shares.zrok.io"
$PskHex = "3d97d8ee4e6de4c452351fb2e4d2252a44dce8aef3fa3db5e4de2ce2402a4b05"
$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

function Write-Status($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-OK($msg) { Write-Host $msg -ForegroundColor Green }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host $msg -ForegroundColor Red }

# ============================================================
#  STEP 0: Determine Agent ID
# ============================================================
if ($AgentId -eq "") {
    $AgentId = ($env:COMPUTERNAME).ToLower().Replace(" ", "-").Replace(".", "-")
    Write-Warn "Nessun AgentId specificato. Uso: $AgentId"
}

Write-Host ""
Write-Host "=" * 60
Write-Status "  Caduceo Agent - Bootstrap Installer"
Write-Host "=" * 60
Write-Host ""

# ============================================================
#  STEP 1: Find or install Python
# ============================================================
$usePython = ""
$useVenv = $true

# Check system Python - must actually work (not just be a Windows Store stub)
$sysPython = Get-Command python -ErrorAction SilentlyContinue
$pythonWorks = $false
if ($sysPython) {
    try {
        $pyTest = & python --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $pyTest -match "Python \d+") {
            $pythonWorks = $true
        }
    } catch {
        $pythonWorks = $false
    }
}
if ($pythonWorks) {
    Write-OK "[1/6] Python di sistema trovato: $pyTest"
    Write-Host "       Path: $($sysPython.Source)"
    $usePython = $sysPython.Source
    $useVenv = $true
} elseif (Test-Path $EmbedExe) {
    Write-OK "[1/6] Python embeddable gia' presente: $EmbedExe"
    $usePython = $EmbedExe
    $useVenv = $false
} else {
    Write-Status "[1/6] Python non trovato. Scarico Python embeddable..."
    
    # Download Python embeddable
    $zipPath = Join-Path $env:TEMP "python-embed.zip"
    Write-Host "       Scaricamento da $PythonUrl ..."
    
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $PythonUrl -OutFile $zipPath -UseBasicParsing
    } catch {
        # Try with curl as fallback
        Write-Warn "       Invoke-WebRequest fallito, provo curl..."
        & curl.exe -sL -o $zipPath $PythonUrl
        if ($LASTEXITCODE -ne 0) {
            Write-Err "ERRORE: Impossibile scaricare Python. Controlla la connessione internet."
            exit 1
        }
    }
    
    Write-Host "       Estrazione in $EmbedDir ..."
    New-Item -ItemType Directory -Path $EmbedDir -Force | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $EmbedDir -Force
    Remove-Item $zipPath -ErrorAction SilentlyContinue
    
    # Fix _pth file to enable site-packages and pip
    $pthFile = Get-ChildItem $EmbedDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $pthContent = @"
python312.zip
.
Lib
Lib\site-packages
import site
"@
        Set-Content -Path $pthFile.FullName -Value $pthContent -Encoding UTF8
        Write-OK "       _pth file configurato"
    }
    
    # Create Lib and site-packages directories
    New-Item -ItemType Directory -Path (Join-Path $EmbedDir "Lib\site-packages") -Force | Out-Null
    
    # Install pip
    Write-Host "       Installazione pip..."
    $getPip = Join-Path $env:TEMP "get-pip.py"
    try {
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPip -UseBasicParsing
    } catch {
        & curl.exe -sL -o $getPip $GetPipUrl
    }
    & $EmbedExe $getPip
    Remove-Item $getPip -ErrorAction SilentlyContinue
    
    if (-not (Test-Path $EmbedExe)) {
        Write-Err "ERRORE: Installazione Python embeddable fallita."
        exit 1
    }
    
    $embedVersion = & $EmbedExe --version 2>&1
    Write-OK "       Python embeddable installato: $embedVersion"
    $usePython = $EmbedExe
    $useVenv = $false
}

# ============================================================
#  STEP 2: Create config directory
# ============================================================
Write-Status "[2/6] Creazione configurazione..."
New-Item -ItemType Directory -Path $CaduceoDir -Force | Out-Null

$agentIdToUse = $AgentId
$tagsList = $Tags.Split(",")

$config = @{
    relay_url  = $RelayUrl
    psk_hex    = $PskHex
    agent_id   = $agentIdToUse
    tags       = $tagsList
    install_dir = $CaduceoDir
} | ConvertTo-Json

$configFile = Join-Path $CaduceoDir "agent.json"
Set-Content -Path $configFile -Value $config -Encoding UTF8

# Restrict config file permissions (Windows)
try {
    icacls $configFile /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
} catch {
    Write-Warn "       Impossibile restringere permessi. Esegui come Admin se vuoi proteggere il config."
}

Write-OK "       Config salvato: $configFile"
Write-Host "       Agent ID: $agentIdToUse"
Write-Host "       Relay:    $RelayUrl"

# ============================================================
#  STEP 3: Install packages
# ============================================================
if ($useVenv) {
    # System Python: use install.py which creates venv
    Write-Status "[3/6] Scaricamento install.py..."
    $installPy = Join-Path $env:TEMP "caduceo-install.py"
    Invoke-WebRequest -Uri "https://caduceo.shares.zrok.io/download/install.py?platform=windows" -OutFile $installPy -UseBasicParsing
    Write-OK "       install.py scaricato"
    
    Write-Status "[4/6] Esecuzione install.py con venv..."
    & $usePython $installPy
    
    # Find venv python for service registration
    $venvPython = Join-Path $CaduceoDir "venv\Scripts\python.exe"
    $venvPythonw = Join-Path $CaduceoDir "venv\Scripts\pythonw.exe"
} else {
    # Embedded Python: install packages directly (no venv)
    Write-Status "[3/6] Installazione pacchetti in Python embeddable..."
    
    # Install dependencies
    Write-Host "       Installazione websockets, psutil, pywin32, Pillow..."
    & $usePython -m pip install websockets psutil pywin32 Pillow --quiet 2>&1 | ForEach-Object { Write-Host "       $_" }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Err "ERRORE: Installazione dipendenze fallita."
        Write-Err "Prova manualmente: $usePython -m pip install websockets psutil pywin32 Pillow"
        exit 1
    }
    
    # Install caduceo packages
    Write-Host "       Scaricamento pacchetti caduceo..."
    $commonWhl = Join-Path $CaduceoDir "caduceo_common-0.1.0-py3-none-any.whl"
    $agentWhl = Join-Path $CaduceoDir "caduceo_agent-0.1.0-py3-none-any.whl"
    
    # Download wheels from relay
    Invoke-WebRequest -Uri "https://caduceo.shares.zrok.io/download/caduceo_common-0.1.0-py3-none-any.whl" -OutFile $commonWhl -UseBasicParsing -ErrorAction SilentlyContinue
    Invoke-WebRequest -Uri "https://caduceo.shares.zrok.io/download/caduceo_agent-0.1.0-py3-none-any.whl" -OutFile $agentWhl -UseBasicParsing -ErrorAction SilentlyContinue
    
    if ((Test-Path $commonWhl) -and (Test-Path $agentWhl)) {
        & $usePython -m pip install --force-reinstall $commonWhl $agentWhl --quiet 2>&1 | ForEach-Object { Write-Host "       $_" }
        Remove-Item $commonWhl, $agentWhl -ErrorAction SilentlyContinue
        Write-OK "       Pacchetti caduceo installati (da wheel)"
    } else {
        # Fallback: extract wheels from install.py
        Write-Host "       Wheel non disponibili via download. Estrazione da install.py..."
        $instPy = Join-Path $env:TEMP "caduceo-install.py"
        try {
            Invoke-WebRequest -Uri "https://caduceo.shares.zrok.io/download/install.py?platform=windows" -OutFile $instPy -UseBasicParsing
        } catch {
            & curl.exe -sL -o $instPy "https://caduceo.shares.zrok.io/download/install.py?platform=windows"
        }
        
        # Extract wheel data from install.py and create wheels
        & $usePython -c @"
import base64, re, sys
content = open(r'$instPy').read()
for var, fname in [('WHEEL_COMMON_B64', r'$CaduceoDir\caduceo_common-0.1.0-py3-none-any.whl'), ('WHEEL_AGENT_B64', r'$CaduceoDir\caduceo_agent-0.1.0-py3-none-any.whl')]:
    match = re.search(var + r' = \"([^\"]{100,})\"', content)
    if match:
        with open(fname, 'wb') as f:
            f.write(base64.b64decode(match.group(1)))
        print(f'Extracted {fname}')
    else:
        print(f'WARNING: {var} not found in install.py')
"@
        & $usePython -m pip install --force-reinstall "$CaduceoDir\caduceo_common-0.1.0-py3-none-any.whl" "$CaduceoDir\caduceo_agent-0.1.0-py3-none-any.whl" --quiet 2>&1 | ForEach-Object { Write-Host "       $_" }
        Remove-Item "$CaduceoDir\caduceo_common*", "$CaduceoDir\caduceo_agent*" -ErrorAction SilentlyContinue
        Remove-Item $instPy -ErrorAction SilentlyContinue
        Write-OK "       Pacchetti caduceo installati (da install.py)"
    }
    
    # For embedded Python, agent runs directly (no venv)
    $venvPython = $EmbedExe
    $venvPythonw = Join-Path $EmbedDir "pythonw.exe"
}

# ============================================================
#  STEP 4: Windows Defender exclusions
# ============================================================
Write-Status "[4/6] Configurazione Windows Defender..."
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole("Administrators")
if ($isAdmin) {
    Add-MpPreference -ExclusionPath $CaduceoDir -ErrorAction SilentlyContinue
    Add-MpPreference -ExclusionProcess "python.exe" -ErrorAction SilentlyContinue
    Add-MpPreference -ExclusionProcess "pythonw.exe" -ErrorAction SilentlyContinue
    Write-OK "       Esclusioni Windows Defender aggiunte"
} else {
    Write-Warn "       Esegui come Admin per aggiungere esclusioni Windows Defender:"
    Write-Host '       Add-MpPreference -ExclusionPath "$env:USERPROFILE\.caduceo"'
}

# ============================================================
#  STEP 5: Register Task Scheduler
# ============================================================
Write-Status "[5/6] Registrazione Task Scheduler..."

# Remove old task if exists
schtasks /Delete /TN "CaduceoAgent" /F 2>$null | Out-Null

if (-not (Test-Path $venvPythonw)) {
    $venvPythonw = $venvPythonw -replace "pythonw\.exe$", "python.exe"
    Write-Warn "       pythonw.exe non trovato, uso python.exe"
}

$cmd = "`"$venvPythonw`" -m caduceo_agent --config `"$configFile`""
schtasks /Create /TN "CaduceoAgent" /TR $cmd /SC ONLOGON /RL HIGHEST /RU $env:USERNAME /F 2>$null | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-OK "       Task Scheduler: CaduceoAgent registrato (ONLOGON)"
} else {
    Write-Warn "       Task Scheduler fallito. Comando manuale (come Admin):"
    Write-Host "       schtasks /Create /TN CaduceoAgent /TR `"$cmd`" /SC ONLOGON /RL HIGHEST /RU $env:USERNAME /F"
}

# ============================================================
#  STEP 6: Test connection
# ============================================================
Write-Status "[6/6] Test avvio agent..."

# Start agent briefly to test
$testCmd = "`"$venvPython`" -m caduceo_agent --config `"$configFile`""
$proc = Start-Process -FilePath $venvPython -ArgumentList "-m", "caduceo_agent", "--config", $configFile -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 5
if (-not $proc.HasExited) {
    Write-OK "       Agent avviato con successo (PID: $($proc.Id))"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
} else {
    Write-Warn "       Agent terminato con codice: $($proc.ExitCode)"
    Write-Host "       Verifica configurazione e connettivita'."
}

# ============================================================
# DONE
# ============================================================
Write-Host ""
Write-Host "=" * 60
Write-OK "  Caduceo Agent installato con successo!"
Write-Host "=" * 60
Write-Host ""
Write-Host "  Directory:  $CaduceoDir"
Write-Host "  Config:     $configFile"
Write-Host "  Agent ID:   $agentIdToUse"
Write-Host "  Relay:      $RelayUrl"
Write-Host "  Python:     $venvPython"
Write-Host ""
Write-Host "  L'agent si avviera' automaticamente al prossimo login."
Write-Host "  Per avviare ora: schtasks /Run /TN CaduceoAgent"
Write-Host ""