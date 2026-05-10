<#
.SYNOPSIS
Caduceo Agent Installer for Windows
Works WITH or WITHOUT Python. Handles Windows Store stub automatically.

.PARAMETER AgentId
Unique identifier for this agent (e.g. "mio-pc", "ufficio", "laptop")
If not specified, uses COMPUTERNAME in lowercase.

.PARAMETER Tags
Comma-separated tags (default: "caduceo")

.PARAMETER PskHex
PSK per autenticazione (opzionale, se non specificato usa default)

.EXAMPLE
.\install-agent.ps1 -AgentId vmcardpresso
.\install-agent.ps1 -AgentId ufficio -Tags "caduceo,office"

One-liner (run from CMD or PowerShell):
  powershell -Command "& { $f=\"$env:TEMP\install-agent.ps1\"; Invoke-WebRequest 'https://caduceo.shares.zrok.io/download/install-agent.ps1' -OutFile $f -UseBasicParsing; & $f -AgentId vmcardpresso }"
#>

param(
    [string]$AgentId = "",
    [string]$Tags = "caduceo",
    [string]$PskHex = ""
)

# Do NOT set $ErrorActionPreference="Stop" globally - it kills on stderr from external commands
# Use local error handling with try/catch or -ErrorAction Stop where needed
$ErrorActionPreference = "Continue"

# Track temporary files for cleanup
$tempFiles = @()

function Add-TempFile {
    param([string]$Path)
    $tempFiles += $Path
}

function Cleanup-TempFiles {
    foreach ($f in $tempFiles) {
        if (Test-Path $f) {
            Remove-Item $f -Force -ErrorAction SilentlyContinue
        }
    }
}

# ============================================================
#  HELPER: Run native EXE without $ErrorActionPreference="Stop" killing on stderr
#  Many Windows EXEs (schtasks, pip, icacls, curl) write info/warnings to stderr.
#  $ErrorActionPreference="Stop" converts these to terminating errors.
#  Returns the exit code. Output is written to host (not pipeline).
# ============================================================
function Invoke-NativeSafe {
    param([scriptblock]$Cmd)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # CRITICAL: Capture $LASTEXITCODE immediately - assignment resets it!
    $output = & $Cmd 2>&1; $exitCode = $global:LASTEXITCODE
    $ErrorActionPreference = $prev
    # Write output to console for visibility but don't return it
    $output | ForEach-Object { Write-Host $_ }
    return $exitCode
}

# ============================================================
#  CONFIGURATION
# ============================================================
$RelayUrl  = "wss://caduceo.shares.zrok.io"
# Use PSK from parameter if provided, otherwise use default (for backwards compatibility)
if ($PskHex -eq "") {
    $PskHex = "3d97d8ee4e6de4c452351fb2e4d2252a44dce8aef3fa3db5e4de2ce2402a4b05"
}
$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$CaduceoDir = Join-Path $env:USERPROFILE ".caduceo"

# ============================================================
#  UTILITY FUNCTIONS
# ============================================================
function Write-Status($msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-OK($msg)      { Write-Host $msg -ForegroundColor Green }
function Write-Warn($msg)    { Write-Host $msg -ForegroundColor Yellow }
function Write-Err($msg)     { Write-Host $msg -ForegroundColor Red }

# Write file without BOM (critical for Python _pth files and JSON)
# PowerShell Set-Content -Encoding UTF8 adds a BOM which breaks Python!
function Set-ContentNoBom([string]$Path, [string]$Content) {
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ============================================================
#  STEP 0: Determine Agent ID
# ============================================================
if ($AgentId -eq "") {
    $AgentId = ($env:COMPUTERNAME).ToLower().Replace(" ", "-").Replace(".", "-")
    # Sanitize: keep only alphanumeric, dash, underscore
    $AgentId = $AgentId -replace '[^a-z0-9_-]', ''
    Write-Warn "Nessun AgentId specificato. Uso: $AgentId"
}

Write-Host ""
Write-Host ("=" * 60)
Write-Status "  Caduceo Agent Installer"
Write-Host ("=" * 60)
Write-Host ""
Write-Host "  Agent ID:  $AgentId"
Write-Host "  Tags:      $Tags"
Write-Host "  Relay:     $RelayUrl"
Write-Host ""

# ============================================================
#  STEP 1: Find or install Python
# ============================================================
$usePython = ""
$useVenv   = $true
$venvPython = ""
$venvPythonw = ""

# Check system Python - must actually work (not a Windows Store stub)
$pythonWorks = $false
$pyTest = $null
$pyCmd = $null

foreach ($cmd in @("python", "python3", "py")) {
    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $testResult = & $cmd --version 2>&1
        $testExit = $global:LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($testExit -eq 0 -and ($testResult -join "") -match "Python \d+") {
            # Additional test: actually execute Python to detect Windows Store stub
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $execTest = & $cmd -c "print('ok')" 2>&1
            $execExit = $global:LASTEXITCODE
            $ErrorActionPreference = $prevEAP
            if ($execExit -eq 0 -and ($execTest -join "") -eq "ok") {
                $pyCmd = $cmd
                $pyTest = ($testResult -join "")
                $pythonWorks = $true
                break
            }
        }
    } catch {
        # Not a real Python
    }
}

if ($pythonWorks) {
    Write-OK "[1/6] Python di sistema trovato: $pyTest"
    $sysPython = (Get-Command $pyCmd).Source
    Write-Host "       Path: $sysPython"
    $usePython = $sysPython
    $useVenv = $true
} elseif (Test-Path (Join-Path $CaduceoDir "python\python.exe")) {
    # Check if existing embeddable Python actually works
    $embedExe = Join-Path $CaduceoDir "python\python.exe"
    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $embedTest = & $embedExe --version 2>&1
        $embedExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($embedExit -eq 0 -and ($embedTest -join "") -match "Python \d+") {
            Write-OK "[1/6] Python embeddable gia' installato: $(($embedTest -join ''))"
            $usePython = $embedExe
            $useVenv = $false
            $pythonWorks = $true
        }
    } catch { }
}

