"""Subnet discovery (devices/discovery.py) and the AmneziaWG server over SSH (amnezia.py)."""
import asyncio
import json

import httpx
import pytest
from pysnmp.proto.rfc1902 import ObjectIdentifier, OctetString

from app.adapters import amnezia
from app.adapters.amnezia import DISABLED_MARK, AmneziaDockerGateway, AwgConfigCodec, parse_kv
from app.adapters.devices import discovery
from app.domain.errors import ExternalServiceError
from app.domain.models import PeerEntry


class FakeSnmp:
    answers: dict = {}

    def __init__(self, host, port, creds, timeout=3, retries=1):
        self.host = host

    async def get(self, *oids):
        got = self.answers.get(self.host)
        if isinstance(got, Exception):
            raise got
        return dict(got or {})

    def close(self):
        pass


def web(routes):
    """httpx.AsyncClient of discovery answering per host and path."""
    real = httpx.AsyncClient

    def factory(**kw):
        def handler(req):
            port = req.url.port or (443 if req.url.scheme == "https" else 80)
            hit = routes.get((req.url.host, port, req.url.path))
            if isinstance(hit, Exception):
                raise hit
            return hit or httpx.Response(404)
        kw.pop("verify", None)
        return real(transport=httpx.MockTransport(handler), **kw)
    return factory


def test_probe_and_scan(monkeypatch):
    open_ports = {("192.0.2.1", 22): "SSH-2.0-ROSSSH", ("192.0.2.1", 80): True,
                  ("192.0.2.2", 443): True, ("192.0.2.2", 8043): True,
                  ("192.0.2.3", 443): True}

    async def fake_open(host, port, timeout=1.5):
        return open_ports.get((host, port), False)
    monkeypatch.setattr(discovery, "_open", fake_open)
    monkeypatch.setattr(discovery.httpx, "AsyncClient", web({
        ("192.0.2.1", 80, "/"): httpx.Response(200, text="<title>RouterOS</title>"),
        ("192.0.2.2", 443, "/"): httpx.Response(200, headers={"server": "nginx"}, text="hello"),
        ("192.0.2.2", 443, "/auth"): httpx.Response(401, headers={"X-NDM-Realm": "Keenetic"}),
        ("192.0.2.2", 8043, "/"): httpx.Response(200, text="controller"),
        ("192.0.2.2", 8043, "/api/info"): httpx.Response(200, json={"result": {"omadacId": "x"}}),
        ("192.0.2.2", 8043, "/redfish/v1"): httpx.ConnectError("refused"),
        ("192.0.2.3", 443, "/"): httpx.ConnectError("refused"),
    }))
    FakeSnmp.answers = {
        "192.0.2.1": {"1.3.6.1.2.1.1.1.0": OctetString("RouterOS RB5009"), "1.3.6.1.2.1.1.2.0": ObjectIdentifier("1.3.6.1.4.1.14988.1"),
                      "1.3.6.1.2.1.1.5.0": OctetString("gw")},
        "192.0.2.4": {"1.3.6.1.2.1.1.1.0": OctetString("Printer")},
        "192.0.2.3": RuntimeError("timeout"),
    }
    monkeypatch.setattr(discovery, "SnmpClient", FakeSnmp)

    gw = asyncio.run(discovery.probe("192.0.2.1"))
    assert gw["platforms"][0] == "mikrotik" and gw["ports"] == [22, 80] and gw["name"] == "gw"
    assert gw["snmp"]["vendor"] == "MikroTik" and "SSH-2.0-ROSSSH" in gw["evidence"]
    ctl = asyncio.run(discovery.probe("192.0.2.2"))
    assert set(ctl["platforms"]) == {"keenetic", "omada"} and ctl["snmp"] == {}
    assert asyncio.run(discovery.probe("192.0.2.4"))["platforms"] == ["snmp"]  # SNMP only, nothing recognised
    dead = asyncio.run(discovery.probe("192.0.2.3"))
    assert dead["platforms"] == [] and dead["reachable"]  # port 443 answered, the web page did not

    found = asyncio.run(discovery.scan("192.0.2.0/29"))
    assert [f["host"] for f in found] == ["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"]
    assert [f["host"] for f in asyncio.run(discovery.scan("192.0.2.4/32"))] == ["192.0.2.4"]
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(discovery.scan("10.0.0.0/16"))


def test_open_reads_the_ssh_banner(monkeypatch):
    real = asyncio.open_connection

    async def go():
        async def serve(reader, writer):
            writer.write(b"SSH-2.0-OpenSSH_9.6 Debian\r\n")
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def to_server(host, p):
            return await real("127.0.0.1", port)

        async def refused(host, p):
            raise ConnectionRefusedError()
        try:
            monkeypatch.setattr(asyncio, "open_connection", to_server)
            assert await discovery._open("192.0.2.9", 22) == "SSH-2.0-OpenSSH_9.6 Debian"
            assert await discovery._open("192.0.2.9", 443) is True
            monkeypatch.setattr(asyncio, "open_connection", refused)
            assert await discovery._open("192.0.2.9", 22) is False
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(go())


