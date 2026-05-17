@echo off
title Caduceo Agent - Installazione Automatica
echo.
echo   ========================================================
echo   ^|       CADUCEO AGENT - Installazione Automatica       ^|
echo   ========================================================
echo.
echo Avvio installazione in corso...
echo.

powershell -ExecutionPolicy Bypass -NoProfile -Command "Set-ExecutionPolicy Bypass -Scope Process -Force; &= '%~dp0install-agent-windows.ps1'"

echo.
echo.
echo Premi un tasto per chiudere...
pause >nul