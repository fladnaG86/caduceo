@echo off
REM Caduceo Agent - Bootstrap Installer for Windows
REM Works WITHOUT Python pre-installed.
REM Usage: bootstrap-install.bat [AGENT_ID]
REM Example: bootstrap-install.bat mio-pc

setlocal enabledelayedexpansion

set AGENT_ID=%~1
if "!AGENT_ID!"=="" (
    set AGENT_ID=%COMPUTERNAME%
    echo Nessun Agent ID specificato. Uso: %COMPUTERNAME%
)

echo.
echo ============================================================
echo   Caduceo Agent - Bootstrap Installer
echo ============================================================
echo.
echo   Agent ID: !AGENT_ID!

REM Check if REAL Python exists (not just the Store stub)
set HAS_PYTHON=0
python --version >nul 2>&1
if %ERRORLEVEL%==0 (
    set HAS_PYTHON=1
) else (
    python3 --version >nul 2>&1
    if %ERRORLEVEL%==0 (
        set HAS_PYTHON=1
    )
)

if !HAS_PYTHON!==1 (
    echo [1] Python di sistema trovato - uso install.py
    echo.
    powershell -Command "Invoke-WebRequest -Uri 'https://your-relay.example.com/download/install.py?platform=windows' -OutFile '%TEMP%\caduceo-install.py' -UseBasicParsing"
    echo [2] Esecuzione install.py...
    python "%TEMP%\caduceo-install.py"
    goto :done
)

REM No working Python - delegate to PowerShell bootstrap
echo [1] Python non trovato o non funzionante
echo     Il launcher di Windows Store non e' un Python vero.
echo     Uso bootstrap con Python embeddable.
echo.
echo [2] Scaricamento bootstrap script...
powershell -Command "Invoke-WebRequest -Uri 'https://your-relay.example.com/download/bootstrap-install.ps1' -OutFile '%TEMP%\bootstrap-install.ps1' -UseBasicParsing"
echo [3] Esecuzione bootstrap...
powershell -ExecutionPolicy Bypass -File "%TEMP%\bootstrap-install.ps1" -AgentId "!AGENT_ID!"
goto :done

:done
echo.
pause