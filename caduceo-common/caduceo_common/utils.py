"""Caduceo Common - Identificativi e utilità."""

import platform
import uuid
from .constants import OS_WINDOWS, OS_LINUX, OS_MACOS


def detect_os() -> str:
    """Rileva il sistema operativo e restituisce la costante Caduceo."""
    system = platform.system().lower()
    if system == "windows":
        return OS_WINDOWS
    elif system == "darwin":
        return OS_MACOS
    else:
        return OS_LINUX


def generate_agent_id(hostname: str | None = None) -> str:
    """Genera un ID univoco per un agent basato su hostname."""
    if hostname is None:
        hostname = platform.node()
    # Normalizza: lowercase, spazi → trattini, rimuovi caratteri speciali
    agent_id = hostname.lower().strip()
    agent_id = agent_id.replace(" ", "-")
    agent_id = "".join(c for c in agent_id if c.isalnum() or c in "-_")
    if not agent_id:
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    return agent_id


def generate_request_id() -> str:
    """Genera un ID univoco per una richiesta."""
    return f"req-{uuid.uuid4().hex[:12]}"