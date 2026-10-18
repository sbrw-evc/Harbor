"""The whole backend through its HTTP API: the real container against PostgreSQL (TEST_DATABASE_URL), ClickHouse on
chdb, and fake hosts behind SSH — the AmneziaWG container of the VPS and RouterOS of the MikroTik."""
import asyncio
import json
import os
import re
import shlex
import time
from types import SimpleNamespace

import httpx
import pytest
from chfake import ChdbClient
from test_devices_service import SNAPS, FakeDriver

from app import container as container_module
from app.adapters.amnezia import AwgConfigCodec
from app.adapters.devices import PLATFORMS, Platform
from app.bootstrap import Storage
from app.config import Settings
from app.db.base import Database
from app.db.models import Base
from app.main import app

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL is not set")
PASSWORD = "Correct-Horse-9"
HEADERS = {"x-requested-with": "network-control"}

AWG_CONFIG = """[Interface]
PrivateKey = c2VydmVyLXByaXZhdGUta2V5LWZvci10ZXN0cy0wMDA=
Address = 10.8.1.1/24
ListenPort = 51820
Jc = 4
Jmin = 40
Jmax = 70
S1 = 0
S2 = 0
H1 = 1
H2 = 2
H3 = 3
H4 = 4
"""


class Hosts:
    """The VPS and the router the panel reaches over SSH, with their state."""

    def __init__(self):
        self.awg_conf = AWG_CONFIG
        self.clients_table: list = []
        self.addresses: set[str] = set()
        self.mt_peers: dict[str, dict] = {}
        self.ran: list[str] = []

    # --- the AmneziaWG container (docker exec ... sh -c '<cmd>') ---
    def awg(self, cmd: str, stdin: str | None) -> str:
        if "syncconf" in cmd:
            self.awg_conf = stdin
            return ""
        if "clientsTable.tmp" in cmd:
            self.clients_table = json.loads(stdin)
            return ""
        if "clientsTable" in cmd:
            return json.dumps(self.clients_table)
        if "show" in cmd and "dump" in cmd:
            _, _, peers = AwgConfigCodec().parse(self.awg_conf)
            rows = ["priv\tpub\t51820\toff"]
            for i, p in enumerate(peers):
                rows.append(f"{p.public_key}\t(none)\t198.51.100.{i + 2}:5000{i}\t{p.address}\t{int(time.time()) - 30}"
                            f"\t{1000 * (i + 1)}\t{2000 * (i + 1)}\toff")
            return "\n".join(rows) + "\n"
        if "wireguard_server_public_key" in cmd:
            return "c2VydmVyLXB1YmxpYy1rZXktZm9yLXRlc3RzLTAwMDA=\n"
        if "wireguard_psk" in cmd:
            return ""
        if "ls -1t" in cmd:
            return "awg0.conf.20260101-000000\n"
        if "/backups/" in cmd:
            return AWG_CONFIG
        if "--version" in cmd:
            return "awg v1.0.20240213\namneziawg-go 0.2.12\n"
        if cmd.startswith("cat ") and cmd.endswith(".conf"):
            return self.awg_conf
        return ""

    # --- RouterOS ---
    def routeros(self, cmd: str) -> str:
        if "count-only" in cmd:
            return (f"count={len(self.addresses)}\nroute=10.8.1.1|true|1\nmangle=amnezia: route|10|1\n"
                    "cpu=3\nuptime=1d\nversion=7.14.2 (stable)\nboard=RB5009\n")
        if ":foreach i in=[/ip firewall address-list find list=vpn-sites" in cmd:
            return "\n".join(sorted(self.addresses)) + "\n"
        if cmd.startswith("/ip firewall address-list"):
            for c in re.findall(r"add list=vpn-sites address=(\S+)", cmd):
                self.addresses.add(c)
            for c in re.findall(r"remove \[find list=vpn-sites address=(\S+) dynamic=no\]", cmd):
                self.addresses.discard(c)
            return ""
        if cmd.startswith(":local i [/interface wireguard find"):
            return "key=bWlrcm90aWstcHVibGljLWtleS1mb3ItdGVzdHMtMDA=\nport=51820\nrunning=true\ndisabled=false\nmtu=1420\n"
        if "/ip cloud get public-address" in cmd:
            return "203.0.113.9\n"
        if ":foreach p in=[/interface wireguard peers find" in cmd:
            return "".join(f"{k}|198.51.100.50|51000|1m|10|20|{p['address']}|{str(p['disabled']).lower()}|{p['comment']}\n"
                           for k, p in self.mt_peers.items())
        if cmd.startswith("/interface wireguard peers add"):
            key = re.search(r'public-key="([^"]+)"', cmd).group(1)
            self.mt_peers[key] = {"address": re.search(r"allowed-address=(\S+)", cmd).group(1),
                                  "comment": re.search(r'comment="([^"]*)"', cmd).group(1), "disabled": False}
            return ""
        if cmd.startswith("/interface wireguard peers set"):
            key = re.search(r'public-key="([^"]+)"\]', cmd).group(1)
            p = self.mt_peers[key]
            if "disabled=yes" in cmd or "disabled=no" in cmd:
                p["disabled"] = "disabled=yes" in cmd
            new = re.search(r'\] .*public-key="([^"]+)"', cmd)
            if new:
                self.mt_peers[new.group(1)] = self.mt_peers.pop(key)
            return ""
        if cmd.startswith("/interface wireguard peers remove"):
            self.mt_peers.pop(re.search(r'public-key="([^"]+)"', cmd).group(1), None)
            return ""
        return ""


