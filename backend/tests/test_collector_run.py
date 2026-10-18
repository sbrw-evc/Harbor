"""The flow collector around the classifier (flow-collector/collector.py): GeoIP, collector.json, the ClickHouse
writer and the main loop over goflow2."""
import asyncio
import datetime as dt
import gzip
import importlib.util
import ipaddress
import json
import pathlib

import httpx
import pytest

pytest.importorskip("maxminddb")

PATH = pathlib.Path(__file__).resolve().parents[2] / "flow-collector" / "collector.py"
REAL_CLIENT = httpx.AsyncClient
REAL_SLEEP = asyncio.sleep


@pytest.fixture
def col(monkeypatch):
    spec = importlib.util.spec_from_file_location("nc_collector_run", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    async def fast(*a, **kw):
        await REAL_SLEEP(0)
    monkeypatch.setattr(mod.asyncio, "sleep", fast)
    return mod


def answering(handler):
    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*a, **kw)
    return factory


class Reader:
    def __init__(self, data):
        self.data = data

    def get(self, ip):
        return self.data.get(ip)


def test_geo_refresh_and_lookup(col, tmp_path, monkeypatch):
    month_before = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")

    def handler(req):
        if req.url.path.endswith(f"dbip-city-lite-{month_before}.mmdb.gz"):
            return httpx.Response(200, content=gzip.compress(b"CITY"))
        if "asn" in req.url.path:
            raise httpx.ConnectError("refused")
        return httpx.Response(404)
    monkeypatch.setattr(col.httpx, "AsyncClient", answering(handler))
    opened = []

    def open_database(path):
        opened.append(path)
        return Reader({"8.8.8.8": {"country": {"iso_code": "US"}, "autonomous_system_number": 15169,
                                   "autonomous_system_organization": "GOOGLE"}})
    monkeypatch.setattr(col.maxminddb, "open_database", open_database)
    geo = col.GeoEnricher(str(tmp_path / "geo"))
    asyncio.run(geo.refresh())
    assert (tmp_path / "geo" / "dbip-city-lite.mmdb").read_bytes() == b"CITY" and len(opened) == 1  # no ASN database
    assert geo.lookup("8.8.8.8") == ("US", 0, "")
    geo._readers["asn"] = Reader({"8.8.8.8": {"autonomous_system_number": 15169, "autonomous_system_organization": "GOOGLE"}})
    geo._cache.clear()
    assert geo.lookup("8.8.8.8") == ("US", 15169, "GOOGLE") and geo.lookup("8.8.8.8") is geo._cache["8.8.8.8"]
    geo._cache.update({str(i): ("", 0, "") for i in range(200_001)})
    assert geo.lookup("1.1.1.1") == ("", 0, "") and len(geo._cache) == 1
    asyncio.run(geo.refresh())  # fresh files are opened, not downloaded
    assert len(opened) == 2


def test_load_config(col, tmp_path):
    path = tmp_path / "collector.json"
    assert col.load_config(str(path)) is False  # no file: the environment stays
    path.write_text(json.dumps({"lan_cidr": "10.0.0.0/8, nonsense", "gateway_ip": "10.0.0.2", "awg_host": "203.0.113.1",
                                "router_host": "10.0.0.1", "clickhouse": {"url": "http://ch:8123", "user": "u",
                                                                          "password": "p", "database": "flows2"}}))
    assert col.load_config(str(path)) is True
    assert col.LAN == [ipaddress.ip_network("10.0.0.0/8")] and (col.GATEWAY, col.VPN_HOST, col.ROUTER) == ("10.0.0.2", "203.0.113.1", "10.0.0.1")
    assert (col.CH_URL, col.CH_USER, col.CH_PASSWORD, col.CH_DB) == ("http://ch:8123", "u", "p", "flows2")
    assert col.load_config(str(path)) is False  # the same again: no reconnect
    path.write_text("not json")
    assert col.load_config(str(path)) is False


def test_classifier_drops(col):
    class Geo:
        def lookup(self, ip):
            return ("", 0, "")
    c = col.Classifier(Geo())
    col.LAN[:] = [ipaddress.ip_network("192.168.0.0/24")]
    col.KEEP_INTERNAL = False
    assert c({"src_addr": "192.168.0.2", "dst_addr": "192.168.0.3"}) is None
    col.VPN_HOST = "203.0.113.1"
    assert c({"src_addr": "192.168.0.2", "dst_addr": "203.0.113.1"}) is None
    assert c({"src_addr": "192.168.0.2"}) is None


