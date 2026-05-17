@echo off
title Caduceo Agent - Installazione Automatica
echo.
echo   ========================================================
echo   ^|       CADUCEO AGENT - Installazione Automatica       ^|
echo   ========================================================
echo.
echo Avvio installazione in corso...
echo.

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0install-agent-windows.ps1"

echo.
echo.
echo Premi un tasto per chiudere...
pause >nul