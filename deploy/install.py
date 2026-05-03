#!/usr/bin/env python3
"""
Caduceo Agent - Installazione automatica (Windows / Linux / macOS)
Versione: 1.0.0

Uso:
  python install.py              # Installa e avvia
  python install.py --uninstall  # Rimuovi
  python install.py --check      # Verifica stato

Un solo file, zero dipendenze esterne. I pacchetti sono incorporati.
"""

import sys, os, platform, subprocess, json, tempfile, base64, shutil

# ══════════════════════════════════════════════════════════════════
# CONFIGURAZIONE
# ══════════════════════════════════════════════════════════════════
RELAY_URL = "wss://caduceo.maulanhermes.uk"
PSK_HEX = "3d97d8ee4e6de4c452351fb2e4d2252a44dce8aef3fa3db5e4de2ce2402a4b05"

# Wheel incorporati (base64)
COMMON_WHL_B64 = "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OzcUB0FWPoSxCrKMzhfBMtWaExxD6Bux2xfkwqxI2/qS22lfNwNtLY4HUH1L/icT0g700NLhyZR2q63fxjwUQRaz0xy4ov/JAGtdEuVoI6D10oz9vQfdH3MmSS9mf+8oeQINS7aj6yJwqHmq4Hl5qdJLgVBkY/UmaNok7HpWGpLrVhYnqMJLZJMOwyFPnxu9/jNQ82vxjcw6y0BA/fKc1s0d2kBr81WJ9FIegtmxcgAnKbL4m+x6QklC6icS7kTmleUV6OJKxduwBNFmr1NhI/OLpY1D5NDOuGzEBOacJL9czSiunwvvLN4W8fenM0cimugdMOaGX5uLVADNbFyx6zIscXs9Ouv9z2ck5Zc3AVN+CqYnPNAJbjPyvrYCzdQC7eGzXk9mmeJXQW8fd1WtupsbzMK00AmQQf5lnSv1KiiOFa85Xvmd+8JHtmU5D/uQ7jHOAV/vF+ao55uZfYt2Xi6yUOEXEJfewLpJBQk/VBzk/6Ip6VCdXtJ6Sm/0MSFXpne2AhTubbMcC5LPh3mobaXKjHBxPyoy6MZmDI1zQThe/mvCtwzPNznu7R9QSwECFAMUAAAACAD8U6NcU1joKuQBAABJBAAAGgAAAAAAAAAAAAAAtIEAAAAAY2FkdWNlb19jb21tb24vX19pbml0X18ucHlQSwECFAMUAAAACAD2U6Ncybyg1KQDAAA0BwAAGwAAAAAAAAAAAAAAtIEcAgAAY2FkdWNlb19jb21tb24vY29uc3RhbnRzLnB5UEsBAhQDFAAAAAgAek6jXD+68r2oAwAApwoAABgAAAAAAAAAAAAAALSB+QUAAGNhZHVjZW9fY29tbW9uL2NyeXB0by5weVBLAQIUAxQAAAAIAA1Uo1wz/QrkWwUAAC0SAAAaAAAAAAAAAAAAAAC0gdcJAABjYWR1Y2VvX2NvbW1vbi9tZXNzYWdlcy5weVBLAQIUAxQAAAAIAGZOo1xT+GLJCgEAAMYBAAAaAAAAAAAAAAAAAAC0gWoPAABjYWR1Y2VvX2NvbW1vbi9wcm90b2NvbC5weVBLAQIUAxQAAAAIAIZOo1yvU4ey/wEAABYEAAAXAAAAAAAAAAAAAAC0gawQAABjYWR1Y2VvX2NvbW1vbi91dGlscy5weVBLAQIUAxQAAAAIAEGlo1xdoubBdwAAAKQAAAAnAAAAAAAAAAAAAACkgeASAABjYWR1Y2VvX2NvbW1vbi0wLjEuMC5kaXN0LWluZm8vTUVUQURBVEFQSwECFAMUAAAACABBpaNcJ0zmilwAAABbAAAAJAAAAAAAAAAAAAAAtIGcEwAAY2FkdWNlb19jb21tb24tMC4xLjAuZGlzdC1pbmZvL1dIRUVMUEsBAhQDFAAAAAgAQaWjXOPA96URAAAADwAAACwAAAAAAAAAAAAAALSBOhQAAGNhZHVjZW9fY29tbW9uLTAuMS4wLmRpc3QtaW5mby90b3BfbGV2ZWwudHh0UEsBAhQDFAAAAAgAQaWjXC1TuXzsAQAAMgMAACUAAAAAAAAAAAAAALSBlRQAAGNhZHVjZW9fY29tbW9uLTAuMS4wLmRpc3QtaW5mby9SRUNPUkRQSwUGAAAAAAoACgAAAwAAxBYAAAAA"
AGENT_WHL_B64 = "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"

