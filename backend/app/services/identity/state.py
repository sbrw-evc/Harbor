"""Signed, expiring values for the steps of a sign-in (the OpenID Connect state): nothing is kept in memory, so any
replica of Harbor can finish what another started. The key is kept in OpenBao."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from ...domain.errors import DomainError
from ...ports import SecretStore

PATH = "identity/state"


class StateSigner:
    def __init__(self, secrets_store: SecretStore):
        self._secrets = secrets_store
        self._key: bytes | None = None

    async def _get_key(self) -> bytes:
        if self._key is None:
            stored = await self._secrets.get(PATH)
            if not stored or not stored.get("key"):
                stored = {"key": secrets.token_urlsafe(48)}
                await self._secrets.put(PATH, stored)
            self._key = stored["key"].encode()
        return self._key

    async def sign(self, value: dict, ttl: int) -> str:
        body = base64.urlsafe_b64encode(json.dumps({**value, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode()).rstrip(b"=")
        mac = hmac.new(await self._get_key(), body, hashlib.sha256).digest()
        return f"{body.decode()}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"

    async def open(self, token: str | None) -> dict:
        """The signed value; raises when it was changed or has expired."""
        fail = DomainError("The sign-in took too long or was started in another browser. Try again.")
        if not token or "." not in token:
            raise fail
        body, mac = token.rsplit(".", 1)
        want = base64.urlsafe_b64encode(hmac.new(await self._get_key(), body.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(want, mac):
            raise fail
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if value.get("exp", 0) < time.time():
            raise fail
        return value