CONFIG = """[Interface]
PrivateKey = srv
Address = 10.8.1.1/24
ListenPort = 51820
Jc = 4

[Peer]
PublicKey = A=
PresharedKey = P=
AllowedIPs = 10.8.1.2/32

#DISABLED# [Peer]
#DISABLED# PublicKey = B=
#DISABLED# AllowedIPs = 10.8.1.3/32

"""


def test_codec_round_trip():
    codec = AwgConfigCodec()
    iface, lines, peers = codec.parse(CONFIG)
    assert iface == {"PrivateKey": "srv", "Address": "10.8.1.1/24", "ListenPort": "51820", "Jc": "4"}
    assert [(p.public_key, p.address, p.preshared_key, p.disabled) for p in peers] == [
        ("A=", "10.8.1.2/32", "P=", False), ("B=", "10.8.1.3/32", None, True)]
    text = codec.render(lines, peers)
    assert text.count(DISABLED_MARK) == 3 and codec.parse(text)[2] == peers
    assert AwgConfigCodec.peer_lines("C=", None, "10.8.1.4/32") == ["[Peer]", "PublicKey = C=", "AllowedIPs = 10.8.1.4/32"]
    assert AwgConfigCodec.peer_lines("C=", "Q=", "10.8.1.4/32")[2] == "PresharedKey = Q="
    with pytest.raises(ExternalServiceError, match="no \\[Interface\\]"):
        codec.parse("[Peer]\nPublicKey = A=\n")
    assert parse_kv(["# comment", "Key =  spaced value  ", "not a pair"]) == {"Key": "spaced value"}


class Runner:
    def __init__(self, answers):
        self.answers, self.ran = answers, []

    async def run(self, cmd, stdin=None):
        self.ran.append((cmd, stdin))
        for needle, out in self.answers.items():
            if needle in cmd:
                return out
        return ""


DUMP = "srvpriv\tsrvpub\t51820\toff\n" \
       "A=\tP=\t198.51.100.7:40000\t10.8.1.2/32\t1700000000\t100\t200\toff\n" \
       "B=\t(none)\t(none)\t10.8.1.3/32\t0\t0\t0\toff\n" \
       "short\tline\n"


def test_gateway():
    r = Runner({"cat /opt/amnezia/awg/wg0.conf": CONFIG, "show wg0 dump": DUMP,
                "wireguard_server_public_key.key": "SRVPUB=\n", "wireguard_psk.key": "",
                "cat /opt/amnezia/awg/clientsTable": '[{"clientId": "A="}]', "ls -1t": "wg0.conf.1\nwg0.conf.2\n",
                "backups/wg0.conf.1": "old", "--version": "awg v1.0.2\namneziawg-go v0.2.12\n"})
    g = AmneziaDockerGateway(r, "amnezia-awg", "/opt/amnezia/awg", "wg0", "awg")

    async def go():
        assert await g.read_config() == CONFIG
        assert r.ran[-1][0] == "docker exec amnezia-awg sh -c 'cat /opt/amnezia/awg/wg0.conf'"
        await g.apply_config(CONFIG)
        cmd, stdin = r.ran[-1]
        assert stdin == CONFIG and cmd.startswith("docker exec -i amnezia-awg") and "/tmp" not in cmd
        assert "/opt/amnezia/awg/panel-new/wg0.strip" in cmd
        with pytest.raises(ExternalServiceError):
            await g.apply_config("garbage")
        rt = await g.runtime()
        assert set(rt) == {"A=", "B="} and rt["A="].endpoint == "198.51.100.7:40000" and rt["B="].endpoint is None
        assert (rt["A="].rx, rt["A="].tx, rt["A="].latest_handshake) == (100, 200, 1700000000)
        assert await g.server_public_key() == "SRVPUB="
        assert await g.preshared_key() is None
        assert await g.read_clients_table() == [{"clientId": "A="}]
        await g.write_clients_table([{"clientId": "B="}])
        assert json.loads(r.ran[-1][1]) == [{"clientId": "B="}]
        assert await g.list_backups() == ["wg0.conf.1", "wg0.conf.2"]
        assert await g.read_backup("wg0.conf.1") == "old"
        with pytest.raises(ExternalServiceError, match="Invalid backup name"):
            await g.read_backup("../etc/passwd")
        assert await g.versions() == {"tools": "awg v1.0.2", "go": "amneziawg-go v0.2.12"}
    asyncio.run(go())

    empty = AmneziaDockerGateway(Runner({"clientsTable": "not json", "wireguard_psk.key": "PSK=\n"}), "c", "/d", "wg0", "awg")
    assert asyncio.run(empty.read_clients_table()) == []
    assert asyncio.run(empty.preshared_key()) == "PSK="
    assert asyncio.run(empty.versions()) == {"tools": "", "go": ""}
    assert isinstance(PeerEntry, type) and amnezia.DISABLED_MARK == DISABLED_MARK
