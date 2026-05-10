@echo off
REM Caduceo Agent - Quick Install (Windows)
REM Usage: powershell -ExecutionPolicy Bypass -File quick-install.ps1
REM Non-interactive: set AGENT_ID=my-pc before running
REM
REM Parameter: %1 = AGENT_ID (optional)

setlocal enabledelayedexpansion

set RELAY_URL=wss://your-relay.example.com
set PSK=CHANGE_ME_GENERATE_A_NEW_PSK
set PLATFORM=windows

if "%~1"=="" (
    echo === Caduceo Agent Quick Install ===
    echo.
    echo Scaricamento installer...
    powershell -Command "Invoke-WebRequest -Uri 'https://your-relay.example.com/download/install.py?platform=%PLATFORM%' -OutFile '%TEMP%\caduceo-install.py'"
    echo.
    echo Esecuzione installer interattivo...
    python %TEMP%\caduceo-install.py
) else (
    set AGENT_ID=%~1
    echo === Caduceo Agent Quick Install ===
    echo Agent ID: !AGENT_ID!
    echo.
    echo Creazione configurazione...
    if not exist "%USERPROFILE%\.caduceo" mkdir "%USERPROFILE%\.caduceo"
    
    echo {"relay_url": "%RELAY_URL%", "psk_hex": "%PSK%", "agent_id": "!AGENT_ID!", "tags": "caduceo"} > "%USERPROFILE%\.caduceo\agent.json"
    
    echo Scaricamento installer...
    powershell -Command "Invoke-WebRequest -Uri 'https://your-relay.example.com/download/install.py?platform=%PLATFORM%' -OutFile '%TEMP%\caduceo-install.py'"
    echo.
    echo Esecuzione installer con --config...
    python %TEMP%\caduceo-install.py
)

echo.
echo === Installazione completata ===
pause