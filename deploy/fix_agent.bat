@echo off
C:\Users\mlanterna\.caduceo\venv\Scripts\pip.exe install --force-reinstall C:\Users\mlanterna\caduceo_common-0.1.0-py3-none-any.whl C:\Users\mlanterna\caduceo_agent-0.1.0-py3-none-any.whl
schtasks /Delete /TN CaduceoAgent /F
schtasks /Create /TN CaduceoAgent /TR "\"C:\Users\mlanterna\.caduceo\venv\Scripts\pythonw.exe\" -m caduceo_agent --config \"C:\Users\mlanterna\.caduceo\agent.json\"" /SC ONSTART /RU mlanterna /RL HIGHEST /F
icacls "C:\Users\mlanterna\.caduceo\agent.json" /inheritance:r /grant:r "mlanterna:(R)"
schtasks /Run /TN CaduceoAgent