"""The "First steps" checklist and the page setup guides: every step follows real data and settings (PostgreSQL,
TEST_DATABASE_URL); the address check and the role → can_fix mapping run without it."""
import asyncio
import datetime as dt
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.domain.models import Role
from app.services.common import Actor
from app.services.onboarding import GUIDES, Facts, OnboardingService, _checklist, _result, is_public_address

URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")
NOW = dt.datetime.now(dt.timezone.utc)


def test_public_address():
    assert is_public_address("https://nc.example.com")
    assert is_public_address("http://192.168.0.210:8080")
    for bad in ("", "http://localhost:8080", "http://127.0.0.1", "http://[::1]:80", "ftp://nc.example.com",
                "http://app.localhost", "nc.example.com"):
        assert not is_public_address(bad), bad


def test_roles_decide_who_can_fix():
    viewer = _result(_checklist(Facts()), Role.VIEWER)
    assert viewer["done"] is False
    assert [s["id"] for s in viewer["steps"]] == ["spa", "router", "vps", "gateway", "address", "device", "flows",
                                                 "vpn_client", "telegram", "backups"]
    assert not any(s["can_fix"] for s in viewer["steps"])
    operator = {s["id"]: s["can_fix"] for s in _result(_checklist(Facts()), Role.OPERATOR)["steps"]}
    assert operator == {"spa": False, "router": False, "vps": False, "gateway": False, "address": False,
                        "device": False, "flows": True, "vpn_client": True,
                        "telegram": False, "backups": False}
    assert all(s["can_fix"] for s in _result(_checklist(Facts()), Role.ADMIN)["steps"])
    # Optional steps do not hold a guide back.
    f = Facts(devices=1, devices_up=1, endpoints=3, spa_connected=True)
    assert _result(GUIDES["devices"](f), Role.ADMIN)["done"] is True
    assert set(GUIDES) == {"devices", "flows", "clients", "routing", "settings"}


class FakeSpa:
    def __init__(self):
        self.st: dict = {}
        self.conn = None

    async def state(self):
        return self.st

    async def connection(self):
        return self.conn


class FakeNotifier:
    def __init__(self):
        self.ok = False

    async def configured(self):
        return self.ok


class FakeBackups:
    def __init__(self):
        self.items: list = []

    def list(self):
        return self.items


class FakeFlows:
    def __init__(self):
        self.total = 0
        self.fail = False

    async def status(self):
        if self.fail:
            raise RuntimeError("ClickHouse is down")
        return {"total_flows": self.total}


@pytest.fixture
def env():
    async def setup(public_urls=("", "http://localhost:8080")):
        from app.db.base import Database
        from app.db.models import Base
        from app.services.settings import SettingsService

        db = Database(URL)
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.create_schema()
        e = SimpleNamespace(db=db, settings=SettingsService(db), spa=FakeSpa(), notifier=FakeNotifier(),
                            backups=FakeBackups(), flows=FakeFlows())
        e.svc = OnboardingService(db, e.settings, e.spa, e.notifier, e.backups, e.flows, list(public_urls))
        return e
    return setup


async def state(svc: OnboardingService, page: str | None = None, role: Role = Role.ADMIN) -> dict[str, bool]:
    r = await (svc.guide(page, role) if page else svc.checklist(role))
    return {s["id"]: s["done"] for s in r["steps"]} | {"_done": r["done"]}


