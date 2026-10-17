"""Sessions of Harbor and the local sign-in.

People sign in either locally (``auth_mode`` "local": a user name, a password and optionally a TOTP code) or through
Keystone / SPA (``auth_mode`` "keystone", services/identity/spa.py). Either way the application starts its own session:
an opaque token in an HttpOnly cookie, kept in PostgreSQL only as a SHA-256 hash.

Secrets of local users never go to PostgreSQL: the argon2id password hash and the TOTP secret live in OpenBao at
``local/users/<id>/password`` and ``local/users/<id>/totp``. PostgreSQL keeps what is not secret: whether TOTP is on,
whether the password must be changed, the failed attempts and the lockout."""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import io
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass

import pyotp
import qrcode
import qrcode.image.svg
from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from ..db.base import Database
from ..db.models import PanelSession, PanelUser
from ..db.repositories import SessionRepo, UserRepo
from ..domain.errors import DomainError, Unauthorized
from ..ports import SecretStore
from .audit import AuditService
from .common import Actor, current, utcnow

MAX_FAILURES = 5
LOCK_MINUTES = 10
MIN_PASSWORD = 10
PASSWORD_PATH = "local/users/{}/password"
TOTP_PATH = "local/users/{}/totp"
TOTP_PENDING_PATH = "local/users/{}/totp_pending"
# A password check against a user that does not exist still costs the same.
_DUMMY_HASH = _Argon2().hash(secrets.token_urlsafe(16))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PasswordHasher:
    def __init__(self):
        self._h = _Argon2()

    def hash(self, password: str) -> str:
        return self._h.hash(password)

    def verify(self, hashed: str, password: str) -> bool:
        try:
            return self._h.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    async def ahash(self, password: str) -> str:
        return await asyncio.to_thread(self.hash, password)

    async def averify(self, hashed: str, password: str) -> bool:
        return await asyncio.to_thread(self.verify, hashed, password)

    @staticmethod
    def validate_strength(password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise DomainError(f"The password must be at least {MIN_PASSWORD} characters long")
        classes = sum(any(f(c) for c in password) for f in (str.islower, str.isupper, str.isdigit))
        if classes < 2:
            raise DomainError("The password must mix lower and upper case letters or digits")


class IpRateLimiter:
    def __init__(self, limit: int = 10, window: int = 300):
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._limit, self._window = limit, window

    def check(self, ip: str) -> None:
        now = time.monotonic()
        hits = [t for t in self._hits[ip] if now - t < self._window]
        self._hits[ip] = hits
        if len(hits) >= self._limit:
            raise DomainError("Too many sign-in attempts. Wait a few minutes.")
        hits.append(now)


@dataclass
class LoginResult:
    token: str
    user: PanelUser
    requires_2fa: bool


class AuthService:
    def __init__(self, db: Database, audit: AuditService, ttl_hours, secrets_store: SecretStore | None = None):
        self._db = db
        self._audit = audit
        self._ttl_hours = ttl_hours
        self._secrets = secrets_store
        self.hasher = PasswordHasher()
        self._limiter = IpRateLimiter()

    @property
    def _ttl(self) -> dt.timedelta:
        return dt.timedelta(hours=int(current(self._ttl_hours)))

    # Sessions -----------------------------------------------------------------------------------------------------

    async def start_session(self, user_id: int, ip: str, user_agent: str, **audit) -> str:
        token = secrets.token_urlsafe(32)
        async with self._db.session() as s:
            user = await UserRepo(s).require(user_id)
            now = utcnow()
            SessionRepo(s).add(PanelSession(token_hash=hash_token(token), user_id=user.id, expires_at=now + self._ttl,
                                            ip=ip, user_agent=user_agent[:256]))
            user.last_login_at = now
            self._audit.record(s, Actor(user.username, user.role.value, ip), "auth.login", **audit)
        return token

    async def authenticate(self, token: str | None) -> tuple[PanelUser, PanelSession]:
        if not token:
            raise Unauthorized("Sign-in required")
        async with self._db.session() as s:
            sess = await SessionRepo(s).get(hash_token(token))
            now = utcnow()
            if not sess or sess.expires_at < now or sess.pending_2fa:
                raise Unauthorized("The session has expired")
            user = await UserRepo(s).get(sess.user_id)
            if not user or not user.is_active:
                raise Unauthorized("The account is disabled")
            if (now - sess.last_seen_at).total_seconds() > 60:
                sess.last_seen_at = now
            return user, sess

    async def logout(self, token: str, ip: str = "") -> None:
        async with self._db.session() as s:
            sess = await SessionRepo(s).get(hash_token(token))
            if sess:
                user = await UserRepo(s).get(sess.user_id)
                if user and not sess.pending_2fa:
                    self._audit.record(s, Actor(user.username, user.role.value, ip), "auth.logout")
                await s.delete(sess)

    async def sessions(self, user_id: int, current_token: str) -> list[dict]:
        cur = hash_token(current_token)
        async with self._db.session() as s:
            rows = await SessionRepo(s).for_user(user_id)
        return [{"id": r.token_hash[:16], "created_at": r.created_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat(),
                 "ip": r.ip, "user_agent": r.user_agent, "current": r.token_hash == cur} for r in rows if not r.pending_2fa]

    async def revoke_session(self, user_id: int, session_prefix: str) -> None:
        async with self._db.session() as s:
            for r in await SessionRepo(s).for_user(user_id):
                if r.token_hash.startswith(session_prefix):
                    await s.delete(r)

    # Local credentials (OpenBao) ---------------------------------------------------------------------------------

    def _store(self) -> SecretStore:
        if self._secrets is None:
            raise DomainError("OpenBao is not available")
        return self._secrets

    async def password_hash(self, user_id: int) -> str | None:
        data = await self._store().get(PASSWORD_PATH.format(user_id))
        return (data or {}).get("hash")

    async def set_password(self, user_id: int, password: str) -> None:
        self.hasher.validate_strength(password)
        await self._store().put(PASSWORD_PATH.format(user_id), {"hash": await self.hasher.ahash(password)})

    async def forget(self, user_id: int) -> None:
        """Deletes the local secrets of a user (removed, or moved to Keystone)."""
        for path in (PASSWORD_PATH, TOTP_PATH, TOTP_PENDING_PATH):
            await self._store().delete(path.format(user_id))

    # Local sign-in -----------------------------------------------------------------------------------------------

    async def login(self, username: str, password: str, ip: str, user_agent: str) -> LoginResult:
        self._limiter.check(ip)
        failure: str | None = None
        async with self._db.session() as s:
            user = await UserRepo(s).by_username(username.strip())
            now = utcnow()
            hashed = await self.password_hash(user.id) if user and user.source == "local" else None
            if user and user.locked_until and user.locked_until > now:
                failure = "The account is locked for a while after failed sign-in attempts"
            elif not await self.hasher.averify(hashed or _DUMMY_HASH, password) or not hashed or not user.is_active:
                if user:
                    user.failed_attempts += 1
                    if user.failed_attempts >= MAX_FAILURES:
                        user.locked_until = now + dt.timedelta(minutes=LOCK_MINUTES)
                        user.failed_attempts = 0
                failure = "Wrong user name or password"
            if failure:
                self._audit.record(s, Actor(username.strip()[:64] or "?", "", ip), "auth.login_failed")
        if failure:
            raise Unauthorized(failure)
        async with self._db.session() as s:
            user = await UserRepo(s).require(user.id)
            user.failed_attempts = 0
            user.locked_until = None
            token = secrets.token_urlsafe(32)
            SessionRepo(s).add(PanelSession(token_hash=hash_token(token), user_id=user.id, expires_at=now + self._ttl,
                                            ip=ip, user_agent=user_agent[:256], pending_2fa=user.totp_enabled))
            if not user.totp_enabled:
                user.last_login_at = now
                self._audit.record(s, Actor(user.username, user.role.value, ip), "auth.login", method="password")
            return LoginResult(token, user, user.totp_enabled)

    async def verify_2fa(self, token: str, code: str, ip: str) -> PanelUser:
        async with self._db.session() as s:
            sess = await SessionRepo(s).get(hash_token(token))
            if not sess or not sess.pending_2fa or sess.expires_at < utcnow():
                raise Unauthorized("The sign-in took too long, sign in again")
            user = await UserRepo(s).require(sess.user_id)
            secret = await self._store().get(TOTP_PATH.format(user.id))
            if not secret or not pyotp.TOTP(secret["secret"]).verify(code.strip(), valid_window=1):
                self._limiter.check(ip)
                raise Unauthorized("Wrong code")
            sess.pending_2fa = False
            user.last_login_at = utcnow()
            self._audit.record(s, Actor(user.username, user.role.value, ip), "auth.login", method="totp")
            return user

    async def change_password(self, user_id: int, current_password: str, new: str, current_token: str) -> None:
        self.hasher.validate_strength(new)
        hashed = await self.password_hash(user_id)
        if not hashed or not await self.hasher.averify(hashed, current_password):
            raise DomainError("The current password is wrong")
        if current_password == new:
            raise DomainError("The new password is the same as the current one")
        await self.set_password(user_id, new)
        async with self._db.session() as s:
            user = await UserRepo(s).require(user_id)
            user.must_change_password = False
            user.password_changed_at = utcnow()
            await SessionRepo(s).revoke_user(user.id, keep=hash_token(current_token))
            self._audit.record(s, Actor(user.username, user.role.value), "auth.password_changed")

    async def totp_begin(self, user: PanelUser) -> dict:
        secret = pyotp.random_base32()
        await self._store().put(TOTP_PENDING_PATH.format(user.id), {"secret": secret})
        uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="Harbor")

        def render() -> str:
            buf = io.BytesIO()
            qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2).save(buf)
            return buf.getvalue().decode()

        return {"secret": secret, "uri": uri, "qr_svg": await asyncio.to_thread(render)}

    async def totp_enable(self, user_id: int, code: str) -> None:
        pending = await self._store().get(TOTP_PENDING_PATH.format(user_id))
        if not pending or not pyotp.TOTP(pending["secret"]).verify(code.strip(), valid_window=1):
            raise DomainError("Wrong confirmation code")
        await self._store().put(TOTP_PATH.format(user_id), pending)
        await self._store().delete(TOTP_PENDING_PATH.format(user_id))
        async with self._db.session() as s:
            user = await UserRepo(s).require(user_id)
            user.totp_enabled = True
            self._audit.record(s, Actor(user.username, user.role.value), "auth.2fa_enabled")

    async def totp_disable(self, user_id: int, actor: Actor) -> None:
        await self._store().delete(TOTP_PATH.format(user_id))
        async with self._db.session() as s:
            user = await UserRepo(s).require(user_id)
            user.totp_enabled = False
            self._audit.record(s, actor, "auth.2fa_disabled", "panel_user", user.id, user.username)
