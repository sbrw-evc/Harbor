"""The SPA client against a fake SPA (tests/spafake.py): connection key, sign-in (PKCE, state, nonce, ID token
checks), access_denied, user synchronisation (create, update, bind, disable, role mapping, ETag), the role catalogue
and sign-out. Tests that keep users need PostgreSQL (TEST_DATABASE_URL); the protocol checks run without it."""
import asyncio
import base64
import contextlib
import hashlib
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app.domain.errors import ExternalServiceError, Unauthorized
from app.domain.models import Role
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.common import Actor, EventBus
from app.services.identity import spa as spa_mod
from app.services.identity.spa import NotConnected, SignInError, SpaService, parse_key, role_of, safe_return
from spafake import CLIENT_ID, ISSUER, FakeSpa, connection_key

URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")
APP = "http://nc.test"


class Secrets:
    def __init__(self, d: dict | None = None):
        self.d = dict(d or {})

    async def get(self, path):
        return self.d.get(path)

    async def put(self, path, data):
        self.d[path] = data

    async def delete(self, path):
        self.d.pop(path, None)

    async def healthy(self):
        return True

    async def keys(self, prefix):
        return sorted({p[len(prefix):].split("/")[0] + "/" for p in self.d if p.startswith(prefix) and "/" in p[len(prefix):]})


class MemSettings:
    def __init__(self):
        self.d = {"auth_mode": "keystone"}

    async def get(self, key):
        return self.d.get(key)

    async def put_private(self, key, value):
        self.d[key] = value


def make(fake: FakeSpa, db=None, settings=None, secrets=None, **kw) -> SpaService:
    audit = AuditService(db, EventBus())
    kw.setdefault("key", connection_key())
    return SpaService(db, secrets or Secrets(), settings or MemSettings(), AuthService(db, audit, 12), audit,
                      transport=fake.transport(), **kw)


async def authorize(fake: FakeSpa, url: str) -> dict:
    """Follows the authorization request at the fake SPA; returns the query of the redirect back to the app."""
    async with httpx.AsyncClient(transport=fake.transport()) as h:
        r = await h.get(url)
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{APP}/api/auth/spa/callback"
    return {k: v[0] for k, v in parse_qs(loc.query).items()}


# Without a database -------------------------------------------------------------------------------------------------

def test_connection_key():
    c = parse_key(connection_key(issuer=ISSUER + "/"))
    assert (c.issuer, c.client_id, c.client_secret, c.token) == (ISSUER, CLIENT_ID, "s3cret", "fed-token")
    for bad in ("umbid1.abc", "spa1.!!!", "spa1." + base64.urlsafe_b64encode(b'{"issuer":"x"}').decode(),
                connection_key(issuer="ftp://spa")):
        with pytest.raises(ValueError):
            parse_key(bad)


def test_roles_and_return_paths():
    assert role_of(["viewer", "admin"]) == Role.ADMIN
    assert role_of(["operator", "unknown"]) == Role.OPERATOR
    assert role_of(["unknown"]) is None and role_of([]) is None
    assert safe_return("/devices?x=1") == "/devices?x=1"
    for bad in ("https://evil.example", "//evil.example", "/api/auth/me", None, "", "/\\evil"):
        assert safe_return(bad) == "/"


def test_key_sources(tmp_path):
    fake = FakeSpa()

    async def scenario():
        none = make(fake, key="")
        assert await none.connection() is None
        assert (await none.public_config())["connected"] is False
        with pytest.raises(NotConnected):
            await none.start(APP, "/")
        f = tmp_path / "key"
        f.write_text(connection_key() + "\n")
        assert (await make(fake, key="", key_file=str(f)).connection()).source == "file"
        bao = make(fake, key="", secrets=Secrets({"spa/connection_key": {"value": connection_key()}}))
        assert (await bao.connection()).source == "openbao"
        broken = make(fake, key="spa1.broken")
        assert await broken.connection() is None
        assert (await broken.status())["key_error"] == "The SPA connection key is damaged"

    asyncio.run(scenario())