def test_clickhouse_writer(col, monkeypatch):
    state = {"exists": 0, "inserts": 0}

    def handler(req):
        q = req.url.params["query"]
        if q.startswith("EXISTS"):
            state["exists"] += 1
            if state["exists"] == 1:
                raise httpx.ConnectError("starting")
            return httpx.Response(200, text="0\n" if state["exists"] == 2 else "1\n")
        if "local_networks" in q:
            return httpx.Response(200, text="10.1.0.0/16\nbad\n2001:db8::/32\n")
        state["inserts"] += 1
        return httpx.Response(500 if state["inserts"] == 1 else 200, text="busy")
    monkeypatch.setattr(col.httpx, "AsyncClient", answering(handler))
    w = col.ClickHouseWriter()

    async def go():
        await w.wait_ready()
        assert state["exists"] == 3
        assert [str(n) for n in await w.local_networks()] == ["10.1.0.0/16", "2001:db8::/32"]
        await w.write([{"Bytes": 1}, {"Bytes": 2}])
        assert w.written == 2 and state["inserts"] == 2
    asyncio.run(go())


def test_clickhouse_writer_failures(col, monkeypatch):
    def down(req):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(col.httpx, "AsyncClient", answering(down))
    w = col.ClickHouseWriter()
    assert asyncio.run(w.local_networks()) == []
    asyncio.run(w.write([{"Bytes": 1}]))  # five attempts, then the rows are dropped
    assert w.written == 0
    monkeypatch.setattr(col.httpx, "AsyncClient", answering(lambda req: httpx.Response(404)))
    w = col.ClickHouseWriter()
    assert asyncio.run(w.local_networks()) == []


def test_wait_for_config(col, tmp_path, monkeypatch):
    col.CONFIG_FILE = str(tmp_path / "collector.json")
    col.CH_PASSWORD = ""
    calls = {"n": 0}
    real = col.load_config

    def load(path=None):
        calls["n"] += 1
        if calls["n"] == 3:
            (tmp_path / "collector.json").write_text(json.dumps({"clickhouse": {"password": "p"}}))
        return real(col.CONFIG_FILE)
    monkeypatch.setattr(col, "load_config", load)
    asyncio.run(col.wait_for_config(every=0))
    assert calls["n"] == 3 and col.CH_PASSWORD == "p"


class Proc:
    def __init__(self, lines):
        self.stdout = asyncio.StreamReader()
        for line in lines:
            self.stdout.feed_data(line)
        self.stdout.feed_eof()

    async def wait(self):
        return 1


class Stop(Exception):
    pass


def test_run_reads_goflow2_and_writes_batches(col, tmp_path, monkeypatch):
    col.CONFIG_FILE = str(tmp_path / "collector.json")
    col.CH_PASSWORD = "p"
    col.GEO_DIR = str(tmp_path / "geo")
    col.BATCH = 2
    col.LAN[:] = [ipaddress.ip_network("192.168.0.0/24")]
    written = []

    async def nothing(self, *a):
        return None
    monkeypatch.setattr(col.GeoEnricher, "refresh", nothing)
    monkeypatch.setattr(col.ClickHouseWriter, "wait_ready", nothing)

    async def no_nets(self):
        return [ipaddress.ip_network("10.9.0.0/24")]

    async def write(self, rows):
        written.append(len(rows))
    monkeypatch.setattr(col.ClickHouseWriter, "local_networks", no_nets)
    monkeypatch.setattr(col.ClickHouseWriter, "write", write)
    flow = {"src_addr": "192.168.0.10", "dst_addr": "8.8.8.8", "proto": "UDP", "src_port": 5000, "dst_port": 53,
            "bytes": 100, "packets": 1, "time_received_ns": 1_700_000_000_000_000_000}
    lines = [json.dumps(flow).encode() + b"\n"] * 3 + [b"not json\n", json.dumps({"src_addr": "x"}).encode() + b"\n"]
    runs = {"n": 0}

    async def exec_(*args, **kw):
        runs["n"] += 1
        if runs["n"] > 1:
            raise Stop()
        assert args[0] == "goflow2" and "-format=json" in args
        return Proc(lines)
    monkeypatch.setattr(col.asyncio, "create_subprocess_exec", exec_)
    with pytest.raises(Stop):
        asyncio.run(col.run())
    assert written == [2, 1]  # a full batch, then the rest when goflow2 exited