if (-not $pythonWorks -and -not $usePython) {
    Write-Status "[1/6] Python non trovato o non funzionante (stub Windows Store?)."
    Write-Host "       Scarico Python embeddable (~10 MB)..."

    $zipPath = Join-Path $env:TEMP "python-embed.zip"
    $embedDir = Join-Path $CaduceoDir "python"
    Add-TempFile -Path $zipPath

    # Download Python embeddable
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host "       Scaricamento da python.org..."
        Invoke-WebRequest -Uri $PythonUrl -OutFile $zipPath -UseBasicParsing
    } catch {
        Write-Warn "       Invoke-WebRequest fallito, provo con curl..."
        $dlExit = Invoke-NativeSafe { curl.exe -sL -o $zipPath $PythonUrl }
        if ($dlExit -ne 0) {
            Write-Err "ERRORE: Impossibile scaricare Python. Controlla la connessione internet."
            exit 1
        }
    }

    # Verify download - ZIP should be > 5MB
    if (Test-Path $zipPath) {
        $zipSize = (Get-Item $zipPath).Length
        if ($zipSize -lt 5MB) {
            Write-Err "ERRORE: Download Python incompleto ($([math]::Round($zipSize/1KB)) KB). File corrotto?"
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
            exit 1
        }
    } else {
        Write-Err "ERRORE: File Python non scaricato. Controlla la connessione internet."
        exit 1
    }

    # Extract
    Write-Host "       Estrazione in $embedDir ..."
    if (Test-Path $embedDir) { Remove-Item $embedDir -Recurse -Force }
    New-Item -ItemType Directory -Path $embedDir -Force | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $embedDir -Force
    Remove-Item $zipPath -ErrorAction SilentlyContinue

    # Fix _pth file - CRITICAL: no BOM, else Python can't find encodings
    $pthFile = Get-ChildItem $embedDir -Filter "python*._pth" | Select-Object -First 1
    if ($pthFile) {
        $pthContent = "python312.zip`n.`nLib`nLib\site-packages`nimport site"
        Set-ContentNoBom -Path $pthFile.FullName -Content $pthContent
        Write-OK "       _pth configurato"
    } else {
        Write-Err "ERRORE: File _pth non trovato in $embedDir. Python embeddable corrotto?"
        exit 1
    }

    # Create Lib\site-packages directories
    New-Item -ItemType Directory -Path (Join-Path $embedDir "Lib\site-packages") -Force | Out-Null

    # Install pip
    Write-Host "       Installazione pip..."
    $getPip = Join-Path $env:TEMP "get-pip.py"
    Add-TempFile -Path $getPip
    try {
        Invoke-WebRequest -Uri $GetPipUrl -OutFile $getPip -UseBasicParsing
    } catch {
        $dlExit = Invoke-NativeSafe { curl.exe -sL -o $getPip $GetPipUrl }
        # Verify file exists and has content after curl
        if (-not (Test-Path $getPip) -or (Get-Item $getPip).Length -eq 0) {
            Write-Err "ERRORE: Impossibile scaricare get-pip.py."
            exit 1
        }
    }

    # Verify Python works BEFORE installing pip
    Write-Host "       Verifica Python..."
    $embedExe = Join-Path $embedDir "python.exe"
    $pyVerifyExit = Invoke-NativeSafe { & $embedExe --version }
    if ($pyVerifyExit -ne 0) {
        Write-Err "ERRORE: Python embeddable non funziona (exit code: $pyVerifyExit)"
        Write-Err "       Verifica che il file _pth sia corretto in: $embedDir"
        exit 1
    }
    Write-OK "       Python embeddable verificato"

    # Now install pip (may write warnings to stderr - use Invoke-NativeSafe)
    $pipExit = Invoke-NativeSafe { & $embedExe $getPip }
    Remove-Item $getPip -ErrorAction SilentlyContinue
    if ($pipExit -ne 0) {
        Write-Warn "       pip installazione con warning (exit=$pipExit), continuo comunque..."
    }

    $usePython = $embedExe
    $useVenv = $false
}

