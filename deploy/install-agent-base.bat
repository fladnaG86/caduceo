@echo off
REM Caduceo Assistente Remoto IA - Installer
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0install-agent-base.ps1" %*
