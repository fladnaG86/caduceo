@echo off
REM ============================================================
REM   Caduceo Agent v0.5.0 - Self-Contained Installer
REM   Download and run this file on ANY Windows PC.
REM   No Python needed - it will be downloaded automatically.
REM ============================================================
REM
REM   Usage:  caduceo-setup.bat [AGENT_ID]
REM   Example: caduceo-setup.bat mio-pc
REM
REM   Or double-click to run (uses PC name as Agent ID)

setlocal enabledelayedexpansion

set AGENT_ID=%~1
if "!AGENT_ID!"=="" (
    set AGENT_ID=%COMPUTERNAME%
    echo Nessun Agent ID specificato. Uso: %COMPUTERNAME%
)

echo.
echo ============================================================
echo   Caduceo Agent v0.5.0 - Bootstrap Installer
echo   (HMAC Challenge-Response Auth)
echo ============================================================
echo.

REM Download the PowerShell installer
echo [1] Scaricamento installer...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://caduceo.shares.zrok.io/download/caduceo-setup.ps1' -OutFile '%TEMP%\caduceo-setup.ps1' -UseBasicParsing"

if not exist "%TEMP%\caduceo-setup.ps1" (
    echo ERRORE: Download fallito. Controlla la connessione internet.
    pause
    exit /b 1
)

echo [2] Esecuzione installer (Agent ID: !AGENT_ID!)...
echo.
powershell -ExecutionPolicy Bypass -File "%TEMP%\caduceo-setup.ps1" -AgentId "!AGENT_ID!"

echo.
pause