#!/usr/bin/env python3
"""
Caduceo Agent - Single-File Installer v0.4.0
v0.4: HMAC challenge-response PSK auth (nonce-based), backoff jitter, subprocess.run()

Usage:
  python install.py              # Install (PSK from config/env/interactive)
  python install.py --check       # Check status
  python install.py --uninstall   # Uninstall
"""

import os, sys, json, base64, subprocess, shutil, platform
from pathlib import Path

RELAY_URL = "wss://caduceo.shares.zrok.io"
PSK_HEX = "3d97d8ee4e6de4c452351fb2e4d2252a44dce8aef3fa3db5e4de2ce2402a4b05"
WHEEL_COMMON_B64 = "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"
WHEEL_AGENT_B64 = "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"
INSTALL_DIR = str(Path.home() / ".caduceo")
VENV_DIR = os.path.join(INSTALL_DIR, "venv")
SYSTEM = platform.system()

def green(s): return s if SYSTEM == "Windows" else f"\033[92m{s}\033[0m"
def red(s): return s if SYSTEM == "Windows" else f"\033[91m{s}\033[0m"
def yellow(s): return s if SYSTEM == "Windows" else f"\033[93m{s}\033[0m"
def cyan(s): return s if SYSTEM == "Windows" else f"\033[96m{s}\033[0m"

def get_psk():
    for i, arg in enumerate(sys.argv):
        if arg == "--psk" and i + 1 < len(sys.argv):
            val = sys.argv[i + 1]
            if len(val) == 64: return val
            print(red(f"  PSK non valida (deve essere 64 caratteri hex, got {len(val)})"))
            sys.exit(1)
    env_psk = os.environ.get("CADUCEO_PSK", "")
    if env_psk and len(env_psk) == 64: return env_psk
    config_file = os.path.join(INSTALL_DIR, "agent.json")
    if os.path.exists(config_file):
        try:
            with open(config_file) as f: config = json.load(f)
            existing = config.get("psk_hex", "")
            if existing and len(existing) == 64: return existing
        except: pass
    if PSK_HEX and len(PSK_HEX) == 64: return PSK_HEX
    print(yellow("  PSK non trovata. Trovala sul relay:"))
    print(yellow("    cat ~/.caduceo/relay.json | grep psk_hex"))
    psk = input("  Inserisci PSK (64 caratteri hex): ").strip()
    if not psk or len(psk) != 64:
        import secrets as _s
        psk = _s.token_hex(32)
        print(yellow("  [!] PSK generata randomicamente. DEVI aggiornare il relay!"))
    return psk

def default_agent_id():
    return (platform.node() or "unknown-pc").lower().replace(" ", "-").replace(".", "-")[:40]

def install():
    print("=" * 50)
    print(cyan("  Caduceo Agent - Installazione automatica"))
    print("=" * 50)
    agent_id = input(f"Agent ID [{default_agent_id()}]: ").strip() or default_agent_id()
    psk = get_psk()
    tags = input("Tags (csv) [caduceo]: ").strip() or "caduceo"
    print(f"\n  Relay:    {RELAY_URL}")
    print(f"  Agent ID: {agent_id}")
    print(f"  PSK:      ...{psk[-8:]}")
    print(f"  Tags:     {tags}\n")
    os.makedirs(INSTALL_DIR, exist_ok=True)
    bin_dir = "Scripts" if SYSTEM == "Windows" else "bin"
    python_ext = ".exe" if SYSTEM == "Windows" else ""
    venv_python = os.path.join(VENV_DIR, bin_dir, "python" + python_ext)
    venv_pip = os.path.join(VENV_DIR, bin_dir, "pip" + python_ext)
    if not os.path.exists(venv_python):
        print(green("Creazione virtual environment..."))
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
    else:
        print(green("Virtual environment esistente, lo riutilizzo..."))
    print(green("Estrazione pacchetti embedded..."))
    common_whl = os.path.join(INSTALL_DIR, "caduceo_common-0.1.0-py3-none-any.whl")
    agent_whl = os.path.join(INSTALL_DIR, "caduceo_agent-0.1.0-py3-none-any.whl")
    with open(common_whl, "wb") as f: f.write(base64.b64decode(WHEEL_COMMON_B64))
    with open(agent_whl, "wb") as f: f.write(base64.b64decode(WHEEL_AGENT_B64))
    print(green("Installazione pacchetti..."))
    subprocess.run([venv_pip, "install", "--force-reinstall", common_whl], check=True)
    subprocess.run([venv_pip, "install", "--force-reinstall", agent_whl], check=True)
    deps = ["websockets>=12.0", "psutil"]
    if SYSTEM == "Windows": deps += ["pywin32", "Pillow"]
    print(green("Installazione dipendenze: " + " ".join(deps)))
    subprocess.run([venv_pip, "install"] + deps, check=True)
    config = {"relay_url": RELAY_URL, "psk_hex": psk, "agent_id": agent_id,
              "tags": tags.split(","), "install_dir": INSTALL_DIR}
    config_file = os.path.join(INSTALL_DIR, "agent.json")
    with open(config_file, "w") as f: json.dump(config, f, indent=2)
    if SYSTEM != "Windows": os.chmod(config_file, 0o600)
    install_service(venv_python, config_file)
    if SYSTEM == "Windows": setup_antivirus()
    for whl in [common_whl, agent_whl]:
        if os.path.exists(whl): os.remove(whl)
    print("\n" + green("=" * 50))
    print(green("  Caduceo Agent installato con successo!"))
    print(green("=" * 50))
    print(f"  Directory:  {INSTALL_DIR}")
    print(f"  Config:     {config_file}")

