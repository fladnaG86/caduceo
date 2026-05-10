@echo off
REM ============================================================
REM   Caduceo Agent - Windows Installer
REM   Funziona CON o SENZA Python installato
REM   Gestisce automaticamente lo stub del Windows Store
REM
REM   Uso:  install-agent.bat [AGENT_ID]
REM   Es:   install-agent.bat my-pc
REM         install-agent.bat ufficio
REM ============================================================

setlocal enabledelayedexpansion
set AGENT_ID=%~1

if "!AGENT_ID!"=="" (
    set AGENT_ID=%COMPUTERNAME%
    echo Nessun Agent ID specificato. Uso: %COMPUTERNAME%
    echo.
)

echo ============================================================
echo   Caduceo Agent Installer
echo ============================================================
echo   Agent ID: !AGENT_ID!
echo.
echo Scaricamento script di installazione...

powershell -Command "Invoke-WebRequest -Uri 'https://your-relay.example.com/download/install-agent.ps1' -OutFile '%TEMP%\install-agent.ps1' -UseBasicParsing"

echo Esecuzione installazione...
echo.

powershell -ExecutionPolicy Bypass -File "%TEMP%\install-agent.ps1" -AgentId "!AGENT_ID!"

echo.
pause