# ============================================================
#  STEP 2: Create config directory and agent.json
# ============================================================
Write-Status "[2/6] Creazione configurazione..."
New-Item -ItemType Directory -Path $CaduceoDir -Force | Out-Null

$tagsList = $Tags -split ","
# Ensure ConvertTo-Json handles arrays correctly (PS 5.1 serializes single-element arrays as strings)
$config = @{
    relay_url   = $RelayUrl
    psk_hex     = $PskHex
    agent_id    = $AgentId
    tags        = $tagsList
    install_dir = $CaduceoDir
} | ConvertTo-Json -Depth 5

$configFile = Join-Path $CaduceoDir "agent.json"

# Remove existing file with restricted permissions (icacls (R) blocks overwrite)
# Use retry loop to handle antivirus/Defender file locks
if (Test-Path $configFile) {
    $removed = $false
    for ($i = 0; $i -lt 5 -and -not $removed; $i++) {
        try {
            # Reset permissions so we can overwrite
            Invoke-NativeSafe { icacls $configFile /grant:r "$($env:USERNAME):(F)" } | Out-Null
            Start-Sleep -Milliseconds 500  # Wait for icacls to complete
            Remove-Item $configFile -Force -ErrorAction Stop
            $removed = $true
        } catch {
            if ($i -lt 4) {
                Start-Sleep -Milliseconds 500  # Wait and retry
            } else {
                # If icacls fails (not admin), try forced delete
                try { Remove-Item $configFile -Force } catch { Write-Warn "       Impossibile rimuovere $configFile - permessi insufficienti?" }
            }
        }
    }
}

# Use BOM-free UTF8 for JSON too
Set-ContentNoBom -Path $configFile -Content $config

