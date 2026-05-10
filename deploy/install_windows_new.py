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

RELAY_URL = "wss://your-relay.example.com"
PSK_HEX = "CHANGE_ME_GENERATE_A_NEW_PSK"
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
