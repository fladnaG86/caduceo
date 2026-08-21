"""
oidc_auth.py — Validazione di token OIDC emessi da Authentik per Caduceo.

POC — integrazione futura del login via Authentik nel relay Caduceo.
Questo modulo e' isolato e NON modifica il relay di produzione: va copiato
nel relay (caduceo-relay/caduceo_relay/) quando l'integrazione sara' ufficiale.

Obiettivo: quando la console ottiene un token da Authentik (flusso OIDC),
il relay deve VALIDARLO prima di accettarlo come autenticazione. La validazione
usa le JWKS (chiavi pubbliche RS256) esposte da Authentik — NON bisogna mai
fidarsi di un token senza verificarne la firma e l'emittente.

Ruoli:
  - Authentik espone l'utente, i suoi gruppi e gli attributi.
  - Mappiamo i gruppi di Authentik ai ruoli Caduceo esistenti: admin/viewer.
  - In questo POC: un gruppo chiamato 'admin' => ruolo admin, altrimenti viewer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import jwt  # PyJWT


@dataclass
class OIDCConfig:
    """Parametri di connessione al provider Authentik."""

    issuer: str = ""  # es. http://127.0.0.1:9000/application/o/caduceo/
    client_id: str = ""
    redirect_uri: str = "http://localhost:8100/callback"
    # Mappatura gruppo Authentik -> ruolo Caduceo
    group_to_role: dict = field(default_factory=lambda: {"admin": "admin"})
    default_role: str = "viewer"


class OIDCValidator:
    """Carica discovery + JWKS di Authentik e valida i token."""

    def __init__(self, cfg: OIDCConfig):
        self.cfg = cfg
        self._jwks_client = None
        self._discovery: dict = {}

    # ── Utility ────────────────────────────────────────────────

    def _get(self, url: str) -> dict:
        import urllib.request

        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def load_discovery(self) -> dict:
        """Scarica il .well-known/openid-configuration dell'issuer."""
        if not self.cfg.issuer:
            raise ValueError("Issuer non configurato (AUTHENTIK_ISSUER)")
        disc_url = self._discovery_url()
        self._discovery = self._get(disc_url)
        return self._discovery

    def _discovery_url(self) -> str:
        base = self.cfg.issuer.rstrip("/")
        # Authentik espone discovery sotto /application/o/<provider>/ e anche /application/o/<provider>/.well-known/openid-configuration
        # Se issuer già finisce con .well-known, usalo; altrimenti appendi
        if base.endswith(".well-known/openid-configuration"):
            return base
        return base + "/.well-known/openid-configuration"

    def get_jwks(self):
        if self._jwks_client is None:
            disc = self.load_discovery()
            jwks_uri = disc.get("jwks_uri") or (self.cfg.issuer.rstrip("/") + "/jwks")
            self._jwks_client = jwt.PyJWKClient(jwks_uri)
        return self._jwks_client

    # ── Endpoint (per il flusso) ───────────────────────────────

    def authorization_url(self, state: str, code_verifier: str) -> str:
        disc = self.load_discovery()
        auth_endpoint = disc.get("authorization_endpoint")
        params = {
            "response_type": "code",
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.redirect_uri,
            "scope": "openid profile email offline_access",
            "state": state,
            "code_challenge": self._s256(code_verifier),
            "code_challenge_method": "S256",
        }
        qs = urllib.parse.urlencode(params)
        return f"{auth_endpoint}?{qs}"

    def _s256(self, verifier: str) -> str:
        return (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

    def token_endpoint(self) -> str:
        disc = self.load_discovery()
        return disc.get("token_endpoint", "")

    def exchange_code(self, code: str, code_verifier: str) -> dict:
        """Scambia l'authorization code con i token. Uso in /callback."""
        import urllib.request

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.cfg.redirect_uri,
            "client_id": self.cfg.client_id,
            "code_verifier": code_verifier,
        }
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            self.token_endpoint(), data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    # ── Validazione ────────────────────────────────────────────

    def validate_access_token(self, access_token: str) -> dict:
        """Valida l'access_token RS256 contro le JWKS di Authentik.

        Restituisce i claims (payload) se valido, altrimenti solleva.
        Non affidarsi mai a un token senza questa verifica.
        """
        jwks = self.get_jwks()
        signing_key = jwks.get_signing_key_from_jwt(access_token)
        payload = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.cfg.client_id,
            issuer=self._extract_issuer(),
            options={"verify_exp": True},
        )
        return payload

    def _extract_issuer(self) -> str:
        disc = self.load_discovery()
        return disc.get("issuer", self.cfg.issuer.rstrip("/"))

    def map_role(self, claims: dict) -> str:
        """Mappa l'utente Authentic a un ruolo Caduceo in base ai gruppi."""
        groups = claims.get("groups") or claims.get("realm_access", {}).get("roles") or []
        if isinstance(groups, str):
            groups = [groups]
        for g in groups:
            if g in self.cfg.group_to_role:
                return self.cfg.group_to_role[g]
        return self.cfg.default_role

    def build_caduceo_claims(self, access_token: str) -> dict:
        """Ritorna i claim pronti per il JWT Caduceo: sub + role + email."""
        claims = self.validate_access_token(access_token)
        return {
            "sub": claims.get("preferred_username") or claims.get("sub") or "unknown",
            "email": claims.get("email", ""),
            "role": self.map_role(claims),
            "issuer": claims.get("iss", ""),
            "auth_provider": "authentik",
        }

    def decode_id_token(self, id_token: str) -> dict:
        """Decodifica l'id_token OIDC verificando firma RS256 via JWKS.

        L'id_token contiene i claim utente leggibili (preferred_username, name,
        email). Fallisce se la firma/issuer/audience non sono validi.
        """
        jwks = self.get_jwks()
        signing_key = jwks.get_signing_key_from_jwt(id_token)
        return jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.cfg.client_id,
            issuer=self._extract_issuer(),
            options={"verify_exp": True},
        )

    def decode_id_token_loose(self, id_token: str) -> dict:
        """Decodifica l'id_token senza validare la firma.

        POC fallback: usato solo per leggere il nome utente da un id_token
        che arriva già dal relay su connessione TLS. In produzione va sempre
        verificata la firma (decode_id_token). Non usare per decisioni di auth.
        """
        try:
            return jwt.decode(id_token, options={"verify_signature": False})
        except Exception:
            return {}

    def get_userinfo(self, access_token: str) -> dict:
        """Interroga l'userinfo endpoint di Authentik con l'access_token.

        Restituisce i claim utente (preferred_username, name, email) in modo
        affidabile — più completo dell'id_token, che può ometterli.
        """
        disc = self.load_discovery()
        userinfo_uri = disc.get("userinfo_endpoint")
        if not userinfo_uri:
            return {}
        req = urllib.request.Request(
            userinfo_uri,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)


# ── Punto di integrazione nel relay (esempio) ──────────────────
#
# Nel server.py del relay, si sostituisce/precede la verifica JWT esistente
# con la validazione dei token OIDC di Authentik. Esempio (in aggiunta a verify_token):
#
#   oidc_cfg = OIDCConfig(
#       issuer=os.environ.get("AUTHENTIK_ISSUER",""),
#       client_id=os.environ.get("AUTHENTIK_CLIENT_ID",""),
#   )
#   oidc = OIDCValidator(oidc_cfg)
#
#   def verify_oidc_or_local(authorization=Header(...)):
#       # 1) prova a validare come token OIDC Authentik
#       try:
#           claims = oidc.build_caduceo_claims(token_val)
#           return {"sub": claims["sub"], "role": claims["role"]}
#       except Exception:
#           # 2) fallback al JWT interno esistente (bootstrap)
#           return verify_token(authorization)
#
# e si usano gli endpoint /api/auth/oidc/start e /api/auth/oidc/callback nel relay
# per i redirect di login, oppure si delega al frontend il flusso OIDC completo.