HOSTS = Hosts()


class FakeSshRunner:
    def __init__(self, host, user, keys, known_hosts):
        self.host = host

    async def run(self, command, stdin=None, timeout=30):
        HOSTS.ran.append(command)
        if command.startswith("docker exec"):
            return HOSTS.awg(shlex.split(command)[-1], stdin)
        return HOSTS.routeros(command)


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
        prefix = prefix.strip("/") + "/"
        return sorted({k[len(prefix):].split("/")[0] + "/" for k in self.d if k.startswith(prefix)})

    async def snapshot(self):
        return b"raft-snapshot"


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(container_module, "SshRunner", FakeSshRunner)
    monkeypatch.setattr(container_module, "ClickHouseClient", lambda *a: ChdbClient(str(tmp_path / "ch"), "api_flows"))
    PLATFORMS["fake"] = Platform("fake", "Fake", "Fake", "any", "router", {"api": 1}, ("password",), FakeDriver)
    for d in ("gateway", "shared", "backups", "geoip"):
        (tmp_path / d).mkdir()
    cfg = Settings(data_dir=str(tmp_path), geoip_dir=str(tmp_path / "geoip"), backup_dir=str(tmp_path / "backups"),
                   gateway_config_dir=str(tmp_path / "gateway"), shared_dir=str(tmp_path / "shared"),
                   bao_approle_dir=str(tmp_path / "approle"))
    storage = Storage(database_url=URL, clickhouse_url="http://clickhouse:8123", clickhouse_user="u",
                      clickhouse_password="p", clickhouse_db="flows", bao_addr="http://openbao:8200",
                      bao_mount="amnezia", bao_approle_dir=str(tmp_path / "approle"), secrets=Secrets())

    async def drop():
        db = Database(URL)
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.engine.dispose()
    asyncio.run(drop())
    yield SimpleNamespace(cfg=cfg, storage=storage, tmp=tmp_path)
    PLATFORMS.pop("fake", None)