def test_start_uses_pkce_nonce_and_signed_state():
    fake = FakeSpa()

    async def scenario():
        svc = make(fake)
        url, verifier = await svc.start(APP, "/devices", login_hint="alice", idp_hint="passkey", prompt="login")
        u = urlsplit(url)
        assert f"{u.scheme}://{u.netloc}{u.path}" == f"{ISSUER}/oauth2/authorize"
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert q["redirect_uri"] == f"{APP}/api/auth/spa/callback"
        assert q["code_challenge_method"] == "S256"
        assert q["code_challenge"] == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert (q["login_hint"], q["idp_hint"], q["prompt"]) == ("alice", "passkey", "login")
        st = await svc.signer.open(q["state"])
        assert st["n"] == q["nonce"] and st["r"] == "/devices"
        assert make(fake, public_url="https://nc.example.com/").redirect_uri(APP) == "https://nc.example.com/api/auth/spa/callback"

    asyncio.run(scenario())


def test_callback_rejects_wrong_state():
    fake = FakeSpa()
    fake.add_user("usr_a", "alice", ["operator"])
    fake.signed_in = "usr_a"

    async def scenario():
        svc = make(fake)
        url, verifier = await svc.start(APP, "/")
        back = await authorize(fake, url)
        for state, cookie in ((back["state"] + "x", verifier), (back["state"], None), (back["state"], "other")):
            with pytest.raises(SignInError) as e:
                await svc.finish(APP, back["code"], state, cookie, "", "")
            assert e.value.code == "invalid_state"
        other = make(fake)  # another installation: another state key
        with pytest.raises(SignInError):
            await other.finish(APP, back["code"], back["state"], verifier, "", "")

    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", [{"aud": "app_other"}, {"iss": "http://evil.test"}, {"nonce": "other"},
                                    {"exp": 1000}])
def test_callback_rejects_bad_id_token(tamper):
    fake = FakeSpa()
    fake.add_user("usr_a", "alice", ["operator"])
    fake.signed_in = "usr_a"
    fake.tamper = tamper

    async def scenario():
        svc = make(fake)
        url, verifier = await svc.start(APP, "/")
        back = await authorize(fake, url)
        with pytest.raises(SignInError) as e:
            await svc.finish(APP, back["code"], back["state"], verifier, "", "")
        assert e.value.code == "invalid_token"

    asyncio.run(scenario())


def test_publish_role_catalogue_and_bad_token():
    fake = FakeSpa()

    async def scenario():
        svc = make(fake)
        assert await svc.publish_roles() is True
        assert await svc.publish_roles() is False
        assert fake.published == [{"roles": spa_mod.ROLE_CATALOGUE, "default": "viewer"}]
        assert [r["id"] for r in fake.published[0]["roles"]] == ["admin", "operator", "viewer"]
        assert [r["name"] for r in fake.published[0]["roles"]] == ["Administrator", "Operator", "Viewer"]
        await svc.publish_roles(force=True)
        assert len(fake.published) == 2
        with pytest.raises(ExternalServiceError, match="replaced"):
            await make(fake, key=connection_key(token="old")).publish_roles()

    asyncio.run(scenario())


# With PostgreSQL ----------------------------------------------------------------------------------------------------

@pytest.fixture
def env():
    async def setup():
        from app import main
        from app.api import routes_admin, routes_auth, routes_ops
        from app.domain.errors import DomainError
        from app.db.base import Database
        from app.db.models import Base
        from app.services.settings import SettingsService
        from app.services.tokens import ApiTokenService
        from app.services.users import PanelUserService

        db = Database(URL)
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.create_schema()
        fake = FakeSpa()
        settings = SettingsService(db)
        await settings.update({"auth_mode": "keystone"}, internal=True)
        audit = AuditService(db, EventBus())
        auth = AuthService(db, audit, 12)
        svc = SpaService(db, Secrets(), settings, auth, audit, key=connection_key(), transport=fake.transport())
        c = SimpleNamespace(rc=SimpleNamespace(cookie_secure=False, session_ttl_hours=12), db=db, spa=svc, auth=auth,
                            audit=audit, settings=settings, tokens=ApiTokenService(db, audit), users=PanelUserService(db))
        app = FastAPI()
        app.add_exception_handler(DomainError, main.domain_error)
        for r in (routes_auth.router, routes_ops.router, routes_admin.router):
            app.include_router(r)
        app.state.container = c
        return SimpleNamespace(db=db, fake=fake, spa=svc, c=c, app=app)
    return setup