@needs_db
def test_checklist_follows_real_data(env):
    from sqlalchemy import select
    from app.db.models import NetworkDevice, PanelUser, VpnClient
    from app.db.repositories import UserRepo

    async def scenario():
        e = await env()
        await e.settings.update({"auth_mode": "keystone"}, internal=True)
        empty = await state(e.svc)
        assert not any(empty.values())
        # The router seeded from ROUTER_HOST is not a device yet: it has to answer a poll.
        async with e.db.session() as s:
            s.add(NetworkDevice(name="MikroTik", platform="mikrotik", host="192.168.0.1", role="router"))
            s.add(VpnClient(public_key="gw", name="gateway", system=True))
        st = await state(e.svc)
        assert not st["device"] and not st["vpn_client"]
        async with e.db.session() as s:
            d = (await s.execute(select(NetworkDevice))).scalar_one()
            d.last_ok_at = NOW
            s.add(VpnClient(public_key="k1", name="alice-phone"))
            # SPA: connected and synchronised, but nobody holds the admin role there yet.
            UserRepo(s).add(PanelUser(username="bob", role=Role.OPERATOR, source="spa", external_id="usr_b", is_active=True))
        e.spa.conn = object()
        e.spa.st = {"last_sync_at": NOW.isoformat(), "error": None}
        st = await state(e.svc)
        assert st["device"] and st["vpn_client"] and not st["spa"]
        async with e.db.session() as s:
            UserRepo(s).add(PanelUser(username="ann", role=Role.ADMIN, source="spa", external_id="usr_a", is_active=True))
        assert (await state(e.svc))["spa"]
        e.spa.st["error"] = "SPA rejected the connection key"
        assert not (await state(e.svc))["spa"]
        e.spa.st["error"] = None

        # Flows, Telegram (bot and chat in OpenBao, and notifications on), backups (enabled and one on disk).
        e.flows.total = 10
        e.notifier.ok = True
        e.backups.items = [{"name": "backup-1.tar.gz"}]
        st = await state(e.svc)
        assert st["flows"] and st["backups"] and not st["telegram"]
        await e.settings.update({"notify_enabled": True})
        assert (await state(e.svc))["telegram"]
        await e.settings.update({"backup_enabled": False})
        assert not (await state(e.svc))["backups"]
        await e.settings.update({"backup_enabled": True})

        # A failing ClickHouse is "not done", not an error.
        e.flows.fail = True
        assert not (await state(e.svc))["flows"]
        e.flows.fail = False

        st = await state(e.svc)
        assert not st["address"] and not st["router"] and not st["_done"]
        await e.settings.update({"router_host": "192.168.0.1"})
        e.svc._public_urls = ["https://nc.example.com", "http://localhost:8080"]
        st = await state(e.svc)
        assert st["address"] and st["_done"] is True

    asyncio.run(scenario())


@needs_db
def test_local_mode_and_infrastructure_steps(env):
    from app.db.models import Alert, NetworkDevice, PanelUser
    from app.db.repositories import UserRepo
    from app.domain.models import Severity

    async def scenario():
        e = await env()
        await e.settings.update({"auth_mode": "local"}, internal=True)
        st = await state(e.svc)
        assert not st["spa"] and not st["router"] and not st["vps"] and not st["gateway"]
        # Standalone: a local administrator is the sign-in step; Keystone is not needed.
        async with e.db.session() as s:
            UserRepo(s).add(PanelUser(username="root", role=Role.ADMIN, source="local", is_active=True))
        assert (await state(e.svc))["spa"]
        # The router: set in the settings and answering polls.
        await e.settings.update({"router_host": "192.168.0.1"})
        assert not (await state(e.svc))["router"]
        async with e.db.session() as s:
            s.add(NetworkDevice(name="MikroTik", platform="mikrotik", host="192.168.0.1", role="router", last_ok_at=NOW))
        assert (await state(e.svc))["router"]
        # The VPS: set, and not unreachable.
        await e.settings.update({"awg_host": "203.0.113.10"})
        assert (await state(e.svc))["vps"]
        async with e.db.session() as s:
            s.add(Alert(key="server.unreachable", severity=Severity.CRITICAL, title="down"))
        assert not (await state(e.svc))["vps"]
        # The gateway: its address and its own AmneziaWG client config written.
        await e.settings.update({"gateway_ip": "192.168.0.102"})
        assert not (await state(e.svc))["gateway"]
        e.svc._gateway_client = lambda: True
        assert (await state(e.svc))["gateway"]

    asyncio.run(scenario())


