@echo off
REM ============================================================
REM  Caduceo LOCAL Agent - Launcher (installazione manuale)
REM  ------------------------------------------------------------
REM  Installa l'agente Caduceo come servizio NSSM in
REM  C:\ProgramData\caduceo. Usa install-agent-gpo.ps1.
REM  Eseguire come Amministratore (o tramite GPO come SYSTEM).
REM  NON richiede WinRM ne' internet sulla macchina.
REM ============================================================
setlocal

REM Trova la cartella dello script (gestisce spazi nel percorso)
set "PS1_FILE=%~dp0install-agent-gpo.ps1"

if not exist "%PS1_FILE%" (
    echo [ERRORE] Non trovo install-agent-gpo.ps1 accanto a questo file.
    echo          Assicurati che entrambi i file siano nella stessa cartella.
    pause
    exit /b 1
)

echo [INFO] Installazione agente Caduceo...
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%PS1_FILE%"
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo.
    echo [OK] Installazione completata. L'agente si avviera' automaticamente.
) else (
    echo.
    echo [ERRORE] Installazione fallita (codice %EXIT_CODE%).
    echo          Controlla i log in C:\ProgramData\caduceo\install.log
)

echo.
pause
exit /b %EXIT_CODE%
