"""The setup wizard, the settings that left .env, the local sign-in with its secrets in OpenBao and the move of the
local users to Keystone."""
import asyncio
import json
import os
import stat
from types import SimpleNamespace

import httpx
import pyotp
import pytest
from fastapi import FastAPI

from app.bootstrap import BaoInitializer, Bootstrap, SetupGate, parse_pg_url, pg_url
from app.domain.errors import DomainError, Forbidden, Unauthorized
from app.domain.models import Role
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.common import Actor, EventBus
from app.services.settings import validate
from spafake import FakeSpa, connection_key

URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")
ADMIN = Actor("root", Role.ADMIN.value)
PASSWORD = "Correct-Horse-9"


class Secrets:
    def __init__(self):
        self.d: dict = {}

    async def get(self, path):
        return self.d.get(path)

    async def put(self, path, data):
        self.d[path] = dict(data)

    async def delete(self, path):
        self.d.pop(path, None)

    async def healthy(self):
        return True

    async def keys(self, prefix):
        return []


# Settings ---------------------------------------------------------------------------------------------------------

def test_settings_are_validated():
    assert validate({"lan_cidr": "192.168.0.7/24"})["lan_cidr"] == "192.168.0.0/24"
    assert validate({"awg_host": "VPS.example.com"})["awg_host"] == "vps.example.com"
    assert validate({"public_base_url": "https://nc.example.com/"})["public_base_url"] == "https://nc.example.com"
    assert validate({"port_forwards": "8443=192.168.0.10:443,  2222=192.168.0.11:22"})["port_forwards"] == \
        "8443=192.168.0.10:443 2222=192.168.0.11:22"
    assert validate({"mt_wg_port": "51821"})["mt_wg_port"] == 51821
    for bad in ({"lan_cidr": "nonsense"}, {"gateway_ip": "nc.local"}, {"awg_host": "bad host"},
                {"mt_wg_port": 70000}, {"public_base_url": "ftp://x"}, {"port_forwards": "80=host:80"},
                {"lan_if": "eth0; rm -rf /"}, {"awg_dir": "relative"}):
        with pytest.raises(ValueError):
            validate(bad)


def test_postgres_url_round_trip():
    url = pg_url("db", 5433, "nc", "user@x", "p@ss:w/rd")
    assert parse_pg_url(url) == {"host": "db", "port": 5433, "database": "nc", "user": "user@x", "password": "p@ss:w/rd"}


# The setup code ---------------------------------------------------------------------------------------------------

def test_setup_code(tmp_path):
    gate = SetupGate(str(tmp_path), limit=3)
    code = gate.ensure()
    assert len(code) == 12 and SetupGate(str(tmp_path)).ensure() == code  # shared by the processes
    assert stat.S_IMODE(os.stat(tmp_path / "setup-code").st_mode) == 0o600
    gate.check(code.lower(), "1.1.1.1")
    gate.check(f"{code[:4]}-{code[4:8]} {code[8:]}", "1.1.1.1")  # typed with separators
    for _ in range(3):
        with pytest.raises(Forbidden):
            gate.check("WRONG", "2.2.2.2")
    with pytest.raises(Forbidden, match="Too many"):
        gate.check(code, "2.2.2.2")  # locked out even with the right code
    gate.check(code, "3.3.3.3")
    gate.finish()
    with pytest.raises(Forbidden):
        gate.check(code, "3.3.3.3")


# OpenBao of the compose file ----------------------------------------------------------------------------------------