@needs_db
def test_page_guides(env):
    from sqlalchemy import select
    from app.db.models import FlowRule, NetworkDevice, NetworkEndpoint, SavedView, VpnClient
    from app.db.repositories import SettingRepo
    from app.services.routing import CUSTOM_KEY, SITE_LIST_KEY

    async def scenario():
        e = await env()
        g = await state(e.svc, "devices")
        assert g == {"add": False, "answers": False, "endpoints": False, "discover": False, "map": False, "_done": False}
        async with e.db.session() as s:
            s.add(NetworkDevice(name="gw", platform="mikrotik", host="192.168.0.1", role="router", last_ok_at=NOW))
            s.add(NetworkDevice(name="sw", platform="snmp", host="192.168.0.2", role="switch", map_x=10.0, map_y=5.0))
            s.add(NetworkEndpoint(mac="11:22:33:44:55:66", ip="192.168.0.50"))
        g = await state(e.svc, "devices")
        assert all(g.values())

        g = await state(e.svc, "routing")
        assert g["router"] and not g["site_list"] and not g["_done"]
        async with e.db.session() as s:
            await SettingRepo(s).put(SITE_LIST_KEY, {"sites": 3})
            await SettingRepo(s).put(CUSTOM_KEY, [{"cidr": "1.2.3.0/24", "comment": ""}])
        g = await state(e.svc, "routing")
        assert g["site_list"] and g["custom"] and g["_done"] and not g["flows"]

        g = await state(e.svc, "flows")
        assert g["device"] and not g["export"] and not g["rules"]
        e.flows.total = 1
        async with e.db.session() as s:
            s.add(FlowRule(name="SYN flood", kind="ddos_syn", params={}, state={}))
            s.add(SavedView(name="Top talkers", state={}, created_by="ann"))
        await e.settings.update({"threat_status": {"prefixes": 1200, "lists": {}}})
        assert all((await state(e.svc, "flows")).values())

        g = await state(e.svc, "clients")
        assert not g["client"] and not g["_done"]
        async with e.db.session() as s:
            s.add(VpnClient(public_key="k1", name="alice", config_downloaded_at=NOW))
        g = await state(e.svc, "clients")
        assert g["client"] and g["config"] and not g["connected"]
        async with e.db.session() as s:
            c = (await s.execute(select(VpnClient))).scalar_one()
            c.last_handshake_at = NOW
        await e.settings.update({"default_valid_days": 30})
        g = await state(e.svc, "clients")
        assert g["connected"] and g["policy"] and g["_done"] and not g["telegram"]

        g = await state(e.svc, "settings")
        assert g["policy"] and not g["spa"] and not g["_done"]
        assert await e.svc.guide("nope", Role.ADMIN) is None

    asyncio.run(scenario())


@needs_db
def test_routes(env):
    from app import main
    from app.api import deps, routes_onboarding
    from app.domain.errors import DomainError

    async def scenario():
        e = await env()
        app = FastAPI()
        app.add_exception_handler(DomainError, main.domain_error)
        app.include_router(routes_onboarding.router)
        app.state.container = SimpleNamespace(onboarding=e.svc)
        role = {"r": Role.OPERATOR}
        app.dependency_overrides[deps.current] = lambda: deps.Principal(Actor("op", role["r"].value), role["r"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nc.test") as h:
            r = (await h.get("/api/onboarding")).json()
            assert r["done"] is False and len(r["steps"]) == 10
            step = {s["id"]: s for s in r["steps"]}
            assert step["spa"] == {"id": "spa", "done": False, "optional": False, "path": "/settings#spa", "action": "",
                                   "can_fix": False}
            assert step["vpn_client"]["can_fix"] is True
            g = (await h.get("/api/guides/clients")).json()
            assert g["steps"][0] == {"id": "client", "done": False, "optional": False, "path": "", "action": "create",
                                     "can_fix": True}
            assert (await h.get("/api/guides/unknown")).status_code == 404
            role["r"] = Role.ADMIN
            assert all(s["can_fix"] for s in (await h.get("/api/onboarding")).json()["steps"])

    asyncio.run(scenario())
