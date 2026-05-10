@echo off
schtasks /Delete /TN CaduceoAgent /F
schtasks /Create /TN CaduceoAgent /TR "\"C:\Users\mlanterna\.caduceo\venv\Scripts\pythonw.exe\" -m caduceo_agent --relay wss://caduceo.shares.zrok.io --psk 3d97d8ee4e6de4c452351fb2e4d2252a44dce8aef3fa3db5e4de2ce2402a4b05 --agent-id o-massimo --tags caduceo" /SC ONSTART /RU mlanterna /RL HIGHEST /F
schtasks /Run /TN CaduceoAgent