class Api:
    """The panel through HTTP, signed in as the administrator; every answer is checked for success unless expected."""

    def __init__(self, client: httpx.AsyncClient):
        self.h = client

    async def call(self, method, path, expect=200, **kw):
        r = await self.h.request(method, path, headers={**HEADERS, **kw.pop("headers", {})}, **kw)
        assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text[:400]}"
        ctype = r.headers.get("content-type", "")
        return r.json() if ctype.startswith("application/json") else r.text

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, json=body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.call("PUT", path, json=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.call("PATCH", path, json=body, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)


def test_the_panel_through_its_api(stack):
    async def scenario():
        c = container_module.Container(stack.cfg, stack.storage)

        async def no_download(*a, **kw):
            return None
        c.geo.refresh = no_download
        c.oui.refresh = no_download
        await c.start()
        app.state.container = c
        app.state.setup = SimpleNamespace(stage=lambda: asyncio.sleep(0, "done"), container=c, gate=None, error=None)
        try:
            await c.settings.update({"auth_mode": "local"}, internal=True)
            from app.domain.models import Role
            from app.services.common import Actor
            await c.users.create(Actor("setup", "admin"), "admin", "Admin", Role.ADMIN, PASSWORD, must_change=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://nc.test") as h:
                await walk(Api(h), c, h)
        finally:
            await c._lose_leadership()
            await c.stop()
            app.state.container = None
    asyncio.run(scenario())


async def walk(api: Api, c, h: httpx.AsyncClient):
    # --- signing in ---
    assert (await api.get("/api/health"))["ok"]
    assert (await api.get("/api/auth/config"))["mode"] == "local"
    await api.get("/api/auth/me", expect=401)
    await api.post("/api/auth/login", {"username": "admin", "password": "wrong-Password-1"}, expect=401)
    me = await api.post("/api/auth/login", {"username": "admin", "password": PASSWORD})
    assert me["user"]["role"] == "admin"
    assert (await api.get("/api/auth/me"))["username"] == "admin"
    await api.post("/api/clients", {"name": "x"}, expect=403, headers={"x-requested-with": ""})  # CSRF
    assert len(await api.get("/api/auth/sessions")) == 1

    # --- the infrastructure ---
    await api.put("/api/settings", {"awg_host": "203.0.113.10", "router_host": "192.168.0.1", "lan_cidr": "192.168.0.0/24",
                                    "gateway_ip": "192.168.0.210", "public_base_url": "https://nc.test",
                                    "client_dns": "1.1.1.1, 1.0.0.1"})
    await c.rc.refresh_now()
    settings = await api.get("/api/settings")
    assert settings["awg_host"] == "203.0.113.10"
    infra = await api.get("/api/infra")
    assert infra and await api.post("/api/infra/check", {"target": "router"}) is not None
    assert (await api.get("/api/server/info")) is not None
    assert "[Interface]" in (await api.get("/api/server/config"))["config"]
    assert await api.get("/api/server/backups/awg0.conf.20260101-000000")
    assert (await api.get("/api/routing/status")) is not None
    await api.post("/api/gateway/client")
    assert os.path.exists(os.path.join(c.cfg.gateway_config_dir, "awg0.conf"))
    mt = await api.get("/api/mikrotik-vpn")
    assert mt["configured"] and mt["public_key"]
    await api.post("/api/mikrotik-vpn/setup")
    assert any("/interface wireguard add name=wg-nc" in r for r in HOSTS.ran)
    assert (await api.get("/api/opnsense-vpn")) is not None

    # --- VPN clients on Amnezia and MikroTik ---
    servers = await api.get("/api/clients/servers")
    assert {s["kind"] for s in servers} >= {"amnezia", "mikrotik"}
    anna = await api.post("/api/clients", {"name": "Anna's phone", "owner": "Anna", "tags": ["family"], "quota_gb": 50})
    tv = await api.post("/api/clients", {"name": "TV", "server": "mikrotik"})
    assert "Anna" in HOSTS.awg_conf or anna["public_key"] in HOSTS.awg_conf
    assert tv["public_key"] in HOSTS.mt_peers
    listed = await api.get("/api/clients")
    assert {x["name"] for x in (listed["items"] if isinstance(listed, dict) else listed)} >= {"Anna's phone", "TV"}
    assert "family" in await api.get("/api/clients/tags")
    await api.patch(f"/api/clients/{anna['id']}", {"note": "work", "expires_at": "2030-01-01T00:00:00Z"})
    await api.post(f"/api/clients/{anna['id']}/block")
    await api.post(f"/api/clients/{anna['id']}/unblock")
    old_key = tv["public_key"]
    await api.post(f"/api/clients/{tv['id']}/reissue")
    assert old_key not in HOSTS.mt_peers
    cfg = await api.get(f"/api/clients/{anna['id']}/config")
    assert "[Interface]" in (cfg.get("config") or json.dumps(cfg))
    assert "[Interface]" in await api.get(f"/api/clients/{anna['id']}/config.conf")
    link = await api.post(f"/api/clients/{anna['id']}/share", {"hours": 2})
    token = link["url"].rstrip("/").split("/")[-1]
    shared = await api.get(f"/api/public/share/{token}")
    assert "[Interface]" in json.dumps(shared)
    await api.post("/api/clients/sync")
    await api.post("/api/clients/bulk", {"ids": [anna["id"]], "action": "block"})
    await api.post("/api/clients/bulk", {"ids": [anna["id"]], "action": "unblock"})

    # --- traffic, analytics, alerts ---
    await c.collector.poll()
    await c.collector.poll()
    await c.policy.run()
    await c.health.run()
    await c.routing.monitor()
    for path in ("/api/analytics/summary", "/api/analytics/live", "/api/analytics/traffic?period=day",
                 "/api/analytics/top", "/api/analytics/heatmap", "/api/analytics/geo", "/api/alerts", "/api/audit",
                 "/api/onboarding", "/api/guides/clients", "/api/notifications"):
        assert await api.get(path) is not None, path
    alerts = await api.get("/api/alerts")
    items = alerts["items"] if isinstance(alerts, dict) else alerts
    if items:
        await api.post(f"/api/alerts/{items[0]['id']}/ack")

    # --- routing ---
    await api.post("/api/routing/custom", {"target": "203.0.113.0/24", "comment": "office"})
    assert "203.0.113.0/24" in HOSTS.addresses
    assert any(x["cidr"] == "203.0.113.0/24" for x in await api.get("/api/routing/custom"))
    assert (await api.post("/api/routing/check", {"target": "203.0.113.7"})) is not None
    await api.delete("/api/routing/custom/203.0.113.0/24")
    assert "203.0.113.0/24" not in HOSTS.addresses
    sites = {"youtube": {"name": "YouTube", "group": "video", "cidr4": ["198.51.100.0/24"], "ip4": ["192.0.2.7"],
                         "replace": {"cidr4": {"198.51.100.0/24": ["198.51.100.0/25", "198.51.100.128/25"]}}},
             "lan": {"cidr4": ["192.168.5.0/24", "::1/128", "nonsense"]}, "junk": "x"}
    files = {"file": ("sites.json", json.dumps(sites).encode(), "application/json")}
    r = await h.post("/api/routing/site-list", files=files, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert "198.51.100.0/24" in HOSTS.addresses

    # --- devices and the network map ---
    dev = await api.post("/api/devices", {"platform": "fake", "host": "router", "role": "router", "credentials": {"password": "x"}})
    await asyncio.sleep(0.5)  # creation polls in the background
    await api.post(f"/api/devices/{dev['id']}/poll")
    assert (await api.get(f"/api/devices/{dev['id']}"))["name"] == "gw"
    # The MikroTik of the settings is added by itself next to the new device.
    assert {d["platform"] for d in await api.get("/api/devices")} == {"fake", "mikrotik"} and await api.get("/api/devices/platforms")
    await api.patch(f"/api/devices/{dev['id']}", {"note": "rack 1"})
    eps = await api.get("/api/endpoints")
    mac = (eps["items"] if isinstance(eps, dict) else eps)[0]["mac"]
    await api.patch(f"/api/endpoints/{mac}", {"alias": "tv", "note": "living room"})
    topo = await api.get("/api/topology")
    assert topo["nodes"]
    await api.put("/api/topology/positions", {f"dev:{dev['id']}": [10.0, 20.0]})
    await api.post(f"/api/devices/{dev['id']}/reset-host-key")
    await api.delete(f"/api/endpoints/{mac}")
    await api.delete(f"/api/devices/{dev['id']}")

    # --- flows (ClickHouse on chdb) ---
    for path in ("/api/flows/fields", "/api/flows/status", "/api/flows/overview", "/api/flows/filters",
                 "/api/flows/exporters", "/api/flows/rules/kinds", "/api/flows/rules", "/api/flows/threats",
                 "/api/flows/views"):
        assert await api.get(path) is not None, path
    await api.post("/api/flows/visualize", {"dimensions": ["Application"], "limit": 5})
    await api.post("/api/flows/visualize", {"dimensions": ["LocalHost", "RemoteCountry"], "graph": "sankey", "limit": 5})
    await api.post("/api/flows/recent", {"limit": 5})
    f = await api.post("/api/flows/filters", {"name": "dns", "filter": "RemotePort = 53 AND Proto = UDP"})
    await api.delete(f"/api/flows/filters/{f['id']}")
    v = await api.post("/api/flows/views", {"name": "top", "state": {"graph": "stacked"}})
    await api.delete(f"/api/flows/views/{v['id']}")
    kinds = await api.get("/api/flows/rules/kinds")
    kind = (kinds[0]["kind"] if isinstance(kinds[0], dict) else kinds[0]) if kinds else "port_scan"
    rule = await api.post("/api/flows/rules", {"name": "scan", "kind": kind})
    await api.patch(f"/api/flows/rules/{rule['id']}", {"enabled": False})
    await api.post(f"/api/flows/rules/{rule['id']}/test")
    await api.delete(f"/api/flows/rules/{rule['id']}")
    await c.detector.run()

    # --- people, tokens, backups ---
    u = await api.post("/api/users", {"username": "viewer1", "password": PASSWORD, "role": "viewer"})
    await api.patch(f"/api/users/{u['id']}", {"display_name": "Viewer"})
    await api.post(f"/api/users/{u['id']}/password", {"password": PASSWORD + "x"})
    assert len(await api.get("/api/users")) == 2
    await api.delete(f"/api/users/{u['id']}")
    tok = await api.post("/api/tokens", {"name": "ci", "role": "viewer", "days": 30})
    secret = tok.get("token") or tok.get("secret")
    r = await h.get("/api/clients", headers={"authorization": f"Bearer {secret}"})
    assert r.status_code == 200, r.text
    tokens = await api.get("/api/tokens")
    await api.delete(f"/api/tokens/{(tokens[0] if isinstance(tokens, list) else tokens['items'][0])['id']}")
    assert (await api.get("/api/spa")) is not None
    backup = await api.post("/api/backups")
    name = backup.get("name") or (await api.get("/api/backups"))[0]["name"]
    assert await api.get(f"/api/backups/{name}")
    await api.delete(f"/api/backups/{name}")

    # --- the end of the clients and the session ---
    await api.delete(f"/api/clients/{tv['id']}")
    assert tv["public_key"] not in HOSTS.mt_peers
    await api.delete(f"/api/clients/{anna['id']}")
    await api.post("/api/auth/logout")
    await api.get("/api/auth/me", expect=401)