def install_service(python_path, config_file):
    if SYSTEM == "Windows":
        print(green("Registrazione come Windows Task Scheduler..."))
        subprocess.run(["schtasks", "/Delete", "/TN", "CaduceoAgent", "/F"], capture_output=True)
        cmd = f'"{python_path}" -m caduceo_agent --config "{config_file}"'
        result = subprocess.run(["schtasks", "/Create", "/TN", "CaduceoAgent",
            "/TR", cmd, "/SC", "ONSTART",
            "/RU", os.environ.get("USERNAME", "%USERNAME%"),
            "/RL", "HIGHEST", "/F"], capture_output=True, text=True)
        if result.returncode == 0:
            print(green("  Task Scheduler: CaduceoAgent registrato"))
        else:
            print(yellow(f"  Task Scheduler fallito: {result.stderr.strip()}"))
            print(yellow('  Esegui manualmente come Admin:'))
            print(f'    schtasks /Create /TN CaduceoAgent /TR "\\"{python_path}\\" -m caduceo_agent" /SC ONSTART /F')
    elif SYSTEM == "Darwin":
        print(green("Registrazione launchd..."))
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.caduceo.agent.plist")
        home = os.path.expanduser("~")
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.caduceo.agent</string>
<key>ProgramArguments</key><array>
<string>{python_path}</string><string>-m</string><string>caduceo_agent</string>
<string>--config</string><string>{config_file}</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>EnvironmentVariables</key><dict>
<key>CADUCEO_ALLOWED_PATHS</key><string>{home}</string></dict>
</dict></plist>"""
        with open(plist_path, "w") as f: f.write(plist)
        subprocess.run(["launchctl", "load", plist_path], capture_output=True)
        print(green(f"  launchd: {plist_path}"))
    else:
        print(green("Registrazione systemd service..."))
        svc = f"""[Unit]
Description=Caduceo Agent - Remote AI Access
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={python_path} -m caduceo_agent --config {config_file}
Restart=always
RestartSec=10
Environment=CADUCEO_ALLOWED_PATHS={os.path.expanduser("~")}

[Install]
WantedBy=multi-user.target
"""
        svc_path = "/etc/systemd/system/caduceo-agent.service"
        try:
            with open(svc_path, "w") as f: f.write(svc)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", "caduceo-agent"], check=True)
            subprocess.run(["systemctl", "start", "caduceo-agent"], check=True)
            print(green("  systemd: caduceo-agent.service avviato"))
        except PermissionError:
            print(yellow("  Serve sudo. Copia il contenuto in " + svc_path))

def setup_antivirus():
    print(yellow("Configurazione Windows Defender..."))
    if not os.environ.get("ADMIN"):
        print(yellow("  Per esclusioni, esegui come Admin:"))
        print(yellow(f'  Add-MpPreference -ExclusionPath "{INSTALL_DIR}"'))
        return
    ps = "powershell" if shutil.which("powershell") else "pwsh"
    subprocess.run([ps, "-Command",
        f'Add-MpPreference -ExclusionPath "{INSTALL_DIR}"'], capture_output=True)

def uninstall():
    print(red("Disinstallazione Caduceo Agent..."))
    if SYSTEM == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", "CaduceoAgent", "/F"], capture_output=True)
    elif SYSTEM == "Darwin":
        plist = os.path.expanduser("~/Library/LaunchAgents/com.caduceo.agent.plist")
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        if os.path.exists(plist): os.remove(plist)
    else:
        subprocess.run(["systemctl", "stop", "caduceo-agent"], capture_output=True)
        subprocess.run(["systemctl", "disable", "caduceo-agent"], capture_output=True)
        if os.path.exists("/etc/systemd/system/caduceo-agent.service"):
            os.remove("/etc/systemd/system/caduceo-agent.service")
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    if os.path.exists(INSTALL_DIR): shutil.rmtree(INSTALL_DIR)
    print(green("Caduceo Agent disinstallato."))

def check():
    print(cyan("Caduceo Agent - Stato installazione"))
    print(f"  Directory: {INSTALL_DIR} -- {'ESISTE' if os.path.exists(INSTALL_DIR) else 'MANCANTE'}")
    config_file = os.path.join(INSTALL_DIR, "agent.json")
    if os.path.exists(config_file):
        with open(config_file) as f: config = json.load(f)
        print(f"  Config:    ESISTE")
        print(f"  Relay:     {config.get('relay_url', '?')}")
        print(f"  Agent ID:  {config.get('agent_id', '?')}")
        psk = config.get('psk_hex', '')
        print(f"  PSK:       ...{psk[-8:] if len(psk) > 8 else '****'}")
    else:
        print(f"  Config:    MANCANTE")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--uninstall": uninstall()
        elif arg == "--check": check()
        elif arg == "--psk": install()
        else: print(f"Uso: python {sys.argv[0]} [--uninstall|--check|--psk]")
    else:
        install()
