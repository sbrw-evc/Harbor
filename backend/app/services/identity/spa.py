"""The SPA client (docs: SPA repository, docs/protocol.md). SPA — Single Point of Authorization — is the only place
where people authenticate (passwords, passkeys, MFA, directories, external providers) and where their roles in
Harbor are decided. Harbor:

* sends people to SPA to sign in (OpenID Connect, authorization code flow with PKCE S256, nonce and signed state) and
  starts its own session after the callback;
* synchronises the people who may use it and their roles from SPA's federation API every five minutes (on the
  leader) and on "Synchronize now", disabling — and revoking the sessions and API tokens of — those SPA disabled or
  no longer lists;
* publishes its fixed role catalogue (admin, operator, viewer) to SPA.

The connection key (``spa1.<base64url JSON>``) comes from SPA_CONNECTION_KEY, the file named by
SPA_CONNECTION_KEY_FILE or the OpenBao secret ``spa/connection_key``."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from ...db.base import Database
from ...db.models import PanelUser
from ...db.repositories import ApiTokenRepo, SessionRepo, UserRepo
from ...domain.errors import DomainError, ExternalServiceError
from ...domain.models import Role
from ...ports import SecretStore
from ..audit import AuditService
from ..auth import AuthService
from ..common import Actor, utcnow
from ..settings import SettingsService
from . import oidc
from .state import StateSigner

log = logging.getLogger(__name__)

KEY_PREFIX = "spa1."
SECRET_PATH = "spa/connection_key"
CALLBACK_PATH = "/api/auth/spa/callback"
COOKIE = "nc_spa"
COOKIE_PATH = "/api/auth/spa/"
FLOW_SECONDS = 600
SYNC_SECONDS = 300
KEY_RELOAD_SECONDS = 60
SCOPES = ["openid", "profile", "email", "groups", "roles"]
SPA_ACTOR = Actor("spa", "system")
# How people sign in (setting auth_mode).
MODE_LOCAL = "local"
MODE_KEYSTONE = "keystone"
# Before SPA, the TOTP secrets of local users were kept in OpenBao at users/<id>/totp.
LEGACY_TOTP = "users/{}/totp"

# The fixed role catalogue of Harbor, published to SPA; SPA administrators assign these roles.
ROLE_CATALOGUE = [
    {"id": Role.ADMIN.value, "name": "Administrator",
     "description": "Everything: VPN server, routing, devices, API tokens, audit log, backups and settings"},
    {"id": Role.OPERATOR.value, "name": "Operator",
     "description": "Manages VPN clients, devices, network clients and alerts"},
    {"id": Role.VIEWER.value, "name": "Viewer", "description": "Read only: monitoring, analytics, flows and maps"},
]
DEFAULT_ROLE = Role.VIEWER.value
_RANK = [Role.VIEWER, Role.OPERATOR, Role.ADMIN]


class NotConnected(DomainError):
    status = 503

    def __init__(self):
        super().__init__("This application is not connected to SPA")


class SignInError(Exception):
    """A failed sign-in, sent back to the sign-in page as ?sso_error=<code>&sso_error_description=<message>."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Connection:
    issuer: str
    client_id: str
    client_secret: str
    token: str
    source: str = "env"


def parse_key(raw: str, source: str = "env") -> Connection:
    raw = raw.strip()
    if not raw.startswith(KEY_PREFIX):
        raise ValueError("The SPA connection key must start with spa1.")
    try:
        d = oidc.b64url_json(raw[len(KEY_PREFIX):])
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("The SPA connection key is damaged") from e
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) and d.get(k) for k in
                                          ("issuer", "client_id", "client_secret", "token")):
        raise ValueError("The SPA connection key lacks issuer, client_id, client_secret or token")
    if not d["issuer"].startswith(("https://", "http://")):
        raise ValueError("The issuer of the SPA connection key is not an http(s) address")
    return Connection(d["issuer"].rstrip("/"), d["client_id"], d["client_secret"], d["token"], source)


def role_of(roles: list[str]) -> Role | None:
    """The strongest role of Harbor among the role IDs SPA gives; None when there is none."""
    known = [Role(r) for r in roles if r in {x.value for x in Role}]
    return max(known, key=_RANK.index) if known else None