async def audit_actions(db) -> list[str]:
    from sqlalchemy import select
    from app.db.models import AuditEntry
    async with db.session() as s:
        return [a for (a,) in (await s.execute(select(AuditEntry.action).order_by(AuditEntry.id))).all()]


async def sign_in(e, client: httpx.AsyncClient, return_path: str = "/devices") -> httpx.Response:
    r = await client.get("/api/auth/spa/start", params={"return": return_path})
    assert r.status_code == 302, r.text
    if not r.headers["location"].startswith(ISSUER):
        return r
    back = await authorize(e.fake, r.headers["location"])
    return await client.get("/api/auth/spa/callback", params=back)


@needs_db
def test_sign_in_and_sign_out(env):
    async def scenario():
        e = await env()
        e.fake.add_user("usr_a", "alice", ["operator", "viewer"], groups=["noc", "ops"])
        e.fake.signed_in = "usr_a"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=e.app), base_url=APP) as client:
            assert (await client.get("/api/auth/config")).json()["connected"] is True
            assert (await client.get("/api/auth/me")).status_code == 401
            start = await client.get("/api/auth/spa/start", params={"return": "/devices"})
            cookie = start.headers["set-cookie"]
            assert "nc_spa=" in cookie and "HttpOnly" in cookie and "Path=/api/auth/spa/" in cookie
            assert "samesite=lax" in cookie.lower() and "Max-Age=600" in cookie
            back = await authorize(e.fake, start.headers["location"])
            r = await client.get("/api/auth/spa/callback", params=back)
            assert r.status_code == 302 and r.headers["location"] == "/devices"
            me = (await client.get("/api/auth/me")).json()
            assert (me["username"], me["role"], me["source"], me["external_id"]) == ("alice", "operator", "spa", "usr_a")
            assert me["email"] == "alice@example.com" and me["groups"] == ["noc", "ops"]
            # The state is single use.
            again = await client.get("/api/auth/spa/callback", params=back)
            assert "sso_error=invalid_state" in again.headers["location"]
            # Sign-out ends the session and sends the browser to SPA's sign-out.
            out = (await client.post("/api/auth/logout", headers={"X-Requested-With": "network-control"})).json()
            assert out["redirect"] == (f"{ISSUER}/oauth2/logout?post_logout_redirect_uri=http%3A%2F%2Fnc.test%2F"
                                       f"&client_id={CLIENT_ID}")
            assert (await client.get("/api/auth/me")).status_code == 401
        actions = await audit_actions(e.db)
        assert "panel_user.created" in actions and "auth.login" in actions and "auth.logout" in actions

    asyncio.run(scenario())


@needs_db
def test_access_denied_and_errors(env):
    async def scenario():
        e = await env()
        e.fake.add_user("usr_n", "nobody", [])
        e.fake.signed_in = "usr_n"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=e.app), base_url=APP) as client:
            r = await sign_in(e, client)
            assert r.status_code == 302 and r.headers["location"].startswith("/login?sso_error=access_denied")
            # A role SPA gives but Network Control does not know is no access either.
            e.fake.add_user("usr_u", "unknown", ["superuser"])
            e.fake.signed_in = "usr_u"
            r = await sign_in(e, client)
            assert r.headers["location"].startswith("/login?sso_error=access_denied")
            assert (await client.get("/api/auth/me")).status_code == 401
            # Not connected: the sign-in page says so.
            e.spa._key, e.spa._conn_loaded = "", 0.0
            r = await client.get("/api/auth/spa/start")
            assert r.headers["location"] == "/login?sso_error=not_connected"
            assert (await client.get("/api/auth/config")).json()["connected"] is False

    asyncio.run(scenario())


