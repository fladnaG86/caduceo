@echo off
title Caduceo Agent - Installazione Automatica
echo.
echo   ========================================
echo   ^|    CADUCEO AGENT - Windows Setup     ^|
echo   ^|    Installazione automatica           ^|
echo   ========================================
echo.
echo Avvio installazione...
echo.

powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0install-agent-windows.ps1"

echo.
echo.
echo Premi un tasto per chiudere...
pause >nul