def safe_return(path: str | None) -> str:
    """Only paths of this application: no other hosts, no protocol-relative addresses."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or path.startswith("/api/"):
        return "/"
    return path[:1024]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


@dataclass
class Identity:
    """A person as SPA describes them (an entry of /api/spa/v1/users or the claims of an ID token)."""
    id: str
    username: str
    name: str
    email: str
    roles: list[str]
    groups: list[str]
    disabled: bool = False

    @classmethod
    def from_entry(cls, u: dict) -> "Identity":
        return cls(str(u.get("id") or ""), str(u.get("username") or ""), str(u.get("name") or ""),
                   str(u.get("email") or ""), oidc.strings(u.get("roles")), oidc.strings(u.get("groups")),
                   bool(u.get("disabled")))

    @classmethod
    def from_claims(cls, c: dict) -> "Identity":
        return cls(str(c.get("sub") or ""), str(c.get("preferred_username") or c.get("sub") or ""),
                   str(c.get("name") or ""), str(c.get("email") or ""), oidc.strings(c.get("roles")),
                   oidc.strings(c.get("groups")))


class SpaService:
    def __init__(self, db: Database, secrets_store: SecretStore, settings: SettingsService, auth: AuthService,
                 audit: AuditService, *, key: str = "", key_file: str = "", public_url: str = "",
                 transport: httpx.AsyncBaseTransport | None = None, lock=None):
        self._db = db
        self._secrets = secrets_store
        self._settings = settings
        self._auth = auth
        self._audit = audit
        self._key = key
        self._key_file = key_file
        self.public_url = public_url.rstrip("/")
        self._transport = transport
        self._lock = lock
        self._local_lock = asyncio.Lock()
        self.signer = StateSigner(secrets_store)
        self._conn: Connection | None = None
        self._conn_error = ""
        self._conn_loaded = 0.0
        self._provider: oidc.Provider | None = None
        self._used_states: dict[str, float] = {}
        self._published: str | None = None

    # The connection key ---------------------------------------------------------------------------------------------

    async def _read_key(self) -> tuple[str, str] | None:
        if self._key.strip():
            return self._key, "env"
        if self._key_file:
            try:
                return Path(self._key_file).read_text().strip(), "file"
            except OSError as e:
                raise ValueError(f"The SPA connection key file cannot be read: {e}") from e
        try:
            stored = await self._secrets.get(SECRET_PATH)
        except Exception as e:  # OpenBao is optional for the key; a failure must not break the sign-in page
            log.warning("SPA connection key from OpenBao: %s", e)
            return None
        if stored:
            raw = stored.get("value") or stored.get("key") or stored.get("connection_key")
            if raw:
                return str(raw), "openbao"
        return None

    async def connection(self) -> Connection | None:
        if time.monotonic() - self._conn_loaded > KEY_RELOAD_SECONDS or (self._conn is None and not self._conn_loaded):
            self._conn_loaded = time.monotonic()
            try:
                found = await self._read_key()
                conn = parse_key(*found) if found else None
                self._conn_error = ""
            except ValueError as e:
                conn, self._conn_error = None, str(e)
                log.error("SPA: %s", e)
            if conn != self._conn:
                self._conn = conn
                self._provider = oidc.Provider(conn.issuer, self._transport) if conn else None
                self._published = None
        return self._conn

    async def _require(self) -> tuple[Connection, oidc.Provider]:
        conn = await self.connection()
        if conn is None or self._provider is None:
            raise NotConnected()
        return conn, self._provider

    async def active(self) -> bool:
        """Whether people sign in through Keystone (auth_mode "keystone"); in the local mode the connection may exist
        (to move the users) but is not used for sign-in or synchronisation."""
        return await self._settings.get("auth_mode") == MODE_KEYSTONE

    async def set_key(self, raw: str, actor: Actor) -> dict:
        """Saves a connection key entered in the panel (OpenBao spa/connection_key) and checks it against Keystone."""
        conn = parse_key(raw, "openbao")
        if self._key.strip() or self._key_file:
            raise DomainError("The connection key is set in the environment (SPA_CONNECTION_KEY): remove it there first")
        _, cfg, _ = await self._api(conn, "GET", "config")
        await self._secrets.put(SECRET_PATH, {"value": raw.strip()})
        self._conn_loaded = 0.0
        await self.connection()
        async with self._db.session() as s:
            self._audit.record(s, actor, "spa.key_set", "spa", "", conn.issuer, client_id=conn.client_id)
        app = (cfg or {}).get("application") or {} if isinstance(cfg, dict) else {}
        return {"issuer": conn.issuer, "application": app.get("name"),
                "user_import": bool((cfg or {}).get("user_import")) if isinstance(cfg, dict) else False}

    async def remove_key(self, actor: Actor) -> None:
        if await self.active():
            raise DomainError("Switch the sign-in to local users first")
        await self._secrets.delete(SECRET_PATH)
        self._conn_loaded = 0.0
        await self.connection()
        async with self._db.session() as s:
            self._audit.record(s, actor, "spa.key_removed", "spa", "", "")

    # Moving from local users to Keystone -----------------------------------------------------------------------

    async def import_local_users(self, auth: AuthService, actor: Actor) -> dict:
        """Sends the active local users with their argon2id password hashes and roles to Keystone
        (POST /api/spa/v1/users/import, open only while a Keystone administrator allows it for this application).
        TOTP is not moved. Returns Keystone's report: imported, conflicts, rejected."""
        conn, _ = await self._require()
        _, cfg, _ = await self._api(conn, "GET", "config")
        if not (isinstance(cfg, dict) and cfg.get("user_import")):
            raise DomainError("Keystone does not accept users from this application now: in Keystone open the "
                              "application and allow moving users, then try again")
        await self.publish_roles(force=True)
        async with self._db.session() as s:
            people = [u for u in await UserRepo(s).all(order_by=PanelUser.id) if u.source == "local" and u.is_active]
        entries = []
        for u in people:
            entries.append({"username": u.username, "name": u.display_name, "email": u.email, "role": u.role.value,
                            "password_hash": await auth.password_hash(u.id)})
        if not entries:
            raise DomainError("There are no active local users to move")
        try:
            async with httpx.AsyncClient(timeout=60, transport=self._transport) as h:
                r = await h.post(f"{conn.issuer}/api/spa/v1/users/import", json={"users": entries},
                                 headers={"authorization": f"Bearer {conn.token}", "accept": "application/json"})
        except httpx.HTTPError as e:
            raise ExternalServiceError(f"SPA is not available: {e}") from e
        if r.status_code == 403:
            raise DomainError("Keystone closed the import: allow moving users for this application again")
        if r.status_code == 404:
            raise DomainError("This Keystone cannot import users yet: update Keystone, or give people roles in it by hand")
        if r.status_code >= 400:
            raise ExternalServiceError(f"SPA: users/import: HTTP {r.status_code}")
        report = r.json()
        result = {k: list(report.get(k) or []) for k in ("imported", "conflicts", "rejected")}
        await self._save_state(last_import={"at": utcnow().isoformat(), **{k: len(v) for k, v in result.items()}})
        async with self._db.session() as s:
            self._audit.record(s, actor, "spa.users_imported", "spa", "", conn.issuer,
                               **{k: len(v) for k, v in result.items()})
        return result

    async def switch_to_keystone(self, auth: AuthService, actor: Actor, purge_local: bool) -> dict:
        """Makes Keystone the way to sign in. Refused unless Keystone gives someone the administrator role here, so
        nobody is locked out. The synchronisation that follows binds local records by user name and disables the
        rest; with ``purge_local`` the local passwords and TOTP secrets of the bound users are deleted from OpenBao."""
        conn, _ = await self._require()
        status, data, _ = await self._api(conn, "GET", "users")
        entries = (data or {}).get("users") if isinstance(data, dict) else None
        admins = [e for e in entries or [] if isinstance(e, dict) and not e.get("disabled")
                  and role_of(oidc.strings(e.get("roles"))) == Role.ADMIN]
        if not admins:
            raise DomainError("Nobody has the administrator role of this application in Keystone: give it to "
                              "yourself there first, or you would be locked out")
        await self._settings.update({"auth_mode": MODE_KEYSTONE}, internal=True)
        result = await self.sync(force=True, actor=actor)
        purged = 0
        if purge_local:
            async with self._db.session() as s:
                bound = [u.id for u in await UserRepo(s).all() if u.source == "spa"]
            for user_id in bound:
                await auth.forget(user_id)
                purged += 1
        async with self._db.session() as s:
            self._audit.record(s, actor, "auth.mode_changed", "settings", "", MODE_KEYSTONE, purged_local=purged)
        return {**result, "purged_local": purged}

    async def switch_to_local(self, actor: Actor) -> None:
        """Back to local users (e.g. Keystone is gone). Only people who still have a local password can sign in."""
        async with self._db.session() as s:
            users = await UserRepo(s).all()
        has_admin = False
        for u in users:
            if u.role == Role.ADMIN and await self._auth.password_hash(u.id):
                has_admin = True
                break
        if not has_admin:
            raise DomainError("No administrator has a local password: create one first (python -m app.cli "
                              "create-admin) or you would be locked out")
        await self._settings.update({"auth_mode": MODE_LOCAL}, internal=True)
        async with self._db.session() as s:
            for u in await UserRepo(s).all():
                if await self._auth.password_hash(u.id):
                    u.source, u.is_active = "local", True
            self._audit.record(s, actor, "auth.mode_changed", "settings", "", MODE_LOCAL)

    def redirect_uri(self, request_base: str) -> str:
        return (self.public_url or request_base.rstrip("/")) + CALLBACK_PATH

    def _client(self, conn: Connection, request_base: str, extra: dict[str, str] | None = None) -> oidc.Client:
        return oidc.Client(conn.issuer, conn.client_id, conn.client_secret, self.redirect_uri(request_base), SCOPES,
                           extra or {})

    # Sign-in --------------------------------------------------------------------------------------------------------

    async def start(self, request_base: str, return_path: str | None, *, login_hint: str = "", idp_hint: str = "",
                    prompt: str = "") -> tuple[str, str]:
        """The address of SPA's authorization endpoint and the PKCE verifier for the short-lived cookie."""
        if not await self.active():
            raise NotConnected()
        conn, provider = await self._require()
        r = oidc.Request()
        state = await self.signer.sign({"n": r.nonce, "r": safe_return(return_path), "v": _fingerprint(r.verifier),
                                        "j": oidc.random_token()[:16]}, FLOW_SECONDS)
        r.state = state
        extra = {k: v for k, v in (("login_hint", login_hint), ("idp_hint", idp_hint),
                                   ("prompt", "login" if prompt == "login" else "")) if v}
        try:
            url = await provider.auth_url(self._client(conn, request_base, extra), r)
        except oidc.OidcError as e:
            raise ExternalServiceError(f"SPA is not available: {e}") from e
        return url, r.verifier

    def _use_state(self, jti: str) -> bool:
        now = time.time()
        self._used_states = {k: v for k, v in self._used_states.items() if v > now}
        if jti in self._used_states:
            return False
        self._used_states[jti] = now + FLOW_SECONDS
        return True

    async def finish(self, request_base: str, code: str, state: str, verifier: str | None, ip: str,
                     user_agent: str) -> tuple[str, str]:
        """Checks the state against the verifier cookie, redeems the code, verifies the ID token and starts a session.
        Returns the session token and the path to go back to."""
        if not await self.active():
            raise NotConnected()
        conn, provider = await self._require()
        try:
            st = await self.signer.open(state)
        except DomainError as e:
            raise SignInError("invalid_state", e.message) from e
        if not verifier or _fingerprint(verifier) != st.get("v") or not self._use_state(str(st.get("j"))):
            raise SignInError("invalid_state", "The sign-in took too long or was started in another browser. Try again.")
        client = self._client(conn, request_base)
        try:
            tokens = await provider.exchange(client, code, verifier)
            claims = await provider.verify(client, tokens["id_token"], str(st.get("n")))
        except oidc.OidcError as e:
            log.warning("SPA sign-in failed: %s", e)
            raise SignInError("invalid_token", f"SPA sign-in failed: {e}") from e
        ident = Identity.from_claims(claims)
        if not ident.id:
            raise SignInError("invalid_token", "SPA sign-in failed: the ID token has no subject")
        async with self._db.session() as s:
            user, _, _ = await self._apply(s, ident, now=utcnow())
            if not user.is_active:
                raise SignInError("access_denied", "You have no role in Harbor. Ask an SPA administrator for access.")
            user_id = user.id
        amr = ",".join(oidc.strings(claims.get("amr")))
        token = await self._auth.start_session(user_id, ip, user_agent, method="spa", amr=amr)
        return token, str(st.get("r") or "/")

    async def sign_out_url(self, request_base: str) -> str | None:
        conn = await self.connection()
        if conn is None:
            return None
        cfg = (await self.state()).get("config") or {}
        url = cfg.get("sign_out_url") or f"{conn.issuer}/oauth2/logout"
        after = (self.public_url or request_base.rstrip("/")) + "/"
        return f"{url}{'&' if '?' in url else '?'}{urlencode({'post_logout_redirect_uri': after, 'client_id': conn.client_id})}"

    # Users --------------------------------------------------------------------------------------------------------

    @staticmethod
    async def _free_username(s, wanted: str, user: PanelUser | None) -> str:
        wanted = (wanted or "user").strip()[:64]
        repo = UserRepo(s)
        candidate, n = wanted, 1
        while True:
            other = await repo.by_username(candidate)
            if other is None or (user is not None and other.id == user.id):
                return candidate
            n += 1
            suffix = f"-{n}"
            candidate = wanted[:64 - len(suffix)] + suffix

    async def _apply(self, s, ident: Identity, *, now) -> tuple[PanelUser, bool, bool]:
        """Creates or updates the local record of a person from SPA. Binds by SPA user ID; a record from before SPA is
        bound once by username. Returns the record, whether it was created and whether an existing one changed."""
        repo = UserRepo(s)
        user = await repo.by_external("spa", ident.id)
        created = False
        if user is None:
            legacy = await repo.by_username(ident.username) if ident.username else None
            if legacy is not None and legacy.external_id is None:
                user = legacy
                user.source, user.external_id = "spa", ident.id
                self._audit.record(s, SPA_ACTOR, "panel_user.bound", "panel_user", user.id, user.username, spa_id=ident.id)
            else:
                user = repo.add(PanelUser(username=await self._free_username(s, ident.username, None), source="spa",
                                          external_id=ident.id, role=Role.VIEWER, is_active=False))
                created = True
        role = role_of(ident.roles)
        active = not ident.disabled and role is not None
        values: dict[str, Any] = {
            "username": await self._free_username(s, ident.username, user) if ident.username else user.username,
            "display_name": ident.name[:128], "email": ident.email[:256], "groups": [g[:128] for g in ident.groups],
            "role": role or user.role, "is_active": active,
        }
        changes = {k: v for k, v in values.items() if getattr(user, k) != v}
        for k, v in values.items():
            setattr(user, k, v)
        user.synced_at = now
        await s.flush()
        if created:
            self._audit.record(s, SPA_ACTOR, "panel_user.created", "panel_user", user.id, user.username,
                               role=user.role.value, active=active)
        elif changes:
            self._audit.record(s, SPA_ACTOR, "panel_user.updated", "panel_user", user.id, user.username,
                               **{k: (v.value if isinstance(v, Role) else v) for k, v in changes.items() if k != "groups"})
        if not active and not created and "is_active" in changes:
            await self._revoke(s, user)
        return user, created, bool(changes) and not created

    async def _revoke(self, s, user: PanelUser) -> None:
        await SessionRepo(s).revoke_user(user.id)
        tokens = await ApiTokenRepo(s).revoke_user(user.id)
        self._audit.record(s, SPA_ACTOR, "panel_user.disabled", "panel_user", user.id, user.username, api_tokens=tokens)

    # Federation API --------------------------------------------------------------------------------------------------

    async def _api(self, conn: Connection, method: str, path: str, *, body: dict | None = None,
                   etag: str | None = None) -> tuple[int, Any, str | None]:
        headers = {"authorization": f"Bearer {conn.token}", "accept": "application/json"}
        if etag:
            headers["if-none-match"] = etag
        try:
            async with httpx.AsyncClient(timeout=oidc.TIMEOUT, transport=self._transport) as h:
                r = await h.request(method, f"{conn.issuer}/api/spa/v1/{path}", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ExternalServiceError(f"SPA is not available: {e}") from e
        if r.status_code == 401:
            raise ExternalServiceError("SPA rejected the connection key: it was replaced or the application was removed")
        if r.status_code == 304:
            return 304, None, etag
        if r.status_code >= 400:
            raise ExternalServiceError(f"SPA: {path}: HTTP {r.status_code}")
        try:
            data = r.json() if r.content else None
        except ValueError as e:
            raise ExternalServiceError(f"SPA: {path}: the answer is not JSON") from e
        return r.status_code, data, r.headers.get("etag")

    async def state(self) -> dict:
        return dict(await self._settings.get("spa") or {})

    async def _save_state(self, **values) -> dict:
        st = {**await self.state(), **values}
        await self._settings.put_private("spa", st)
        return st

    async def publish_roles(self, force: bool = False) -> bool:
        """Publishes the role catalogue (at start, once per connection). Returns whether it was sent."""
        conn, _ = await self._require()
        body = {"roles": ROLE_CATALOGUE, "default": DEFAULT_ROLE}
        digest = _fingerprint(conn.issuer + conn.client_id + json.dumps(body, sort_keys=True))
        if not force and self._published == digest:
            return False
        await self._api(conn, "PUT", "roles", body=body)
        self._published = digest
        await self._save_state(roles_published_at=utcnow().isoformat())
        log.info("SPA: role catalogue published")
        return True

    @contextlib.asynccontextmanager
    async def _sync_lock(self):
        async with self._local_lock:
            if self._lock is None:
                yield
            else:
                async with self._lock.hold():
                    yield

    async def sync(self, force: bool = False, actor: Actor | None = None) -> dict:
        """Reads the users of the application from SPA (with the ETag unless forced), creates, updates and disables
        the local records, and revokes the sessions and API tokens of the people SPA disabled or no longer lists.
        Nothing happens in the local mode: it would disable the local users."""
        if not await self.active():
            return {"connected": False, "mode": MODE_LOCAL}
        conn = await self.connection()
        if conn is None:
            await self._save_state(connected=False, error=self._conn_error or None)
            return {"connected": False}
        async with self._sync_lock():
            started = utcnow()
            try:
                await self.publish_roles()
                _, cfg, _ = await self._api(conn, "GET", "config")
                st = await self.state()
                same = st.get("issuer") == conn.issuer and st.get("client_id") == conn.client_id
                status, data, etag = await self._api(conn, "GET", "users",
                                                     etag=None if force or not same else st.get("etag"))
                summary = {"created": 0, "updated": 0, "disabled": 0, "unchanged": status == 304}
                if status != 304:
                    entries = (data or {}).get("users") if isinstance(data, dict) else None
                    if not isinstance(entries, list):
                        raise ExternalServiceError("SPA: users: unexpected answer")
                    summary.update(await self._apply_all([Identity.from_entry(u) for u in entries if isinstance(u, dict)]))
                async with self._db.session() as s:
                    users = await UserRepo(s).all()
                counts = {"users": len(users), "active": sum(u.is_active for u in users),
                          "spa": sum(1 for u in users if u.source == "spa" and u.external_id)}
                await self._save_state(
                    connected=True, issuer=conn.issuer, client_id=conn.client_id, key_source=conn.source,
                    config=cfg if isinstance(cfg, dict) else {}, etag=etag, error=None,
                    last_attempt_at=started.isoformat(), last_sync_at=started.isoformat(), counts=counts,
                    last_result={k: v for k, v in summary.items()})
                await self.purge_legacy_totp()
                if actor is not None or summary["created"] or summary["updated"] or summary["disabled"]:
                    async with self._db.session() as s:
                        self._audit.record(s, actor or SPA_ACTOR, "spa.sync", "spa", "", conn.issuer,
                                           **{k: v for k, v in summary.items() if k != "unchanged"})
                return {"connected": True, **summary, "counts": counts}
            except DomainError as e:
                await self._save_state(connected=True, issuer=conn.issuer, client_id=conn.client_id,
                                       key_source=conn.source, error=e.message, last_attempt_at=started.isoformat())
                raise

    async def purge_legacy_totp(self) -> int | None:
        """Deletes the TOTP secrets of the sign-in from before SPA (OpenBao users/<id>/totp): MFA is SPA's now. Runs once,
        at the first successful synchronisation, for every user the database knows and every folder OpenBao lists under
        users/ (users deleted before the move included). Returns the number of users checked; None when it was done
        before or OpenBao failed (then it is tried again at the next synchronisation)."""
        if (await self.state()).get("legacy_totp_purged_at"):
            return None
        async with self._db.session() as s:
            ids = {str(u.id) for u in await UserRepo(s).all()}
        try:
            listed = await self._secrets.keys("users/") if hasattr(self._secrets, "keys") else []
            ids |= {k.rstrip("/") for k in listed if k.endswith("/") and k.rstrip("/")}
            for user_id in sorted(ids):
                await self._secrets.delete(LEGACY_TOTP.format(user_id))
        except Exception as e:  # OpenBao down or the policy lacks list/delete: the synchronisation itself goes on
            log.warning("SPA: old TOTP secrets are not deleted yet: %s", e)
            return None
        await self._save_state(legacy_totp_purged_at=utcnow().isoformat())
        async with self._db.session() as s:
            self._audit.record(s, SPA_ACTOR, "spa.legacy_totp_purged", "spa", "", "users/*/totp", users=len(ids))
        log.info("SPA: old TOTP secrets deleted for %d users", len(ids))
        return len(ids)

    async def _apply_all(self, people: list[Identity]) -> dict:
        now = utcnow()
        created = updated = disabled = 0
        async with self._db.session() as s:
            seen: set[int] = set()
            for ident in people:
                if not ident.id:
                    continue
                existing = await UserRepo(s).by_external("spa", ident.id)
                was_active = existing.is_active if existing else False
                user, is_new, changed = await self._apply(s, ident, now=now)
                seen.add(user.id)
                created += is_new
                updated += changed
                disabled += was_active and not user.is_active
            for user in await UserRepo(s).all(PanelUser.is_active):
                if user.id not in seen:
                    user.is_active = False
                    user.synced_at = now
                    await self._revoke(s, user)
                    disabled += 1
        return {"created": created, "updated": updated, "disabled": disabled}

    # What the pages show ---------------------------------------------------------------------------------------------

    async def public_config(self) -> dict:
        conn = await self.connection()
        cfg = ((await self.state()).get("config") or {}) if conn else {}
        return {"mode": await self._settings.get("auth_mode") or MODE_LOCAL,
                "connected": conn is not None, "issuer": conn.issuer if conn else None,
                "account_url": cfg.get("account_url"), "error": None if conn else (self._conn_error or None)}

    async def status(self) -> dict:
        conn = await self.connection()
        st = await self.state()
        cfg = st.get("config") or {}
        if conn is not None and not await self.active():
            # No synchronisation runs in the local mode: ask Keystone directly (the import window, the application).
            try:
                _, live, _ = await self._api(conn, "GET", "config")
                cfg = live if isinstance(live, dict) else cfg
            except DomainError as e:
                st = {**st, "error": e.message}
        app = cfg.get("application") or {}
        return {
            "mode": await self._settings.get("auth_mode") or MODE_LOCAL, "user_import": bool(cfg.get("user_import")),
            "last_import": st.get("last_import"),
            "connected": conn is not None, "issuer": conn.issuer if conn else None,
            "client_id": conn.client_id if conn else None, "key_source": conn.source if conn else None,
            "key_error": self._conn_error or None, "application": app.get("name"), "application_type": app.get("type"),
            "account_url": cfg.get("account_url"), "methods": cfg.get("methods") or [],
            "last_sync_at": st.get("last_sync_at"), "last_attempt_at": st.get("last_attempt_at"),
            "error": st.get("error") if conn else None, "counts": st.get("counts") or {},
            "last_result": st.get("last_result") or {}, "roles_published_at": st.get("roles_published_at"),
            "roles": ROLE_CATALOGUE, "sync_seconds": SYNC_SECONDS,
            "redirect_uri": self.redirect_uri(self.public_url) if self.public_url else None,
        }
