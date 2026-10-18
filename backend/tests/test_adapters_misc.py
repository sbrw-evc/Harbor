"""Small adapters against fake services: GeoIP, the OUI registry, OpenBao, ClickHouse and Telegram."""
import asyncio
import datetime
import gzip
import os
import time

import httpx
import pytest
from httpfake import FakeApi

from app.adapters import geoip, oui
from app.adapters.clickhouse import ClickHouseClient
from app.adapters.geoip import DbIpGeoLocator
from app.adapters.openbao import OpenBaoSecretStore
from app.adapters.oui import OuiDatabase, is_random
from app.adapters.telegram import TOKEN_SECRET, TelegramNotifier
from app.domain.errors import DomainError, ExternalServiceError


REAL_CLIENT = httpx.AsyncClient


def run(coro):
    return asyncio.run(coro)


def patch_client(monkeypatch, module, api: FakeApi):
    """The httpx.AsyncClient a module creates answers from the fake API."""
    def factory(*a, **kw):
        kw.pop("verify", None)
        kw["transport"] = api.transport()
        return REAL_CLIENT(*a, **kw)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


class Reader:
    def __init__(self, data):
        self.data = data

    def get(self, ip):
        return self.data.get(ip)


RECORD = {"country": {"iso_code": "DE", "names": {"en": "Germany", "ru": "Германия"}},
          "city": {"names": {"en": "Berlin"}}, "location": {"latitude": 52.5, "longitude": 13.4}}


def test_geoip_lookup_and_refresh(tmp_path, monkeypatch):
    opened = []

    def open_database(path):
        opened.append(path)
        return Reader({"8.8.8.8": RECORD, "1.1.1.1": {}})
    monkeypatch.setattr(geoip.maxminddb, "open_database", open_database)
    loc = DbIpGeoLocator(str(tmp_path))
    assert loc.lookup("8.8.8.8") is None and opened == []  # no database yet

    today = datetime.date.today()
    month_before = today.replace(day=1) - datetime.timedelta(days=1)
    api = FakeApi({f"/free/dbip-city-lite-{today:%Y-%m}.mmdb.gz": httpx.Response(404),  # not published yet
                   f"/free/dbip-city-lite-{month_before:%Y-%m}.mmdb.gz": httpx.Response(200, content=gzip.compress(b"MMDB"))})
    patch_client(monkeypatch, geoip, api)
    run(loc.refresh())
    assert (tmp_path / "dbip-city-lite.mmdb").read_bytes() == b"MMDB" and len(opened) == 1
    run(loc.refresh())  # fresh enough: nothing is downloaded
    assert len(api.sent) == 2

    p = loc.lookup("8.8.8.8")
    assert (p.country_code, p.country, p.city, p.lat, p.lon) == ("DE", "Германия", "Berlin", 52.5, 13.4)
    assert loc.lookup("8.8.8.8") is p  # cached
    assert loc.lookup("192.168.1.1") is None and loc.lookup("nonsense") is None and loc.lookup("") is None
    assert loc.lookup("1.1.1.1").country is None
    loc._cache.update({str(i): None for i in range(10001)})
    loc.lookup("9.9.9.9")
    assert len(loc._cache) == 1

    # A changed file is reopened (checked at most once a minute).
    loc._checked = 0
    os.utime(tmp_path / "dbip-city-lite.mmdb", (time.time() + 5, time.time() + 5))
    loc.lookup("8.8.8.8")
    assert len(opened) == 2


def test_geoip_download_fails(tmp_path, monkeypatch):
    def refused(req):
        raise httpx.ConnectError("refused")
    patch_client(monkeypatch, geoip, FakeApi({}))
    run(DbIpGeoLocator(str(tmp_path / "geo")).refresh())  # 404 for both months
    api = FakeApi({})
    api.handler = refused
    patch_client(monkeypatch, geoip, api)
    run(DbIpGeoLocator(str(tmp_path / "geo")).refresh())
    assert not (tmp_path / "geo" / "dbip-city-lite.mmdb").exists()


OUI_CSV = """Registry,Assignment,Organization Name,Organization Address
MA-L,001122,Example Corp ,Somewhere
MA-L,BAD,Short,Nowhere
"""