# ══════════════════════════════════════════════════════════════════
# PATH
# ══════════════════════════════════════════════════════════════════
SYSTEM = platform.system()
HOME = os.path.expanduser("~")
CADUCEO_DIR = os.path.join(HOME, ".caduceo")
PSK_FILE = os.path.join(CADUCEO_DIR, "agent.psk")
VENV_DIR = os.path.join(CADUCEO_DIR, "venv")


def get_python_bin(name):
    if SYSTEM == "Windows":
        return os.path.join(VENV_DIR, "Scripts", name)
    return os.path.join(VENV_DIR, "bin", name)


def default_agent_id():
    aid = platform.node().lower().replace(" ", "-")
    return "".join(c if c.isalnum() or c == "-" else "-" for c in aid)


def green(s):
    if SYSTEM == "Windows":
        return s
    return f"\033[92m{s}\033[0m"


def red(s):
    if SYSTEM == "Windows":
        return s
    return f"\033[91m{s}\033[0m"


def cyan(s):
    if SYSTEM == "Windows":
        return s
    return f"\033[96m{s}\033[0m"


# ══════════════════════════════════════════════════════════════════
# UNINSTALL
# ══════════════════════════════════════════════════════════════════
def uninstall():
    print("Disinstallazione Caduceo Agent...")
    if SYSTEM == "Linux":
        subprocess.run(["sudo", "systemctl", "stop", "caduceo-agent"],
                        capture_output=True)
        subprocess.run(["sudo", "systemctl", "disable", "caduceo-agent"],
                        capture_output=True)
        try:
            os.remove("/etc/systemd/system/caduceo-agent.service")
        except OSError:
            pass
        subprocess.run(["sudo", "systemctl", "daemon-reload"],
                        capture_output=True)
    elif SYSTEM == "Darwin":
        plist = os.path.join(HOME,
                              "Library/LaunchAgents/com.caduceo.agent.plist")
        subprocess.run(["launchctl", "unload", plist], capture_output=True)
        try:
            os.remove(plist)
        except OSError:
            pass
    elif SYSTEM == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", "CaduceoAgent", "/F"],
                        capture_output=True)
        subprocess.run(["powershell", "-Command",
                        f"Remove-MpPreference -ExclusionPath '{CADUCEO_DIR}'"],
                       capture_output=True)

    if os.path.exists(VENV_DIR):
        shutil.rmtree(VENV_DIR)
    for f in [PSK_FILE, os.path.join(CADUCEO_DIR, "config.json")]:
        try:
            os.remove(f)
        except OSError:
            pass

    print(green("Caduceo Agent rimosso."))


