@echo off
echo === Caduceo Agent Update v0.3.1 ===

REM Installa i nuovi wheel
echo Installazione wheels...
C:\Users\mlanterna\.caduceo\venv\Scripts\pip.exe install --force-reinstall C:\Users\mlanterna\caduceo_common-0.1.0-py3-none-any.whl C:\Users\mlanterna\caduceo_agent-0.1.0-py3-none-any.whl

REM Ricrea task scheduler con --config (nessun segreto visibile)
echo Aggiornamento Task Scheduler...
schtasks /Delete /TN CaduceoAgent /F
schtasks /Create /TN CaduceoAgent /TR "\"C:\Users\mlanterna\.caduceo\venv\Scripts\pythonw.exe\" -m caduceo_agent --config \"C:\Users\mlanterna\.caduceo\agent.json\"" /SC ONSTART /RU mlanterna /RL HIGHEST /F

REM Restringi permessi del config file
echo Impostazione permessi config...
icacls "C:\Users\mlanterna\.caduceo\agent.json" /inheritance:r /grant:r "mlanterna:(R)"

REM Avvia il nuovo agent
echo Avvio agent...
schtasks /Run /TN CaduceoAgent

echo === Aggiornamento completato ===