def test_oui(tmp_path, monkeypatch):
    db = OuiDatabase(str(tmp_path))
    assert db.vendor("00:0c:42:00:00:01") == "MikroTik" and db.vendor("") == ""
    assert db.vendor("02:00:00:00:00:01") == "Random MAC" and db.vendor("00:99:99:00:00:01") == ""
    assert is_random("zz") is False and is_random("06:00") is True

    patch_client(monkeypatch, oui, FakeApi({"/oui/oui.csv": OUI_CSV}))
    run(db.refresh())
    assert db.vendor("00:11:22:33:44:55") == "Example Corp"
    assert (tmp_path / "oui.tsv").read_text() == "00:11:22\tExample Corp\n"

    again = OuiDatabase(str(tmp_path))
    run(again.refresh())  # the file is fresh: loaded, not downloaded
    assert again.vendor("00:11:22:33:44:55") == "Example Corp"

    os.utime(tmp_path / "oui.tsv", (0, 0))
    patch_client(monkeypatch, oui, FakeApi({"/oui/oui.csv": httpx.Response(503)}))
    stale = OuiDatabase(str(tmp_path))
    run(stale.refresh())  # the download fails: the old file is used
    assert stale.vendor("00:11:22:33:44:55") == "Example Corp"


def bao(tmp_path, routes):
    (tmp_path / "role_id").write_text("role\n")
    (tmp_path / "secret_id").write_text("secret\n")
    store = OpenBaoSecretStore("http://openbao:8200", str(tmp_path), "amnezia")
    api = FakeApi(routes)
    store._client = api.client("http://openbao:8200")
    return store, api


def test_openbao(tmp_path):
    logins = []

    def login(req):
        logins.append(req)
        return httpx.Response(200, json={"auth": {"client_token": f"t{len(logins)}"}})
    reads = {"n": 0}

    def read(req):
        reads["n"] += 1
        if req.headers["X-Vault-Token"] == "t1" and reads["n"] == 2:
            return httpx.Response(403)  # the token expired: a new login, then the request again
        return httpx.Response(200, json={"data": {"data": {"password": "pw"}}})
    store, api = bao(tmp_path, {
        ("POST", "/v1/auth/approle/login"): login,
        ("GET", "/v1/amnezia/data/db/pg"): read,
        ("GET", "/v1/amnezia/data/missing"): httpx.Response(404),
        ("GET", "/v1/amnezia/data/broken"): httpx.Response(500),
        ("POST", "/v1/amnezia/data/db/pg"): httpx.Response(204),
        ("POST", "/v1/amnezia/data/readonly"): httpx.Response(403),
        ("DELETE", "/v1/amnezia/metadata/gone"): httpx.Response(404),
        ("DELETE", "/v1/amnezia/metadata/locked"): httpx.Response(500),
        ("LIST", "/v1/amnezia/metadata/local/users"): {"data": {"keys": ["1/", "2/"]}},
        ("LIST", "/v1/amnezia/metadata/none"): httpx.Response(404),
        ("LIST", "/v1/amnezia/metadata/denied"): httpx.Response(500),
        ("GET", "/v1/sys/storage/raft/snapshot"): httpx.Response(200, content=b"SNAP"),
        ("GET", "/v1/sys/health"): httpx.Response(200),
    })

    async def go():
        assert await store.get("db/pg") == {"password": "pw"}
        assert await store.get("db/pg") == {"password": "pw"} and len(logins) == 2
        assert logins[0].read() == b'{"role_id":"role","secret_id":"secret"}'
        assert await store.get("missing") is None
        with pytest.raises(ExternalServiceError, match="returned 500"):
            await store.get("broken")
        await store.put("db/pg", {"password": "new"})
        with pytest.raises(ExternalServiceError, match="writing readonly"):
            await store.put("readonly", {})
        await store.delete("gone")
        with pytest.raises(ExternalServiceError, match="deleting locked"):
            await store.delete("locked")
        assert await store.keys("/local/users/") == ["1/", "2/"]
        assert await store.keys("none") == []
        with pytest.raises(ExternalServiceError, match="listing denied"):
            await store.keys("denied")
        assert await store.snapshot() == b"SNAP"
        assert await store.healthy()
    run(go())


