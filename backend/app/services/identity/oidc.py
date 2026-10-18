"""OpenID Connect relying party used for SPA: discovery, authorization code flow with PKCE (S256) and nonce, and ID
token verification with the issuer's JWKS (RS256)."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt

TIMEOUT = 10.0
CACHE_SECONDS = 3600


def random_token() -> str:
    return secrets.token_urlsafe(32)


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


@dataclass
class Request:
    """What a sign-in carries from the start to the callback."""
    state: str = field(default_factory=random_token)
    nonce: str = field(default_factory=random_token)
    verifier: str = field(default_factory=random_token)


@dataclass
class Client:
    issuer: str
    client_id: str
    secret: str
    redirect_uri: str
    scopes: list[str]
    extra: dict[str, str] = field(default_factory=dict)


class OidcError(Exception):
    pass


class Provider:
    """One OpenID Connect issuer. The discovery document and the keys are cached for an hour; the keys are fetched
    again at once when a token names a key that is not known yet (key rotation)."""

    def __init__(self, issuer: str, transport: httpx.AsyncBaseTransport | None = None):
        self.issuer = issuer.rstrip("/")
        self._transport = transport
        self._meta: tuple[float, dict] | None = None
        self._jwks: tuple[float, dict] | None = None

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport)

    async def _json(self, url: str, *, method: str = "GET", data: dict | None = None) -> dict:
        try:
            async with self.http() as h:
                r = await h.request(method, url, data=data, headers={"accept": "application/json"})
        except httpx.HTTPError as e:
            raise OidcError(f"{url}: {e}") from e
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code >= 400:
            d = body if isinstance(body, dict) else {}
            raise OidcError(f"{url}: HTTP {r.status_code} {d.get('error', '')} {d.get('error_description', '')}".strip())
        if not isinstance(body, dict):
            raise OidcError(f"{url}: the answer is not JSON")
        return body

    async def discover(self) -> dict:
        if self._meta and time.monotonic() - self._meta[0] < CACHE_SECONDS:
            return self._meta[1]
        m = await self._json(f"{self.issuer}/.well-known/openid-configuration")
        if str(m.get("issuer", "")).rstrip("/") != self.issuer:
            raise OidcError(f"The discovery document of {self.issuer} names the issuer {m.get('issuer')}")
        if not m.get("authorization_endpoint") or not m.get("token_endpoint") or not m.get("jwks_uri"):
            raise OidcError(f"The discovery document of {self.issuer} lacks endpoints")
        self._meta = (time.monotonic(), m)
        return m

    async def auth_url(self, c: Client, r: Request) -> str:
        m = await self.discover()
        q = {
            "client_id": c.client_id, "response_type": "code", "redirect_uri": c.redirect_uri,
            "scope": " ".join(c.scopes), "state": r.state, "nonce": r.nonce,
            "code_challenge": pkce_challenge(r.verifier), "code_challenge_method": "S256", **c.extra,
        }
        ep = m["authorization_endpoint"]
        return f"{ep}{'&' if '?' in ep else '?'}{urlencode(q)}"

    async def exchange(self, c: Client, code: str, verifier: str) -> dict:
        m = await self.discover()
        t = await self._json(m["token_endpoint"], method="POST", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": c.redirect_uri, "client_id": c.client_id,
            "client_secret": c.secret, "code_verifier": verifier,
        })
        if not t.get("id_token"):
            raise OidcError("The token endpoint returned no ID token")
        return t

    async def _keys(self, uri: str, kid: str | None) -> dict:
        hit = self._jwks
        stale = not hit or time.monotonic() - hit[0] > CACHE_SECONDS
        if stale or (kid and not any(k.get("kid") == kid for k in hit[1].get("keys", []))):
            hit = (time.monotonic(), await self._json(uri))
            self._jwks = hit
        return hit[1]

    async def verify(self, c: Client, id_token: str, nonce: str, algorithms: tuple[str, ...] = ("RS256",)) -> dict:
        """Checks the signature (key from the JWKS), issuer, audience, expiry and nonce."""
        m = await self.discover()
        try:
            # Only the header, to pick the key from the JWKS: jwt.decode below verifies the signature with it.
            header = jwt.get_unverified_header(id_token)  # NOSONAR
        except jwt.PyJWTError as e:
            raise OidcError(f"The ID token is malformed: {e}") from e
        if header.get("alg") not in algorithms:
            raise OidcError("The ID token is signed with an unsupported algorithm")
        jwks = await self._keys(m["jwks_uri"], header.get("kid"))
        key = next((k for k in jwks.get("keys", []) if not header.get("kid") or k.get("kid") == header.get("kid")), None)
        if key is None:
            raise OidcError("The ID token is signed with an unknown key")
        try:
            claims = jwt.decode(id_token, jwt.PyJWK(key).key, algorithms=[header["alg"]], audience=c.client_id,
                                issuer=self.issuer, leeway=60, options={"require": ["exp", "iat", "sub"]})
        except jwt.PyJWTError as e:
            raise OidcError(f"The ID token is not valid: {e}") from e
        if claims.get("nonce") != nonce:
            raise OidcError("The ID token has a wrong nonce")
        return claims


def strings(v) -> list[str]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return [v] if isinstance(v, str) else []


def b64url_json(raw: str) -> dict:
    pad = "=" * (-len(raw) % 4)
    return json.loads(base64.urlsafe_b64decode(raw + pad).decode())
