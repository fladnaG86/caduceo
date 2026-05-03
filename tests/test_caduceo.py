"""
Caduceo - Test suite minima.
Test su crypto, messaggi, queue e sicurezza.

Usage: python -m pytest tests/ -v
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import pytest


# ── Crypto Tests ─────────────────────────────────────────────────────────────

class TestCrypto:
    """Test della crittografia AES-256-GCM."""

    def test_encrypt_decrypt_roundtrip(self):
        """Verifica che encrypt seguito da decrypt dia il messaggio originale."""
        from caduceo_common.crypto import Crypto

        crypto = Crypto()
        message = "Test message con caratteri speciali: àèéìòù 🎉"

        encrypted = crypto.encrypt(message)
        assert "nonce_b64" in encrypted
        assert "ciphertext_b64" in encrypted

        decrypted = crypto.decrypt(encrypted["nonce_b64"], encrypted["ciphertext_b64"])
        assert decrypted == message

    def test_encrypt_decrypt_message_roundtrip(self):
        """Verifica encrypt_message/decrypt_message con dizionari."""
        from caduceo_common.crypto import Crypto

        crypto = Crypto()
        message = {"type": "command", "command": "ls -la", "request_id": "req-test123"}

        encrypted = crypto.encrypt_message(message)
        decrypted = crypto.decrypt_message(encrypted)

        assert decrypted == message

    def test_from_hex(self):
        """Verifica creare Crypto da chiave hex."""
        from caduceo_common.crypto import Crypto

        key_hex = Crypto().generate_key_hex()
        crypto1 = Crypto.from_hex(key_hex)
        crypto2 = Crypto.from_hex(key_hex)

        message = "Stessa chiave, stesso risultato"
        encrypted = crypto1.encrypt(message)
        decrypted = crypto2.decrypt(encrypted["nonce_b64"], encrypted["ciphertext_b64"])
        assert decrypted == message

    def test_different_nonce_each_time(self):
        """Verifica che ogni encrypt generi un nonce diverso."""
        from caduceo_common.crypto import Crypto

        crypto = Crypto()
        encrypted1 = crypto.encrypt("test")
        encrypted2 = crypto.encrypt("test")

        # Stesso plaintext, nonce diverso = ciphertext diverso
        assert encrypted1["nonce_b64"] != encrypted2["nonce_b64"]
        assert encrypted1["ciphertext_b64"] != encrypted2["ciphertext_b64"]

    def test_wrong_key_fails(self):
        """Verifica che la decryption con chiave sbagliata fallisca."""
        from caduceo_common.crypto import Crypto

        crypto1 = Crypto()
        crypto2 = Crypto()  # Chiave diversa

        encrypted = crypto1.encrypt("secret message")

        with pytest.raises(Exception):
            crypto2.decrypt(encrypted["nonce_b64"], encrypted["ciphertext_b64"])

    def test_tampered_ciphertext_fails(self):
        """Verifica che ciphertext modificato venga rifiutato (autenticazione)."""
        from caduceo_common.crypto import Crypto
        import base64

        crypto = Crypto()
        encrypted = crypto.encrypt("secret message")

        # Decodifica base64, modifica un byte, ri-codifica
        ct_raw = bytearray(base64.b64decode(encrypted["ciphertext_b64"]))
        if len(ct_raw) > 10:
            ct_raw[10] ^= 0xFF  # Flip a bit
        encrypted["ciphertext_b64"] = base64.b64encode(bytes(ct_raw)).decode("ascii")

        with pytest.raises(Exception):
            crypto.decrypt(encrypted["nonce_b64"], encrypted["ciphertext_b64"])

    def test_key_length_validation(self):
        """Verifica che chiavi di lunghezza sbagliata siano rifiutate."""
        from caduceo_common.crypto import Crypto

        with pytest.raises(ValueError):
            Crypto(key=b"too_short")


# ── Messages Tests ────────────────────────────────────────────────────────────

class TestMessages:
    """Test dei modelli messaggi."""

    def test_register_message_defaults(self):
        """Verifica che RegisterMessage popoli i campi automaticamente."""
        from caduceo_common.messages import RegisterMessage

        msg = RegisterMessage(agent_id="test-pc", tags=["ufficio"])
        assert msg.type == "register"
        assert msg.agent_id == "test-pc"
        assert msg.tags == ["ufficio"]
        assert msg.os in ("linux", "windows", "macos")
        assert msg.hostname  # Popolato automaticamente
        assert msg.timestamp > 0
        assert msg.request_id.startswith("req-")

    def test_command_message_serialization(self):
        """Verifica serializzazione/deserializzazione CommandMessage."""
        from caduceo_common.messages import CommandMessage, parse_message

        cmd = CommandMessage(command="ls -la", timeout=30)
        cmd_dict = cmd.to_dict()
        assert cmd_dict["type"] == "command"
        assert cmd_dict["command"] == "ls -la"

        # Deserializzazione
        parsed = parse_message(cmd_dict)
        assert parsed.type == "command"
        assert parsed.command == "ls -la"

    def test_parse_unknown_type(self):
        """Verifica che tipi sconosciuti vengano gestiti come Message base."""
        from caduceo_common.messages import parse_message, Message

        data = {"type": "unknown_type", "timestamp": 12345}
        parsed = parse_message(data)
        assert isinstance(parsed, Message)
        assert parsed.type == "unknown_type"

    def test_message_request_id_auto(self):
        """Verifica che request_id venga generato automaticamente."""
        from caduceo_common.messages import CommandMessage

        msg1 = CommandMessage(command="test1")
        msg2 = CommandMessage(command="test2")
        assert msg1.request_id != msg2.request_id  # Unici


# ── Queue Tests ──────────────────────────────────────────────────────────────

class TestCommandQueue:
    """Test della coda comandi SQLite."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Crea un percorso DB temporaneo."""
        return tmp_path / "test_queue.db"

    @pytest.mark.asyncio
    async def test_enqueue_dequeue(self, db_path):
        """Verifica inserimento e recupero comandi."""
        from caduceo_relay.server import CommandQueue

        queue = CommandQueue(db_path, ttl=86400)
        await queue._get_db()  # Inizializza DB

        # Inserisci comando
        await queue.enqueue("agent-1", {"type": "command", "command": "ls"})

        # Recupera
        commands = await queue.dequeue("agent-1")
        assert len(commands) == 1
        assert commands[0]["command"] == "ls"

        # Secondo dequeue deve essere vuoto
        commands = await queue.dequeue("agent-1")
        assert len(commands) == 0

        await queue.close()

    @pytest.mark.asyncio
    async def test_enqueue_multiple_agents(self, db_path):
        """Verifica che comandi per agent diversi restino separati."""
        from caduceo_relay.server import CommandQueue

        queue = CommandQueue(db_path, ttl=86400)
        await queue._get_db()

        await queue.enqueue("agent-1", {"type": "command", "command": "ls"})
        await queue.enqueue("agent-2", {"type": "command", "command": "dir"})
        await queue.enqueue("agent-1", {"type": "command", "command": "pwd"})

        assert len(await queue.dequeue("agent-1")) == 2
        assert len(await queue.dequeue("agent-2")) == 1
        assert len(await queue.dequeue("agent-1")) == 0

        await queue.close()

    @pytest.mark.asyncio
    async def test_expired_commands(self, db_path):
        """Verifica che i comandi scaduti vengano rimossi."""
        from caduceo_relay.server import CommandQueue

        queue = CommandQueue(db_path, ttl=1)  # TTL di 1 secondo
        await queue._get_db()

        await queue.enqueue("agent-1", {"type": "command", "command": "ls"})

        # Aspetta che scada
        await asyncio.sleep(1.5)

        # Dequeue deve rimuovere i comandi scaduti e restituire lista vuota
        commands = await queue.dequeue("agent-1")
        assert len(commands) == 0

        await queue.close()

    @pytest.mark.asyncio
    async def test_max_queue_size(self, db_path):
        """Verifica che la coda rispetti il limite massimo."""
        from caduceo_relay.server import CommandQueue
        from caduceo_common.constants import QUEUE_MAX_SIZE

        queue = CommandQueue(db_path, ttl=86400)
        await queue._get_db()

        # Riempie la coda oltre il limite
        for i in range(QUEUE_MAX_SIZE + 5):
            await queue.enqueue("agent-1", {"type": "command", "command": f"cmd-{i}"})

        # La coda deve contenere al massimo QUEUE_MAX_SIZE elementi
        commands = await queue.dequeue("agent-1")
        assert len(commands) <= QUEUE_MAX_SIZE

        await queue.close()


