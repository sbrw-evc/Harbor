"""The SNMP driver of last resort (snmp.py): the table parsing against a fake agent, the client against fake pysnmp."""
import asyncio

import pytest
from pysnmp.proto.rfc1902 import Counter32, Counter64, Gauge32, Integer, IpAddress, ObjectIdentifier, OctetString, TimeTicks

from app.adapters.devices import snmp
from app.adapters.devices.base import Credentials, Target, UnsupportedAction
from app.adapters.devices.snmp import IF, LLDP_REM, OIDS, SnmpClient, SnmpDriver, _int, _mac, _str, vendor_from_oid
from app.domain.errors import ExternalServiceError

MAC1 = OctetString(hexValue="00a0c9000001")


class FakeAgent:
    """{oid: value} for GET, {subtree: {suffix: value}} for walks; records SETs."""

    def __init__(self, scalars: dict, tables: dict):
        self.scalars, self.tables, self.sets = scalars, tables, []
        self.closed = False

    async def get(self, *oids):
        return {o: self.scalars[o] for o in oids if o in self.scalars}

    async def walk(self, oid, limit=20000):
        t = self.tables.get(oid)
        if isinstance(t, Exception):
            raise t
        return dict(t or {})

    async def set(self, oid, value):
        self.sets.append((oid, value))

    def close(self):
        self.closed = True


def switch_agent():
    return FakeAgent(
        {OIDS["sysDescr"]: OctetString("TL-SG3428 JetStream 28-Port\nSoftware 2.0"),
         OIDS["sysObjectID"]: ObjectIdentifier("1.3.6.1.4.1.11863.1.1.3"),
         OIDS["sysUpTime"]: TimeTicks(123400), OIDS["sysName"]: OctetString("core-sw")},
        {IF["descr"]: {"1": OctetString("gigabitEthernet 1/0/1"), "2": OctetString("gigabitEthernet 1/0/2"),
                       "100": OctetString("Vlan1")},
         IF["name"]: {"1": OctetString("Gi1/0/1"), "2": OctetString("Gi1/0/2")},
         IF["type"]: {"1": Integer(6), "2": Integer(6), "100": Integer(136)},
         IF["mtu"]: {"1": Integer(1500)},
         IF["speed"]: {"1": Gauge32(1_000_000_000), "100": Gauge32(0)},
         IF["highspeed"]: {"2": Gauge32(10000)},
         IF["mac"]: {"1": MAC1, "2": OctetString(hexValue="00a0c9000002")},
         IF["admin"]: {"1": Integer(1), "2": Integer(2)},
         IF["oper"]: {"1": Integer(1), "2": Integer(2)},
         IF["in"]: {"1": Counter32(10)}, IF["out"]: {"1": Counter32(20)},
         IF["hcin"]: {"2": Counter64(1000)}, IF["hcout"]: {"2": Counter64(2000)},
         IF["alias"]: {"1": OctetString("uplink")},
         snmp.IP_ADDR_IF: {"192.168.0.2": Integer(100)},
         snmp.IP_ADDR_MASK: {"192.168.0.2": IpAddress("255.255.255.0")},
         snmp.LLDP_LOC_PORT: {"1": OctetString("Gi1/0/1")},
         LLDP_REM["name"]: {"0.1.1": OctetString("gw"), "0.9.2": OctetString("orphan")},
         LLDP_REM["chassis"]: {"0.1.1": OctetString(hexValue="000c42000001")},
         LLDP_REM["port"]: {"0.1.1": OctetString("ether2")},
         LLDP_REM["desc"]: {"0.1.1": OctetString("MikroTik RouterOS 7.14")},
         LLDP_REM["portdesc"]: {},
         snmp.ARP: {"100.192.168.0.50": OctetString(hexValue="aabbcc000050"), "bad": OctetString("x")},
         snmp.BRIDGE_IFINDEX: {"1": Integer(1), "2": Integer(2)},
         snmp.QFDB_PORT: {"10.170.187.204.0.0.80": Integer(2), "10.0.12.66.0.0.1": Integer(1),
                          "1.2.3": Integer(2), "1.170.187.204.0.0.81": Integer(0)}})