def fake_bao():
    st = {"initialized": False, "mounts": {}, "auths": {}, "policies": {}, "roles": {}, "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        st["calls"].append((m, p))
        if p == "/v1/sys/init" and m == "GET":
            return httpx.Response(200, json={"initialized": st["initialized"]})
        if p == "/v1/sys/init" and m == "PUT":
            st["initialized"] = True
            return httpx.Response(200, json={"root_token": "root-1", "recovery_keys_b64": ["rec-1"]})
        if p == "/v1/sys/seal-status":
            return httpx.Response(200, json={"sealed": False, "initialized": st["initialized"]})
        if request.headers.get("x-vault-token") != "root-1":
            return httpx.Response(403, json={"errors": ["permission denied"]})
        if p == "/v1/sys/mounts" and m == "GET":
            return httpx.Response(200, json={"data": st["mounts"]})
        if p.startswith("/v1/sys/mounts/"):
            st["mounts"][p.rsplit("/", 1)[1] + "/"] = json.loads(request.content)
            return httpx.Response(204)
        if p == "/v1/sys/auth" and m == "GET":
            return httpx.Response(200, json={"data": st["auths"]})
        if p == "/v1/sys/auth/approle":
            st["auths"]["approle/"] = {"type": "approle"}
            return httpx.Response(204)
        if p.startswith("/v1/sys/policies/acl/"):
            st["policies"][p.rsplit("/", 1)[1]] = json.loads(request.content)["policy"]
            return httpx.Response(204)
        if p == "/v1/auth/approle/role/backend":
            st["roles"]["backend"] = json.loads(request.content)
            return httpx.Response(204)
        if p.endswith("/role-id"):
            return httpx.Response(200, json={"data": {"role_id": "role-1"}})
        if p.endswith("/secret-id"):
            return httpx.Response(200, json={"data": {"secret_id": "secret-1"}})
        return httpx.Response(404)
    return st, httpx.MockTransport(handler)


def test_bao_initializer(tmp_path):
    st, transport = fake_bao()

    async def scenario():
        shown = await BaoInitializer("http://bao", tmp_path / "approle", transport).run()
        assert shown == {"root_token": "root-1", "recovery_key": "rec-1"}
        assert st["mounts"]["amnezia/"]["options"] == {"version": "2"} and "approle/" in st["auths"]
        assert 'path "amnezia/data/*"' in st["policies"]["amnezia-backend"]
        assert (tmp_path / "approle" / "role_id").read_text().strip() == "role-1"
        assert stat.S_IMODE(os.stat(tmp_path / "approle" / "secret_id").st_mode) == 0o600
        # Initialised already: the root token is needed, and nothing is shown again.
        with pytest.raises(DomainError, match="root token"):
            await BaoInitializer("http://bao", tmp_path / "approle", transport).run()
        assert await BaoInitializer("http://bao", tmp_path / "approle", transport).run("root-1") == {}

    asyncio.run(scenario())


def test_bootstrap_file_has_no_password(tmp_path):
    boot = Bootstrap(str(tmp_path), str(tmp_path / "none"))
    boot.write({"postgres": {"host": "postgres", "port": 5432, "database": "amnezia", "user": "amnezia"},
                "clickhouse": {"url": "http://clickhouse:8123", "user": "amnezia", "database": "flows"},
                "openbao": {"addr": "http://openbao:8200", "mount": "amnezia", "approle_dir": str(tmp_path / "approle")}})
    assert stat.S_IMODE(os.stat(boot.path).st_mode) == 0o600
    assert "password" not in boot.path.read_text()
    # Without DATABASE_URL and the old AppRole files there is nothing to take over.
    assert asyncio.run(Bootstrap(str(tmp_path / "other"), str(tmp_path / "none")).import_legacy({})) is False


# The files of the side containers ---------------------------------------------------------------------------------

def test_gateway_files(tmp_path):
    from app.services.gateway_files import GatewayFiles
    rc = SimpleNamespace(lan_if="eth0", tunnel_net="10.8.1.0/24", port_forwards="8443=192.168.0.10:443",
                         lan_cidr="192.168.0.0/24", mt_wg_network="10.9.0.0/24", gateway_ip="192.168.0.102",
                         awg_host="203.0.113.10", router_host="192.168.0.1")
    files = GatewayFiles(str(tmp_path / "gw"), str(tmp_path / "shared"), {"url": "http://clickhouse:8123",
                                                                         "user": "amnezia", "password": "pw"})
    files.write(rc)
    gw = json.loads((tmp_path / "gw" / "gateway.json").read_text())
    assert gw == {"lan_if": "eth0", "tunnel_net": "10.8.1.0/24", "port_forwards": "8443=192.168.0.10:443"}
    col = json.loads((tmp_path / "shared" / "collector.json").read_text())
    assert col["lan_cidr"] == "192.168.0.0/24,10.9.0.0/24" and col["clickhouse"]["password"] == "pw"
    assert stat.S_IMODE(os.stat(tmp_path / "shared" / "collector.json").st_mode) == 0o600
    assert not files.has_awg_conf()
    files.write_awg_conf("[Interface]\nPrivateKey = x")
    assert files.has_awg_conf() and (tmp_path / "gw" / "awg0.conf").read_text().endswith("\n")


# With PostgreSQL --------------------------------------------------------------------------------------------------

@pytest.fixture
def env():
    async def setup():
        from app.db.base import Database
        from app.db.models import Base
        from app.services.identity.spa import SpaService
        from app.services.settings import SettingsService
        from app.services.users import PanelUserService

        db = Database(URL)
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.create_schema()
        secrets = Secrets()
        audit = AuditService(db, EventBus())
        settings = SettingsService(db)
        auth = AuthService(db, audit, 12, secrets)
        fake = FakeSpa()
        spa = SpaService(db, secrets, settings, auth, audit, transport=fake.transport())
        return SimpleNamespace(db=db, secrets=secrets, audit=audit, settings=settings, auth=auth, fake=fake, spa=spa,
                               users=PanelUserService(db, auth, audit))
    return setup


@needs_db
def test_runtime_imports_old_env_once(env):
    from app.runtime import RuntimeConfig

    async def scenario():
        e = await env()
        rc = RuntimeConfig(e.settings)
        built = []
        rc.adapter(lambda r: built.append(r.router_host) or SimpleNamespace(host=r.router_host))
        imported = await rc.import_legacy_env({"AWG_HOST": "203.0.113.10", "ROUTER_HOST": "192.168.0.1",
                                               "LAN_CIDR": "nonsense", "COOKIE_SECURE": "true", "UNRELATED": "x"})
        assert imported == ["awg_host", "cookie_secure", "router_host"]  # the bad LAN is skipped
        assert await rc.refresh_now() and rc.awg_host == "203.0.113.10" and rc.cookie_secure is True
        assert built[-1] == "192.168.0.1" and rc.routing_ctx["vpn_host"] == "203.0.113.10"
        # Once: what the database has is never overwritten by .env again.
        await e.settings.update({"awg_host": "198.51.100.5"})
        assert await rc.import_legacy_env({"AWG_HOST": "203.0.113.10"}) == []
        await rc.refresh_now()
        assert rc.awg_host == "198.51.100.5" and rc.topology_ctx["vpn_host"] == "198.51.100.5"

    asyncio.run(scenario())


@needs_db
def test_local_sign_in_keeps_secrets_in_openbao(env):
    from sqlalchemy import text

    async def scenario():
        e = await env()
        await e.settings.update({"auth_mode": "local"}, internal=True)
        u = await e.users.create(ADMIN, "root", "Root", Role.ADMIN, PASSWORD, must_change=False)
        stored = e.secrets.d[f"local/users/{u['id']}/password"]["hash"]
        assert stored.startswith("$argon2id$")
        async with e.db.engine.connect() as conn:
            cols = {r[0] for r in await conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'panel_users'"))}
            dump = str((await conn.execute(text("SELECT * FROM panel_users"))).all())
        assert "password_hash" not in cols and "argon2" not in dump
        with pytest.raises(DomainError):
            await e.users.create(ADMIN, "weak", "", Role.VIEWER, "short")

        res = await e.auth.login("root", PASSWORD, "10.0.0.1", "test")
        assert not res.requires_2fa
        user, _ = await e.auth.authenticate(res.token)
        assert user.username == "root"
        with pytest.raises(Unauthorized):
            await e.auth.login("root", "wrong-password-1", "10.0.0.2", "test")
        with pytest.raises(Unauthorized):
            await e.auth.login("nobody", PASSWORD, "10.0.0.2", "test")

        # TOTP: the secret goes to OpenBao, and a sign-in waits for the code.
        setup = await e.auth.totp_begin(user)
        await e.auth.totp_enable(user.id, pyotp.TOTP(setup["secret"]).now())
        assert e.secrets.d[f"local/users/{user.id}/totp"]["secret"] == setup["secret"]
        res = await e.auth.login("root", PASSWORD, "10.0.0.3", "test")
        assert res.requires_2fa
        with pytest.raises(Unauthorized):
            await e.auth.authenticate(res.token)  # not a session before the code
        with pytest.raises(Unauthorized):
            await e.auth.verify_2fa(res.token, "000000", "10.0.0.3")
        await e.auth.verify_2fa(res.token, pyotp.TOTP(setup["secret"]).now(), "10.0.0.3")
        assert (await e.auth.authenticate(res.token))[0].id == user.id

        # The lockout after failed attempts.
        for _ in range(5):
            with pytest.raises(Unauthorized):
                await e.auth.login("root", "wrong-password-1", "10.0.1.1", "test")
        with pytest.raises(Unauthorized, match="locked"):
            await e.auth.login("root", PASSWORD, "10.0.1.2", "test")

        # Deleting a user deletes its secrets.
        other = await e.users.create(ADMIN, "op", "", Role.OPERATOR, PASSWORD)
        assert other["must_change_password"]
        await e.users.delete(ADMIN, other["id"])
        assert not [k for k in e.secrets.d if k.startswith(f"local/users/{other['id']}/")]

    asyncio.run(scenario())


@needs_db
def test_move_local_users_to_keystone(env):
    from app.db.repositories import UserRepo

    async def scenario():
        e = await env()
        await e.settings.update({"auth_mode": "local"}, internal=True)
        root = await e.users.create(ADMIN, "root", "Root", Role.ADMIN, PASSWORD, email="root@corp.test")
        op = await e.users.create(ADMIN, "op", "Operator", Role.OPERATOR, PASSWORD)
        e.fake.add_user("usr_old", "op", ["viewer"])  # somebody named "op" is in Keystone already

        # The local mode does not synchronise (it would disable the local users) nor send people to Keystone.
        assert await e.spa.sync() == {"connected": False, "mode": "local"}
        await e.spa.set_key(connection_key(), ADMIN)
        assert (await e.spa.status())["mode"] == "local"

        with pytest.raises(DomainError, match="allow moving users"):
            await e.spa.import_local_users(e.auth, ADMIN)
        e.fake.import_open = True
        report = await e.spa.import_local_users(e.auth, ADMIN)
        assert [x["username"] for x in report["imported"]] == ["root"]
        assert report["conflicts"] == [{"username": "op", "reason": "username_taken"}]
        sent = e.fake.imported[0]
        assert sent["role"] == "admin" and sent["password_hash"].startswith("$argon2id$")
        assert sent["email"] == "root@corp.test"
        assert e.fake.published  # the role catalogue goes first: imported roles must exist
        assert e.fake.import_open is False  # one window, one import

        # Switching: refused while Keystone has no administrator here, then done; records bound by user name.
        e.fake.users["usr_imp2"]["roles"] = ["viewer"]
        with pytest.raises(DomainError, match="administrator role"):
            await e.spa.switch_to_keystone(e.auth, ADMIN, purge_local=True)
        assert await e.settings.get("auth_mode") == "local"
        e.fake.users["usr_imp2"]["roles"] = ["admin"]
        res = await e.spa.switch_to_keystone(e.auth, ADMIN, purge_local=True)
        assert await e.settings.get("auth_mode") == "keystone" and res["purged_local"] == 2
        async with e.db.session() as s:
            users = {u.username: u for u in await UserRepo(s).all()}
        assert users["root"].source == "spa" and users["root"].role == Role.ADMIN and users["root"].is_active
        assert users["op"].id == op["id"] and users["op"].role == Role.VIEWER  # bound to the Keystone "op"
        assert not [k for k in e.secrets.d if k.startswith("local/users/")]
        with pytest.raises(DomainError):
            await e.spa.remove_key(ADMIN)  # the key cannot go while sign-in depends on it
        assert root["id"] == users["root"].id

    asyncio.run(scenario())


@needs_db
def test_setup_account_step(env, tmp_path):
    from app.api import routes_setup
    from app import main

    async def scenario():
        e = await env()
        gate = SetupGate(str(tmp_path))
        code = gate.ensure()
        rc = SimpleNamespace(session_ttl_hours=12, refresh_now=lambda: asyncio.sleep(0))
        c = SimpleNamespace(settings=e.settings, users=e.users, auth=e.auth, spa=e.spa, rc=rc)
        stage = {"v": "account"}

        async def get_stage():
            return stage["v"]
        st = SimpleNamespace(gate=gate, container=c, stage=get_stage, error=None)
        app = FastAPI()
        app.add_exception_handler(DomainError, main.domain_error)
        app.include_router(routes_setup.router)
        app.state.setup = st
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nc.test") as h:
            body = {"mode": "local", "username": "root", "password": PASSWORD, "public_base_url": "https://nc.test"}
            assert (await h.post("/api/setup/account", json=body)).status_code == 403  # no code
            assert (await h.post("/api/setup/account", json=body, headers={"x-setup-code": "WRONG"})).status_code == 403
            r = await h.post("/api/setup/account", json=body, headers={"x-setup-code": code})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["role"] == "admin" and "nc_session=" in r.headers["set-cookie"]
        assert await e.settings.get("auth_mode") == "local"
        assert await e.settings.get("public_base_url") == "https://nc.test"
        assert not (tmp_path / "setup-code").exists()  # the code is gone with the wizard

    asyncio.run(scenario())