# ══════════════════════════════════════════════════════════════════
# CHECK
# ══════════════════════════════════════════════════════════════════
def check():
    if not os.path.exists(get_python_bin("python")):
        print(red("Non installato."))
        return
    r = subprocess.run([get_python_bin("python"), "-c",
                        "import caduceo_agent; print('OK')"],
                       capture_output=True, text=True)
    if "OK" in r.stdout:
        print(green("Caduceo Agent installato correttamente."))
        cfg = os.path.join(CADUCEO_DIR, "config.json")
        if os.path.exists(cfg):
            with open(cfg) as f:
                data = json.load(f)
            print(f"  Agent ID: {data.get('agent_id', '?')}")
            print(f"  Relay:    {data.get('relay_url', '?')}")
    else:
        print(red("Installazione incompleta."))


# ══════════════════════════════════════════════════════════════════
# INSTALL
# ══════════════════════════════════════════════════════════════════
def install():
    print("=" * 50)
    print(cyan("  Caduceo Agent - Installazione automatica"))
    print("=" * 50)

    agent_id = input(f"Agent ID [{default_agent_id()}]: ").strip()
    if not agent_id:
        agent_id = default_agent_id()

    print(f"  Agent ID: {agent_id}")
    print(f"  Relay:    {RELAY_URL}")
    print(f"  Sistema:  {SYSTEM}")
    print()

    # Check Python
    if sys.version_info < (3, 10):
        print(red("Python 3.10+ necessario. Scarica da https://python.org"))
        sys.exit(1)

    # Directory
    os.makedirs(CADUCEO_DIR, exist_ok=True)

    # Salva PSK
    with open(PSK_FILE, "w") as f:
        f.write(PSK_HEX)
    if SYSTEM != "Windows":
        os.chmod(PSK_FILE, 0o600)
    print(green(f"  [OK] PSK"))

    # Crea venv
    if not os.path.exists(VENV_DIR):
        print("  Creazione virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", VENV_DIR], check=True)
    print(green("  [OK] Virtual environment"))

    # Estrai e installa wheel
    with tempfile.TemporaryDirectory() as tmp:
        common_path = os.path.join(tmp, "caduceo_common-0.1.0-py3-none-any.whl")
        agent_path = os.path.join(tmp, "caduceo_agent-0.1.0-py3-none-any.whl")

        print("  Installazione caduceo-common...")
        with open(common_path, "wb") as f:
            f.write(base64.b64decode(COMMON_WHL_B64))

        print("  Installazione caduceo-agent...")
        with open(agent_path, "wb") as f:
            f.write(base64.b64decode(AGENT_WHL_B64))

        deps = ["websockets", "psutil", "pillow"]
        if SYSTEM == "Windows":
            deps.append("pywin32")

        subprocess.run([get_python_bin("pip"), "install", "--quiet",
                        common_path, agent_path] + deps, check=True)

    print(green("  [OK] Pacchetti installati"))

    # Esclusioni AV (Windows)
    if SYSTEM == "Windows":
        print("  Esclusioni Windows Defender...")
        r = subprocess.run(["powershell", "-Command",
                            f"Add-MpPreference -ExclusionPath '{CADUCEO_DIR}'"],
                           capture_output=True)
        if r.returncode == 0:
            print(green("  [OK] Esclusione AV aggiunta"))
        else:
            print("  [!] Per le esclusioni AV, esegui come Amministratore:")
            print(f"      Add-MpPreference -ExclusionPath '{CADUCEO_DIR}'")
            print("      Add-MpPreference -ExclusionProcess 'python.exe'")

    # Salva config
    config = {
        "agent_id": agent_id,
        "relay_url": RELAY_URL,
        "psk_file": PSK_FILE,
    }
    with open(os.path.join(CADUCEO_DIR, "config.json"), "w") as f:
        json.dump(config, f, indent=2)

    # Servizio
    install_service(agent_id)

    # Avvia
    print()
    start_agent(agent_id)