# Restrict permissions (read-only for user)
# CRITICAL: If permissions fail, the PSK in config file is readable by all users
$permsSet = $false
for ($i = 0; $i -lt 3 -and -not $permsSet; $i++) {
    try {
        $permExit = Invoke-NativeSafe { icacls $configFile /inheritance:r /grant:r "$($env:USERNAME):(R)" }
        if ($permExit -eq 0) {
            $permsSet = $true
        } else {
            Start-Sleep -Milliseconds 500
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $permsSet) {
    Write-Err "ERRORE: Impossibile impostare permessi su $configFile. Il PSK potrebbe essere leggibile da altri utenti."
    exit 1
}

Write-OK "       Config salvato: $configFile"
Write-Host "       Agent ID: $AgentId"
Write-Host "       Relay:    $RelayUrl"

# ============================================================
#  STEP 3: Install caduceo packages
# ============================================================
if ($useVenv) {
    # System Python: download and run install.py (which creates venv)
    Write-Status "[3/6] Scaricamento install.py..."
    $installPy = Join-Path $env:TEMP "caduceo-install.py"
    Add-TempFile -Path $installPy
    try {
        Invoke-WebRequest -Uri "https://caduceo.shares.zrok.io/download/install.py?platform=windows" -OutFile $installPy -UseBasicParsing
    } catch {
        $dlExit = Invoke-NativeSafe { curl.exe -sL -o $installPy "https://caduceo.shares.zrok.io/download/install.py?platform=windows" }
        # Verify file exists and has content after curl
        if (-not (Test-Path $installPy) -or (Get-Item $installPy).Length -eq 0) {
            Write-Err "ERRORE: Impossibile scaricare install.py"
            exit 1
        }
    }
    Write-OK "       install.py scaricato"

    Write-Status "[4/6] Installazione (venv)..."
    $installExit = Invoke-NativeSafe { & $usePython $installPy }
    if ($installExit -ne 0) {
        Write-Warn "       install.py completato con codice $installExit (potrebbe essere ok)"
    }

    $venvPython  = Join-Path $CaduceoDir "venv\Scripts\python.exe"
    $venvPythonw = Join-Path $CaduceoDir "venv\Scripts\pythonw.exe"

    # Write config again (install.py may overwrite it)
    # Reset permissions first since icacls set it to (R) earlier
    if (Test-Path $configFile) {
        Invoke-NativeSafe { icacls $configFile /grant:r "$($env:USERNAME):(F)" } | Out-Null
        Start-Sleep -Milliseconds 500  # Wait for icacls to complete
    }
    Set-ContentNoBom -Path $configFile -Content $config
    # Re-lock permissions
    Invoke-NativeSafe { icacls $configFile /inheritance:r /grant:r "$($env:USERNAME):(R)" } | Out-Null

} else {
    # Embedded Python: install packages directly (no venv)
    Write-Status "[3/6] Installazione pacchetti in Python embeddable..."

    # Download install.py and extract wheels
    Write-Host "       Scaricamento pacchetti caduceo..."
    $instPy = Join-Path $env:TEMP "caduceo-install.py"
    Add-TempFile -Path $instPy
    try {
        Invoke-WebRequest -Uri "https://caduceo.shares.zrok.io/download/install.py?platform=windows" -OutFile $instPy -UseBasicParsing
    } catch {
        $dlExit = Invoke-NativeSafe { curl.exe -sL -o $instPy "https://caduceo.shares.zrok.io/download/install.py?platform=windows" }
        # Verify file exists and has content after curl
        if (-not (Test-Path $instPy) -or (Get-Item $instPy).Length -eq 0) {
            Write-Err "ERRORE: Impossibile scaricare i pacchetti caduceo."
            exit 1
        }
    }

    $commonWhl = Join-Path $CaduceoDir "caduceo_common-0.1.0-py3-none-any.whl"
    $agentWhl  = Join-Path $CaduceoDir "caduceo_agent-0.1.0-py3-none-any.whl"
    Add-TempFile -Path $commonWhl
    Add-TempFile -Path $agentWhl

    # Extract wheels from install.py using Python itself
    $extractScriptFile = Join-Path $env:TEMP "extract_wheels.py"
    Add-TempFile -Path $extractScriptFile
    
    # Base64 encoded Python script to avoid PowerShell ParserErrors
    $b64Script = "aW1wb3J0IGJhc2U2NCwgcmUsIHN5cwpjb250ZW50ID0gb3BlbihyJ3tpbnN0UHl9JykucmVhZCgpCmZvciB2YXIsIGZuYW1lIGluIFsoJ1dIRUVMX0NPTU1PTl9CNjQnLCByJ3tjb21tb25XaGx9JyksICgnV0hFRUxfQUdFTlRfQjY0Jywgcid7YWdlbnRXaGx9JyldOgogICAgbWF0Y2ggPSByZS5zZWFyY2godmFyICsgcicgPSAiKFteIl17MTAwLH0pIicsIGNvbnRlbnQpCiAgICBpZiBtYXRjaDoKICAgICAgICB3aXRoIG9wZW4oZm5hbWUsICd3YicpIGFzIGY6CiAgICAgICAgICAgIGYud3JpdGUoYmFzZTY0LmI2NGRlY29kZShtYXRjaC5ncm91cCgxKSkpCiAgICAgICAgcHJpbnQoJ09LOiAnICsgZm5hbWUpCiAgICBlbHNlOgogICAgICAgIHByaW50KCdTS0lQOiAnICsgdmFyICsgJyBub24gdHJvdmF0bycpCiAgICAgICAgc3lzLmV4aXQoMSk="
    
    # Decode and replace placeholders with actual paths
    $decodedScript = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64Script))
    $decodedScript = $decodedScript -replace '\{instPy\}', $instPy -replace '\{commonWhl\}', $commonWhl -replace '\{agentWhl\}', $agentWhl
    
    Set-ContentNoBom -Path $extractScriptFile -Content $decodedScript

    $extractExit = Invoke-NativeSafe { & $usePython $extractScriptFile }
    if ($extractExit -ne 0) {
        Write-Err "ERRORE: Estrazione wheel fallita. Controlla la connessione internet."
        exit 1
    }

    # Install all packages in one go (critical: both wheels at once)
    Write-Host "       Installazione dipendenze e pacchetti..."
    $pipExit = Invoke-NativeSafe { & $usePython -m pip install websockets psutil pywin32 Pillow "$commonWhl" "$agentWhl" --quiet }

    if ($pipExit -ne 0) {
        Write-Err "ERRORE: Installazione pacchetti fallita."
        Write-Err "Prova manualmente: $usePython -m pip install websockets psutil pywin32 Pillow $commonWhl $agentWhl"
        exit 1
    }

    # Files will be cleaned up at end of script via Cleanup-TempFiles
    Write-OK "       Pacchetti installati"

    $venvPython  = $usePython
    # Embedded Python does NOT include pythonw.exe - always fall back to python.exe
    $pythonwPath = Join-Path (Split-Path $usePython) "pythonw.exe"
    if (Test-Path $pythonwPath) {
        $venvPythonw = $pythonwPath
    } else {
        $venvPythonw = $usePython
        Write-Warn "       pythonw.exe non trovato in Python embeddable, uso python.exe (finestra console visibile)"
    }

    # Skip step 4 marker since we did it here
    Write-Status "[4/6] (gia' completato con pacchetti sopra)"
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

# Remove old task if exists (Ignore errors - task may not exist yet)
Invoke-NativeSafe { schtasks /Delete /TN "CaduceoAgent" /F } | Out-Null

# Determine pythonw.exe path (silent, no console window)
if (-not (Test-Path $venvPythonw)) {
    $venvPythonw = $venvPythonw -replace "pythonw\.exe$", "python.exe"
    Write-Warn "       pythonw.exe non trovato, uso python.exe"
}

$taskCmd = "`"$venvPythonw`" -m caduceo_agent --config `"$configFile`""

# Try creating the task
$schedExit = Invoke-NativeSafe { schtasks /Create /TN "CaduceoAgent" /TR $taskCmd /SC ONLOGON /RL HIGHEST /RU $env:USERNAME /F }

if ($schedExit -eq 0) {
    Write-OK "       Task Scheduler: CaduceoAgent registrato (ONLOGON)"
    # Verify task was actually created by querying it
    $verifyExit = Invoke-NativeSafe { schtasks /Query /TN "CaduceoAgent" }
    if ($verifyExit -ne 0) {
        Write-Warn "       Verifica task fallita - il task potrebbe non essere stato creato correttamente"
    }
} else {
    Write-Err "ERRORE: Task Scheduler fallito (exit=$schedExit)."
    Write-Host "       Comando manuale: schtasks /Create /TN CaduceoAgent /TR `"$taskCmd`" /SC ONLOGON /RL HIGHEST /RU $env:USERNAME /F"
    exit 1
}

# ============================================================
#  STEP 6: Start agent
# ============================================================
Write-Status "[6/6] Avvio agent..."

# Kill any existing caduceo agent processes (but not other python processes)
Get-Process -Name "python","pythonw" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like "*\.caduceo*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

# Start agent
$proc = Start-Process -FilePath $venvPython -ArgumentList "-m", "caduceo_agent", "--config", $configFile -PassThru -WindowStyle Hidden

# Wait longer and verify process is still running (not just 5 seconds)
$started = $false
for ($i = 0; $i -lt 12 -and -not $started; $i++) {  # Up to 30 seconds (12 * 2.5s)
    Start-Sleep -Milliseconds 2500
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
        $started = $true
    }
}

if ($started) {
    Write-OK "       Agent avviato con successo (PID: $($proc.Id))"
    Write-Host "       L'agent e' in esecuzione in background."
} else {
    $exitCode = $proc.ExitCode
    Write-Warn "       Agent terminato con codice: $exitCode"
    Write-Host "       Prova manualmente:"
    Write-Host "       `"$venvPython`" -m caduceo_agent --config `"$configFile`""
}

# ============================================================
# DONE
# ============================================================
Write-Host ""
Write-Host ("=" * 60)
Write-OK "  Caduceo Agent installato con successo!"
Write-Host ("=" * 60)
Write-Host ""
Write-Host "  Directory:  $CaduceoDir"
Write-Host "  Config:     $configFile"
Write-Host "  Agent ID:   $AgentId"
Write-Host "  Relay:      $RelayUrl"
Write-Host "  Python:     $venvPython"
Write-Host ""
Write-Host "  L'agent si avviera' automaticamente al prossimo login."
Write-Host "  Per avviare ora: schtasks /Run /TN CaduceoAgent"
Write-Host ""

# Cleanup temporary files
Cleanup-TempFiles