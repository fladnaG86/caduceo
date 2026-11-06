@echo off
REM ============================================================
REM   Caduceo Agent - Windows Installer (Self-Contained)
REM   Uses local .ps1 and .whl files from this .zip — no downloads
REM   from zrok.io. Python embeddable and pip are still fetched
REM   from python.org / pypa.io if Python is not installed.
REM
REM   Uso:  install-agent.bat [AGENT_ID]
REM   Es:   install-agent.bat my-pc
REM         install-agent.bat ufficio
REM ============================================================

setlocal enabledelayedexpansion
set AGENT_ID=%~1

REM ============================================================
REM   CONFIGURATION: Relay URL (Tailscale)
REM   Change this if your relay address changes.
REM ============================================================
set RELAY_URL=wss://your-relay.example.com

if "!AGENT_ID!"=="" (
    set AGENT_ID=%COMPUTERNAME%
    echo Nessun Agent ID specificato. Uso: %COMPUTERNAME%
    echo.
)

echo ============================================================
echo   Caduceo Agent Installer
echo ============================================================
echo   Agent ID: !AGENT_ID!
echo   Relay:    %RELAY_URL%
echo.

echo Esecuzione installazione locale...
echo.

REM %~dp0 = directory of THIS batch file (with trailing backslash)
REM Ensures install-agent.ps1 is found even if user runs from a different cwd
powershell -ExecutionPolicy Bypass -File "%~dp0install-agent.ps1" -AgentId "!AGENT_ID!" -RelayUrl "%RELAY_URL%"

echo.
pause