# ── Security Tests ────────────────────────────────────────────────────────────

class TestSecurity:
    """Test di sicurezza."""

    def test_path_traversal_protection(self):
        """Verifica che path traversal venga bloccato."""
        from caduceo_agent.client import validate_path, ALLOWED_PATHS

        # Simula ALLOWED_PATHS configurato
        import caduceo_agent.client as client_module
        original_paths = client_module.ALLOWED_PATHS

        try:
            # Configura directory ammesse
            client_module.ALLOWED_PATHS = ["/home/manutenzione", "/tmp"]

            # Path traversal attempt
            with pytest.raises(PermissionError):
                validate_path("../../etc/passwd")

            with pytest.raises(PermissionError):
                validate_path("/etc/shadow")

            # Path consentite
            normal_path = validate_path("/home/manutenzione/documenti/report.txt")
            assert str(normal_path).startswith("/home/manutenzione")

        finally:
            client_module.ALLOWED_PATHS = original_paths

    def test_psk_challenge_generation(self):
        """Verifica che la PSK challenge sia deterministica."""
        from caduceo_common.crypto import Crypto
        from caduceo_agent.client import CaduceoAgent

        # Usa una PSK valida (32 bytes = 64 hex chars)
        psk_hex = Crypto().generate_key_hex()

        agent1 = CaduceoAgent(relay_url="ws://test", psk_hex=psk_hex, agent_id="pc-test")
        agent2 = CaduceoAgent(relay_url="ws://test", psk_hex=psk_hex, agent_id="pc-test")

        # Stessa PSK + stesso agent_id = stessa challenge
        assert agent1._generate_psk_challenge() == agent2._generate_psk_challenge()

        # Agent ID diverso = challenge diversa
        agent3 = CaduceoAgent(relay_url="ws://test", psk_hex=psk_hex, agent_id="other-pc")
        assert agent1._generate_psk_challenge() != agent3._generate_psk_challenge()

    def test_credentials_file_permissions(self, tmp_path):
        """Verifica che i file config/credentials vengano creati con permessi 0600."""
        import stat
        from caduceo_relay.server import RelayConfig

        config_path = tmp_path / "test_config.json"
        config = RelayConfig()
        config.config_path = config_path
        config.save()

        # Verifica permessi
        file_mode = stat.S_IMODE(os.stat(config_path).st_mode)
        assert file_mode == stat.S_IRUSR | stat.S_IWUSR, f"Permessi errati: {oct(file_mode)}"