def test_value_helpers():
    assert _str(OctetString("abc")) == "abc" and _str(OctetString(hexValue="0001")) == "0001"
    assert _str(OctetString("line 1\nline 2")) == "line 1\nline 2"
    assert _str(OctetString(hexValue="ff")) == "ff"  # not UTF-8
    assert _str(Integer(5)) == "5"
    assert _mac(MAC1) == "00:a0:c9:00:00:01" and _mac(OctetString("00:a0:c9:00:00:09")) == "00:a0:c9:00:00:09"
    assert _mac(Integer(1)) == ""
    assert _int(Integer(3)) == 3 and _int(None) is None and _int("x") is None
    assert vendor_from_oid(".1.3.6.1.4.1.14988.1") == "MikroTik" and vendor_from_oid("1.3.6.1.4.1.999999") == "enterprise 999999"
    assert vendor_from_oid("1.3.6.1.2.1") == ""


def test_collect_switch():
    d = SnmpDriver(Target("192.0.2.2"), Credentials())
    d.snmp = switch_agent()
    snap = asyncio.run(d.collect())
    f = snap.facts
    assert (f.hostname, f.vendor, f.model, f.uptime_seconds, f.mac) == ("core-sw", "TP-Link", "TL-SG3428 JetStream 28-Port", 1234, "00:a0:c9:00:00:01")
    gi1, gi2, vlan = snap.interfaces
    assert (gi1.name, gi1.description, gi1.speed_mbps, gi1.rx_bytes, gi1.tx_bytes, gi1.mtu, gi1.oper_up) == ("Gi1/0/1", "uplink", 1000, 10, 20, 1500, True)
    assert (gi2.speed_mbps, gi2.rx_bytes, gi2.admin_up) == (10000, 1000, False)
    assert (vlan.name, vlan.addresses, vlan.speed_mbps) == ("Vlan1", ["192.168.0.2/24"], None)
    gw, orphan = snap.neighbors
    assert (gw.local_interface, gw.remote_name, gw.remote_mac, gw.remote_interface) == ("Gi1/0/1", "gw", "00:0c:42:00:00:01", "ether2")
    assert (orphan.local_interface, orphan.remote_mac) == ("9", "")
    arp = [e for e in snap.endpoints if e.source == "arp"]
    assert [(e.ip, e.mac, e.interface) for e in arp] == [("192.168.0.50", "aa:bb:cc:00:00:50", "Vlan1")]
    # The MAC behind the LLDP uplink (Gi1/0/1) and the one on port 0 are left out.
    assert [(e.mac, e.interface, e.vlan) for e in snap.endpoints if e.source == "bridge"] == [("aa:bb:cc:00:00:50", "Gi1/0/2", 10)]


def test_collect_survives_failing_columns_and_uses_the_old_bridge_table():
    agent = switch_agent()
    agent.tables[IF["alias"]] = ExternalServiceError("timeout")
    agent.tables[snmp.QFDB_PORT] = {}
    agent.tables[snmp.FDB_PORT] = {"170.187.204.0.0.80": Integer(2)}
    agent.scalars = {}
    d = SnmpDriver(Target("192.0.2.2", port=1161, transport="snmp"), Credentials(), "Netgear")
    assert d.snmp.port == 1161
    d.snmp = agent
    snap = asyncio.run(d.collect())
    assert snap.facts.hostname == "" and snap.facts.vendor == "Netgear" and snap.facts.uptime_seconds is None
    assert snap.interfaces[0].description == ""
    assert [(e.interface, e.vlan) for e in snap.endpoints if e.source == "bridge"] == [("Gi1/0/2", None)]


def test_apply():
    d = SnmpDriver(Target("192.0.2.2"), Credentials())
    d.snmp = switch_agent()

    async def go():
        assert await d.apply("hostname", {"name": "core"}) == "Done"
        assert d.snmp.sets[-1][0] == OIDS["sysName"] and str(d.snmp.sets[-1][1]) == "core"
        assert await d.apply("interface", {"name": "Gi1/0/2", "enabled": False, "description": "spare"}) == "Done"
        assert [(o, int(v) if isinstance(v, Integer) else str(v)) for o, v in d.snmp.sets[-2:]] == [
            (f"{IF['admin']}.2", 2), (f"{IF['alias']}.2", "spare")]
        d.snmp.tables[IF["name"]] = {}
        assert await d.apply("interface", {"name": "Vlan1", "enabled": True}) == "Done"  # found by ifDescr
        with pytest.raises(ExternalServiceError, match="not found"):
            await d.apply("interface", {"name": "Gi9", "enabled": True})
        with pytest.raises(UnsupportedAction):
            await d.apply("interface", {"name": "Gi1/0/1", "description": "only"})
        await d.close()
        assert d.snmp.closed
    asyncio.run(go())


class Status:
    def __init__(self, text):
        self.text = text

    def __bool__(self):
        return bool(self.text)

    def prettyPrint(self):
        return self.text