@needs_db
def test_sync_creates_updates_binds_and_disables(env):
    from app.db.models import PanelSession, PanelUser
    from app.db.repositories import SessionRepo, UserRepo
    from app.services.auth import hash_token

    async def scenario():
        e = await env()
        async with e.db.session() as s:
            bob = UserRepo(s).add(PanelUser(username="bob", display_name="Bob (local)", role=Role.VIEWER, source="local"))
            carol = UserRepo(s).add(PanelUser(username="carol", role=Role.ADMIN, source="local"))
            await s.flush()
            bob_id, carol_id = bob.id, carol.id
        carol_token = (await e.c.tokens.create(Actor("carol", "admin"), carol_id, "ci", Role.VIEWER, None))["token"]
        assert (await e.c.tokens.authenticate(carol_token)).role == Role.VIEWER
        carol_session = await e.c.auth.start_session(carol_id, "", "")

        e.fake.add_user("usr_a", "alice", ["operator"])
        e.fake.add_user("usr_b", "bob", ["viewer", "admin"], name="Bob Builder")
        e.fake.add_user("usr_d", "dave", ["operator"], disabled=True)
        e.fake.add_user("usr_e", "erin", ["superuser"])
        res = await e.spa.sync()
        assert (res["created"], res["disabled"]) == (3, 1)
        assert len(e.fake.published) == 1

        async with e.db.session() as s:
            users = {u.username: u for u in await UserRepo(s).all()}
        assert users["alice"].role == Role.OPERATOR and users["alice"].is_active and users["alice"].external_id == "usr_a"
        assert users["bob"].id == bob_id and users["bob"].source == "spa" and users["bob"].external_id == "usr_b"
        assert users["bob"].role == Role.ADMIN and users["bob"].display_name == "Bob Builder" and users["bob"].is_active
        assert not users["dave"].is_active and not users["erin"].is_active
        assert not users["carol"].is_active  # an old local user SPA does not list
        with pytest.raises(Unauthorized):
            await e.c.tokens.authenticate(carol_token)
        with pytest.raises(Unauthorized):
            await e.c.auth.authenticate(carol_session)

        # Unchanged list: the ETag answers 304.
        res = await e.spa.sync()
        assert res["unchanged"] is True
        assert e.fake.requests[-1][2].get("if-none-match")

        # Role change, disabling and removal in SPA.
        bob_session = await e.c.auth.start_session(bob_id, "", "")
        e.fake.users["usr_a"]["roles"] = ["viewer"]
        e.fake.users["usr_b"]["disabled"] = True
        e.fake.users["usr_e"]["roles"] = ["operator"]
        e.fake.users["usr_a"]["username"] = "alice.s"
        res = await e.spa.sync()
        assert (res["updated"], res["disabled"]) == (3, 1)
        async with e.db.session() as s:
            users = {u.external_id: u for u in await UserRepo(s).all()}
            assert not await SessionRepo(s).get(hash_token(bob_session))
        assert users["usr_a"].role == Role.VIEWER and users["usr_a"].username == "alice.s"
        assert not users["usr_b"].is_active
        assert users["usr_e"].is_active and users["usr_e"].role == Role.OPERATOR

        del e.fake.users["usr_e"]
        await e.spa.sync()
        async with e.db.session() as s:
            assert not (await UserRepo(s).by_external("spa", "usr_e")).is_active

        st = await e.spa.status()
        assert st["application"] == "Network Control" and st["account_url"] == f"{ISSUER}/profile"
        assert st["last_sync_at"] and st["error"] is None and st["counts"]["active"] == 1
        assert st["roles_published_at"]
        actions = await audit_actions(e.db)
        assert {"panel_user.bound", "panel_user.created", "panel_user.updated", "panel_user.disabled", "spa.sync"} <= set(actions)

        # A failing SPA is reported on the settings page.
        e.spa._conn, e.spa._key, e.spa._conn_loaded = None, connection_key(token="old"), 0.0
        with pytest.raises(ExternalServiceError):
            await e.spa.sync(force=True)
        assert "replaced" in (await e.spa.status())["error"]

    asyncio.run(scenario())