# ══════════════════════════════════════════════════════════════════
# SERVICE
# ══════════════════════════════════════════════════════════════════
def install_service(agent_id):
    python = get_python_bin("python")

    if SYSTEM == "Linux":
        service = f"""[Unit]
Description=Caduceo Agent - Remote AI Access
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={os.getenv('USER')}
Environment=CADUCEO_PSK_FILE={PSK_FILE}
ExecStart={python} -m caduceo_agent --relay {RELAY_URL} --agent-id {agent_id}
Restart=always
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60

StandardOutput=journal
StandardError=journal
SyslogIdentifier=caduceo-agent

[Install]
WantedBy=multi-user.target
"""
        print("  Creazione servizio systemd...")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".service",
                                          delete=False) as f:
            f.write(service)
            tmp = f.name
        subprocess.run(["sudo", "cp", tmp,
                        "/etc/systemd/system/caduceo-agent.service"],
                       check=True)
        os.unlink(tmp)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable",
                        "caduceo-agent"], check=True)
        print(green("  [OK] Servizio systemd"))

    elif SYSTEM == "Darwin":
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.caduceo.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>caduceo_agent</string>
        <string>--relay</string><string>{RELAY_URL}</string>
        <string>--agent-id</string><string>{agent_id}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict><key>CADUCEO_PSK_FILE</key><string>{PSK_FILE}</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/caduceo-agent.log</string>
    <key>StandardErrorPath</key><string>/tmp/caduceo-agent.err</string>
</dict>
</plist>"""
        plist_path = os.path.join(HOME,
                                   "Library/LaunchAgents/com.caduceo.agent.plist")
        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        with open(plist_path, "w") as f:
            f.write(plist)
        subprocess.run(["launchctl", "load", plist_path], check=True)
        print(green("  [OK] LaunchAgent"))

    elif SYSTEM == "Windows":
        pythonw = get_python_bin("pythonw")
        if not os.path.exists(pythonw):
            pythonw = python
        subprocess.run(["schtasks", "/Create",
                         "/TN", "CaduceoAgent",
                         "/TR", f'"{pythonw}" -m caduceo_agent '
                                f'--relay {RELAY_URL} '
                                f'--agent-id {agent_id}',
                         "/SC", "ONLOGON",
                         "/RL", "LIMITED",
                         "/F"], check=True)
        print(green("  [OK] Scheduled Task Windows"))


def start_agent(agent_id):
    python = get_python_bin("python")

    if SYSTEM == "Linux":
        subprocess.run(["sudo", "systemctl", "start", "caduceo-agent"],
                       check=True)
        print(green("  Caduceo Agent attivo!"))
        print(f"    Agent ID: {agent_id}")
        print(f"    Relay:    {RELAY_URL}")
        print("  Comandi: sudo systemctl status caduceo-agent")
        print("           sudo journalctl -u caduceo-agent -f")

    elif SYSTEM == "Darwin":
        print(green("  Caduceo Agent attivo!"))
        print(f"    Agent ID: {agent_id}")
        print(f"    Relay:    {RELAY_URL}")
        print("  Comandi: tail -f /tmp/caduceo-agent.log")

    elif SYSTEM == "Windows":
        choice = input("Avviare l'agent adesso? [S/n]: ").strip().lower()
        if choice in ("", "s", "si", "y", "yes"):
            pythonw = get_python_bin("pythonw")
            if os.path.exists(pythonw):
                subprocess.Popen([pythonw, "-m", "caduceo_agent",
                                  "--relay", RELAY_URL,
                                  "--agent-id", agent_id],
                                 creationflags=0x00000008)
            else:
                subprocess.Popen([python, "-m", "caduceo_agent",
                                  "--relay", RELAY_URL,
                                  "--agent-id", agent_id],
                                 creationflags=0x00000008)
            print(green("  Caduceo Agent avviato in background!"))
        print(f"    Agent ID: {agent_id}")
        print(f"    Relay:    {RELAY_URL}")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--uninstall":
            uninstall()
        elif arg == "--check":
            check()
        else:
            print("Uso: python install.py [--uninstall|--check]")
            sys.exit(1)
    else:
        install()