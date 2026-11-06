@echo off
REM Caduceo Remote Desktop - Installer
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0install-agent-full.ps1" %*
