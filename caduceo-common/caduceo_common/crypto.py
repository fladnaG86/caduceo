"""Caduceo Common - Crittografia AES-256-GCM."""

import os
import json
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .constants import CRYPTO_KEY_LENGTH, CRYPTO_NONCE_LENGTH


class Crypto:
    """Crittografia simmetrica AES-256-GCM per comunicazioni agent ↔ relay."""

    def __init__(self, key: bytes | None = None):
        if key is None:
            key = AESGCM.generate_key(bit_length=256)
        if len(key) != CRYPTO_KEY_LENGTH:
            raise ValueError(f"Chiave deve essere di {CRYPTO_KEY_LENGTH} bytes, ricevuti {len(key)}")
        self._key = key
        self._aesgcm = AESGCM(key)

    @classmethod
    def from_hex(cls, hex_key: str) -> "Crypto":
        """Crea Crypto da chiave esadecimale."""
        return cls(bytes.fromhex(hex_key))

    @classmethod
    def from_b64(cls, b64_key: str) -> "Crypto":
        """Crea Crypto da chiave base64."""
        return cls(base64.b64decode(b64_key))

    @staticmethod
    def generate_key_hex() -> str:
        """Genera una nuova chiave e la restituisce in hex (per configurazione)."""
        key = AESGCM.generate_key(bit_length=256)
        return key.hex()

    def encrypt(self, plaintext: str, aad: bytes | None = None) -> dict:
        """
        Crittografa un messaggio JSON.

        Args:
            plaintext: il testo da cifrare
            aad: Additional Authenticated Data (es. tipo messaggio + request_id)
                 lega il ciphertext al contesto, impedendo replay o swapping.

        Returns:
            dict con 'nonce_b64', 'ciphertext_b64', 'aad_b64' (se presente)
        """
        nonce = os.urandom(CRYPTO_NONCE_LENGTH)
        plaintext_bytes = plaintext.encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, plaintext_bytes, aad)

        result = {
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        }
        if aad is not None:
            result["aad_b64"] = base64.b64encode(aad).decode("ascii")
        return result

    def decrypt(self, nonce_b64: str, ciphertext_b64: str, aad: bytes | None = None) -> str:
        """
        Decritta un messaggio crittografato.

        Args:
            nonce_b64: nonce in base64
            ciphertext_b64: ciphertext in base64
            aad: Additional Authenticated Data (deve corrispondere a quello usato in encrypt)

        Returns:
            str plaintext decrittato
        """
        nonce = base64.b64decode(nonce_b64)
        if len(nonce) != CRYPTO_NONCE_LENGTH:
            raise ValueError(f"Nonce deve essere di {CRYPTO_NONCE_LENGTH} bytes, ricevuti {len(nonce)}")
        ciphertext = base64.b64decode(ciphertext_b64)
        plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext, aad)
        return plaintext_bytes.decode("utf-8")

    def encrypt_message(self, message: dict, aad: bytes | None = None) -> dict:
        """Crittografa un dizionario come JSON. Utilità per messaggi WebSocket."""
        plaintext = json.dumps(message, ensure_ascii=False)
        return self.encrypt(plaintext, aad)

    def decrypt_message(self, encrypted: dict, aad: bytes | None = None) -> dict:
        """Decritta un messaggio crittografato. Utilità per messaggi WebSocket."""
        # Estrai AAD dal messaggio se presente e non passato esplicitamente
        if aad is None and "aad_b64" in encrypted:
            aad = base64.b64decode(encrypted["aad_b64"])
        plaintext = self.decrypt(encrypted["nonce_b64"], encrypted["ciphertext_b64"], aad)
        return json.loads(plaintext)