def test_openbao_failures(tmp_path):
    store, _ = bao(tmp_path, {("POST", "/v1/auth/approle/login"): httpx.Response(400),
                              ("GET", "/v1/sys/storage/raft/snapshot"): httpx.Response(403)})
    with pytest.raises(ExternalServiceError, match="AppRole login failed"):
        run(store.get("x"))

    def refused(req):
        raise httpx.ConnectError("refused")
    store, api = bao(tmp_path, {("POST", "/v1/auth/approle/login"): {"auth": {"client_token": "t"}},
                                ("GET", "/v1/amnezia/data/x"): refused, ("GET", "/v1/sys/health"): refused,
                                ("GET", "/v1/sys/storage/raft/snapshot"): httpx.Response(403)})
    with pytest.raises(ExternalServiceError, match="unreachable"):
        run(store.get("x"))
    assert run(store.healthy()) is False
    with pytest.raises(ExternalServiceError, match="snapshot unavailable"):
        run(store.snapshot())


def test_clickhouse():
    def query(req):
        sql = req.read().decode()
        if "bad syntax" in sql:
            return httpx.Response(400, text="Code: 62. DB::Exception: Syntax error")
        if "boom" in sql:
            return httpx.Response(500, text="Code: 241. Memory limit\n exceeded")
        return httpx.Response(200, json={"data": [{"n": 1}]})
    api = FakeApi({("POST", "/"): query, "/ping": "Ok.\n"})
    ch = ClickHouseClient("http://clickhouse:8123", "amnezia", "pw", "flows")
    ch._http = api.client("http://clickhouse:8123", headers=ch._http.headers)

    async def go():
        assert await ch.query("SELECT {n:UInt8} AS n", {"n": 1, "s": "x"}) == [{"n": 1}]
        req = api.requests[-1]
        assert req.url.params["param_n"] == "1" and req.url.params["param_s"] == "x" and req.url.params["database"] == "flows"
        assert req.headers["X-ClickHouse-User"] == "amnezia"
        await ch.command("CREATE DATABASE x", database="")
        assert api.requests[-1].url.params["database"] == ""
        await ch.insert("flows.flows", [{"a": 1}, {"b": "й"}])
        assert api.requests[-1].url.params["query"] == "INSERT INTO flows.flows FORMAT JSONEachRow"
        assert api.requests[-1].read().decode() == '{"a": 1}\n{"b": "й"}'
        await ch.insert("t", [])
        with pytest.raises(DomainError, match="Query error"):
            await ch.query("bad syntax")
        with pytest.raises(ExternalServiceError, match="Memory limit exceeded"):
            await ch.query("boom")
        assert await ch.ping()
    run(go())

    def refused(req):
        raise httpx.ConnectError("refused")
    down = FakeApi({("POST", "/"): refused, "/ping": refused})
    ch._http = down.client("http://clickhouse:8123")
    with pytest.raises(ExternalServiceError, match="unreachable"):
        run(ch.query("SELECT 1"))
    assert run(ch.ping()) is False


class Secrets:
    def __init__(self):
        self.d = {}

    async def get(self, path):
        return self.d.get(path)

    async def put(self, path, data):
        self.d[path] = data


def test_telegram():
    chat = {"id": "-100"}

    async def chat_id():
        return chat["id"]
    secrets = Secrets()
    tg = TelegramNotifier(secrets, chat_id)
    api = FakeApi({"/bot1:good/getMe": {"ok": True, "result": {"username": "harbor_bot"}},
                   "/bot1:bad/getMe": httpx.Response(401, json={"ok": False}),
                   ("POST", "/bot1:good/sendMessage"): {"ok": True}})
    tg._http = api.client("https://api.telegram.org")

    async def go():
        assert not await tg.configured()
        with pytest.raises(DomainError, match="not set up"):
            await tg.send("x")
        with pytest.raises(DomainError, match="format"):
            await tg.set_token("nocolon")
        with pytest.raises(DomainError, match="rejected"):
            await tg.set_token("1:bad")
        await tg.set_token(" 1:good ")
        assert secrets.d[TOKEN_SECRET] == {"token": "1:good", "bot": "harbor_bot"}
        assert await tg.configured() and await tg.bot_name() == "harbor_bot"
        await tg.send("<b>alert</b>")
        assert api.sent[-1][2] == {"chat_id": "-100", "text": "<b>alert</b>", "parse_mode": "HTML", "disable_web_page_preview": True}
    run(go())

    api.routes[("POST", "/bot1:good/sendMessage")] = httpx.Response(400, json={"description": "chat not found"})
    with pytest.raises(ExternalServiceError, match="chat not found"):
        run(tg.send("x"))

    def refused(req):
        raise httpx.ConnectError("refused")
    api.routes[("POST", "/bot1:good/sendMessage")] = refused
    with pytest.raises(ExternalServiceError, match="unreachable"):
        run(tg.send("x"))