@needs_db
def test_first_sign_in_binds_legacy_user_once(env):
    from app.db.models import PanelUser
    from app.db.repositories import UserRepo

    async def scenario():
        e = await env()
        async with e.db.session() as s:
            old = UserRepo(s).add(PanelUser(username="admin", role=Role.ADMIN, source="local"))
            await s.flush()
            old_id = old.id
        e.fake.add_user("usr_x", "admin", ["admin"])
        e.fake.signed_in = "usr_x"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=e.app), base_url=APP) as client:
            await sign_in(e, client)
            me = (await client.get("/api/auth/me")).json()
            assert me["id"] == old_id and me["external_id"] == "usr_x"
            # Bound by ID from now on: another SPA account named "admin" gets its own record.
            e.fake.users["usr_x"]["username"] = "root"
            e.fake.add_user("usr_y", "admin", ["viewer"])
            e.fake.signed_in = "usr_y"
            client.cookies.clear()
            await sign_in(e, client)
            me = (await client.get("/api/auth/me")).json()
            assert me["id"] != old_id and me["role"] == "viewer"
            # Users are read-only; Synchronize now is for administrators.
            r = await client.post("/api/spa/sync", headers={"X-Requested-With": "network-control"})
            assert r.status_code == 403
        async with e.db.session() as s:
            assert (await UserRepo(s).get(old_id)).username == "admin"

    asyncio.run(scenario())


@needs_db
def test_first_sync_deletes_old_totp_secrets(env):
    from app.db.models import PanelUser
    from app.db.repositories import UserRepo

    async def scenario():
        e = await env()
        async with e.db.session() as s:
            old = UserRepo(s).add(PanelUser(username="admin", role=Role.ADMIN, source="local"))
            await s.flush()
            old_id = old.id
        secrets = e.spa._secrets
        # A user of the database, one deleted before the move (only in OpenBao) and a secret that is not TOTP.
        secrets.d.update({f"users/{old_id}/totp": {"secret": "A"}, "users/999/totp": {"secret": "B"},
                          "notify/telegram": {"token": "t"}, "spa/state_key": {"k": "x"}})
        e.fake.add_user("usr_x", "admin", ["admin"])
        await e.spa.sync()
        assert not [p for p in secrets.d if p.endswith("/totp")]
        assert "notify/telegram" in secrets.d
        assert (await e.spa.state())["legacy_totp_purged_at"]
        assert "spa.legacy_totp_purged" in await audit_actions(e.db)
        # Once: later synchronisations do not touch OpenBao again.
        secrets.d["users/5/totp"] = {"secret": "C"}
        await e.spa.sync(force=True)
        assert "users/5/totp" in secrets.d

    asyncio.run(scenario())


def test_totp_cleanup_failure_does_not_break_sync():
    class Broken(Secrets):
        async def keys(self, prefix):
            raise ExternalServiceError("OpenBao is unreachable")

    async def scenario():
        svc = make(FakeSpa(), secrets=Broken())

        class Users:
            async def all(self):
                return []

        @contextlib.asynccontextmanager
        async def session():
            yield None

        svc._db = SimpleNamespace(session=session)
        orig = spa_mod.UserRepo
        spa_mod.UserRepo = lambda s: Users()
        try:
            assert await svc.purge_legacy_totp() is None
            assert "legacy_totp_purged_at" not in await svc.state()
        finally:
            spa_mod.UserRepo = orig

    asyncio.run(scenario())
