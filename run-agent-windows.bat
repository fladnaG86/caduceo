@echo off
title Caduceo Agent
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0run-agent-windows.ps1"
pause