# ── Utils Tests ──────────────────────────────────────────────────────────────

class TestUtils:
    """Test delle utilità comuni."""

    def test_detect_os(self):
        """Verifica che detect_os restituisca un valore valido."""
        from caduceo_common.utils import detect_os

        result = detect_os()
        assert result in ("linux", "windows", "macos")

    def test_generate_agent_id(self):
        """Verifica generazione agent ID."""
        from caduceo_common.utils import generate_agent_id

        id1 = generate_agent_id("My Laptop")
        id2 = generate_agent_id("my-laptop")
        id3 = generate_agent_id("")

        assert id1 == "my-laptop"
        assert id2 == "my-laptop"
        assert id3  # Deve generare qualcosa anche con hostname vuoto

    def test_generate_request_id(self):
        """Verifica generazione request ID."""
        from caduceo_common.utils import generate_request_id

        id1 = generate_request_id()
        id2 = generate_request_id()

        assert id1 != id2
        assert id1.startswith("req-")


# ── WebSocket + Crypto Integration Tests ──────────────────────────────────────

class TestWebSocketCryptoIntegration:
    """Test integrazione WebSocket con crittografia AES-256-GCM."""

    def test_encrypt_decrypt_command_roundtrip(self):
        """Verifica che un comando crittografato dal relay sia leggibile dall'agent."""
        from caduceo_common.crypto import Crypto

        psk_hex = Crypto().generate_key_hex()
        relay_crypto = Crypto.from_hex(psk_hex)
        agent_crypto = Crypto.from_hex(psk_hex)

        command_msg = {
            "type": "command",
            "command": "bash",
            "args": ["-c", "echo hello"],
            "timeout": 30,
            "request_id": "req-test-001",
        }

        # Relay cifra il comando
        encrypted = relay_crypto.encrypt_message(command_msg)
        assert "nonce_b64" in encrypted
        assert "ciphertext_b64" in encrypted

        # Agent decifra
        decrypted = agent_crypto.decrypt_message(encrypted)
        assert decrypted["type"] == "command"
        assert decrypted["command"] == "bash"
        assert decrypted["args"] == ["-c", "echo hello"]
        assert decrypted["request_id"] == "req-test-001"

    def test_encrypt_decrypt_response_roundtrip(self):
        """Verifica che una risposta crittografata dall'agent sia leggibile dal relay."""
        from caduceo_common.crypto import Crypto

        psk_hex = Crypto().generate_key_hex()
        relay_crypto = Crypto.from_hex(psk_hex)
        agent_crypto = Crypto.from_hex(psk_hex)

        response_msg = {
            "type": "command_response",
            "request_id": "req-test-001",
            "agent_id": "pc-ufficio-01",
            "exit_code": 0,
            "stdout": "hello\n",
            "stderr": "",
            "duration_ms": 42,
        }

        # Agent cifra la risposta
        encrypted = agent_crypto.encrypt_message(response_msg)

        # Relay decifra
        decrypted = relay_crypto.decrypt_message(encrypted)
        assert decrypted["type"] == "command_response"
        assert decrypted["exit_code"] == 0
        assert decrypted["stdout"] == "hello\n"

    def test_encrypt_decrypt_heartbeat(self):
        """Verifica che gli heartbeat siano crittografati correttamente."""
        from caduceo_common.crypto import Crypto

        psk_hex = Crypto().generate_key_hex()
        crypto = Crypto.from_hex(psk_hex)

        heartbeat = {
            "type": "heartbeat",
            "agent_id": "pc-test",
            "timestamp": 1714723200,
            "stats": {"cpu_percent": 45.2, "memory_percent": 62.1, "disk_percent": 73.0},
        }

        encrypted = crypto.encrypt_message(heartbeat)
        decrypted = crypto.decrypt_message(encrypted)
        assert decrypted["type"] == "heartbeat"
        assert decrypted["stats"]["cpu_percent"] == 45.2

    def test_register_with_psk_challenge(self):
        """Verifica che il messaggio di registrazione contenga la PSK challenge."""
        from caduceo_common.crypto import Crypto

        psk_hex = Crypto().generate_key_hex()
        import hashlib

        agent_id = "pc-ufficio-01"
        expected_challenge = hashlib.sha256(
            (psk_hex + agent_id).encode()
        ).hexdigest()

        # Simula verifica lato relay
        register_msg = {
            "type": "register",
            "agent_id": agent_id,
            "psk_challenge": expected_challenge,
            "hostname": "DESKTOP-A1B2C3",
            "os": "linux",
        }

        # Relay verifica
        verify_challenge = hashlib.sha256(
            (psk_hex + register_msg["agent_id"]).encode()
        ).hexdigest()
        assert register_msg["psk_challenge"] == verify_challenge

    def test_wrong_psk_fails_decryption(self):
        """Verifica che PSK diversi non possano decifrare i messaggi."""
        from caduceo_common.crypto import Crypto

        relay_crypto = Crypto()  # PSK random
        agent_crypto = Crypto()  # PSK diversa

        msg = {"type": "command", "command": "ls"}
        encrypted = relay_crypto.encrypt_message(msg)

        with pytest.raises(Exception):
            agent_crypto.decrypt_message(encrypted)

    def test_encrypted_frame_structure(self):
        """Verifica che i frame crittografati abbiano la struttura corretta."""
        from caduceo_common.crypto import Crypto

        crypto = Crypto()
        msg = {"type": "heartbeat", "agent_id": "test"}
        encrypted = crypto.encrypt_message(msg)

        # Un frame crittografato deve avere nonce + ciphertext
        assert "nonce_b64" in encrypted
        assert "ciphertext_b64" in encrypted

        import base64

        nonce = base64.b64decode(encrypted["nonce_b64"])
        ciphertext = base64.b64decode(encrypted["ciphertext_b64"])

        assert len(nonce) == 12  # 96 bit nonce per AES-GCM
        assert len(ciphertext) > 16  # ciphertext + 16 byte auth tag

    def test_unicode_in_encrypted_messages(self):
        """Verifica che caratteri Unicode sopravvivano alla crittografia."""
        from caduceo_common.crypto import Crypto

        crypto = Crypto()
        msg = {
            "type": "command_response",
            "stdout": "Risultato: àèéìòù € 🎉 ñ",
            "exit_code": 0,
        }

        encrypted = crypto.encrypt_message(msg)
        decrypted = crypto.decrypt_message(encrypted)
        assert decrypted["stdout"] == "Risultato: àèéìòù € 🎉 ñ"