def test_client_get_walk_set(monkeypatch):
    async def target(*a, **kw):
        return "udp-target"
    monkeypatch.setattr(snmp.UdpTransportTarget, "create", target)
    answers = {"get": (None, Status(""), 0, [(ObjectIdentifier("1.3.6.1.2.1.1.5.0"), OctetString("sw")),
                                             (ObjectIdentifier("1.3.6.1.2.1.1.6.0"), type("NoSuchObject", (), {})())])}

    async def get_cmd(*a):
        return answers["get"]

    async def bulk_walk_cmd(*a, **kw):
        yield None, Status(""), 0, [(ObjectIdentifier("1.3.6.1.2.1.2.2.1.2.1"), OctetString("eth0")),
                                    (ObjectIdentifier("1.3.6.1.2.1.2.2.1.2.2"), OctetString("eth1"))]
        yield None, Status(""), 0, [(ObjectIdentifier("1.3.6.1.2.1.2.2.1.3.1"), Integer(6))]  # past the subtree

    async def set_cmd(*a):
        return answers.get("set", (None, Status(""), 0, []))
    monkeypatch.setattr(snmp, "get_cmd", get_cmd)
    monkeypatch.setattr(snmp, "bulk_walk_cmd", bulk_walk_cmd)
    monkeypatch.setattr(snmp, "set_cmd", set_cmd)

    c = SnmpClient("192.0.2.2", 161, Credentials(snmp_version="1", snmp_community=""))

    async def go():
        assert list(await c.get("1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.1.6.0")) == ["1.3.6.1.2.1.1.5.0"]
        assert {k: str(v) for k, v in (await c.walk("1.3.6.1.2.1.2.2.1.2")).items()} == {"1": "eth0", "2": "eth1"}
        assert len(await c.walk("1.3.6.1.2.1.2.2.1.2", limit=1)) == 2  # stops after the batch that reached the limit
        await c.set("1.3.6.1.2.1.1.5.0", OctetString("x"))
        answers["get"] = ("requestTimedOut", None, 0, [])
        with pytest.raises(ExternalServiceError, match="requestTimedOut"):
            await c.get("1.3.6.1.2.1.1.5.0")
        answers["get"] = (None, Status("noSuchName"), 0, [])
        with pytest.raises(ExternalServiceError, match="noSuchName"):
            await c.get("1.3.6.1.2.1.1.5.0")
        answers["set"] = (None, Status("notWritable"), 0, [])
        with pytest.raises(ExternalServiceError, match="write access"):
            await c.set("1.3.6.1.2.1.1.5.0", OctetString("x"))
    asyncio.run(go())
    c.close()


def test_client_walk_errors(monkeypatch):
    async def target(*a, **kw):
        return "udp-target"
    monkeypatch.setattr(snmp.UdpTransportTarget, "create", target)
    mode = {"v": "error-first"}

    async def bulk_walk_cmd(*a, **kw):
        if mode["v"] == "error-later":
            yield None, Status(""), 0, [(ObjectIdentifier("1.3.6.1.2.1.1.9.1"), OctetString("a"))]
        if mode["v"] == "status":
            yield None, Status("genErr"), 0, []
        yield "requestTimedOut", None, 0, []
    monkeypatch.setattr(snmp, "bulk_walk_cmd", bulk_walk_cmd)
    c = SnmpClient("192.0.2.2", 161, Credentials())
    with pytest.raises(ExternalServiceError, match="requestTimedOut"):
        asyncio.run(c.walk("1.3.6.1.2.1.1.9"))
    mode["v"] = "error-later"
    assert list(asyncio.run(c.walk("1.3.6.1.2.1.1.9"))) == ["1"]  # what came before the error is kept
    mode["v"] = "status"
    assert asyncio.run(c.walk("1.3.6.1.2.1.1.9")) == {}


def test_client_v3_users():
    full = SnmpClient("h", 161, Credentials(snmp_version="3", snmp_user="u", snmp_auth_password="authpass12",
                                            snmp_auth_protocol="sha256", snmp_priv_password="privpass12", snmp_priv_protocol="aes256"))
    assert full._auth.authentication_protocol == snmp.usmHMAC192SHA256AuthProtocol and full._auth.privacy_protocol == snmp.usmAesCfb256Protocol
    bare = SnmpClient("h", 161, Credentials(snmp_version="3", snmp_user="u"))
    assert bare._auth.authentication_protocol == snmp.usmNoAuthProtocol and bare._auth.privacy_protocol == snmp.usmNoPrivProtocol
    full.close()
    bare.close()
