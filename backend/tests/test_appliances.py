"""Firewalls and routers with an HTTP API or a Linux shell (appliances.py) against fake devices."""
import asyncio
import json

import httpx
import pytest
from httpfake import FakeApi

from app.adapters.devices.appliances import (FortigateDriver, KeeneticDriver, LinuxDriver, OpenWrtDriver,
                                             OpnsenseDriver, PfsenseDriver, _mask_bits, first_json_value,
                                             parse_linux, split_sections)
from app.adapters.devices.base import Credentials, Target, UnsupportedAction
from app.domain.errors import ExternalServiceError


def run(coro):
    return asyncio.run(coro)


def test_helpers():
    assert _mask_bits("255.255.255.0") == 24 and _mask_bits(16) == 16 and _mask_bits("x") == 32
    assert split_sections("#A\none\n#B\ntwo\nthree") == {"A": "one", "B": "two\nthree"}
    assert first_json_value('{"ssid": "Home", "x": 1}', "ssid") == "Home" and first_json_value("{}", "ssid") == ""


def test_opnsense_collect_apply_backup():
    api = FakeApi({
        "/api/diagnostics/system/systemInformation": {"name": "fw.home", "versions": ["OPNsense 24.7.1"]},
        "/api/diagnostics/system/systemTime": {"uptime": "3 days, 4:05:06"},
        "/api/diagnostics/system/systemResources": {"memory": {"total": "1000", "used": "250"}},
        "/api/diagnostics/interface/getInterfaceNames": {"igb0": "LAN"},
        "/api/diagnostics/interface/getInterfaceConfig": {
            "igb0": {"macaddr": "00:11:22:33:44:55", "flags": ["up"], "status": "active", "mtu": "1500",
                     "ipv4": [{"ipaddr": "192.168.1.1", "subnetbits": 24}]},
            "junk": "not a dict"},
        "/api/diagnostics/traffic/interface": {"interfaces": {"lan": {"device": "igb0", "name": "LAN",
                                                                      "bytes received": "10", "bytes transmitted": "20"}}},
        "/api/diagnostics/interface/getArp": [{"mac": "aa:bb:cc:00:00:01", "ip": "192.168.1.10", "hostname": "pc",
                                               "intf": "igb0"}, {"mac": "", "ip": "192.168.1.11"}],
        "/api/dhcpv4/leases/searchLease": {"rows": [{"mac": "aa:bb:cc:00:00:02", "address": "192.168.1.12",
                                                     "hostname": "tv", "if_descr": "LAN"}]},
        "/api/kea/leases4/search": {"rows": [{"hwaddr": "aa:bb:cc:00:00:03", "address": "192.168.1.13"}]},
        ("POST", "/api/core/system/reboot"): {},
        ("POST", "/api/diagnostics/netflow/setconfig"): {"result": "saved"},
        ("POST", "/api/diagnostics/netflow/reconfigure"): {"status": "ok"},
        "/api/core/backup/download/this": "<opnsense/>",
    })
    d = api.attach(OpnsenseDriver(Target("192.0.2.1"), Credentials(api_key="k", api_secret="s"), "Deciso"))

    async def go():
        snap = await d.collect()
        assert (snap.facts.hostname, snap.facts.version, snap.facts.memory_percent) == ("fw", "24.7.1", 25.0)
        assert snap.facts.uptime_seconds == 3 * 86400 + 4 * 3600 + 5 * 60 + 6
        i = snap.interfaces[0]
        assert (i.name, i.description, i.addresses, i.rx_bytes, i.oper_up) == ("igb0", "LAN", ["192.168.1.1/24"], 10, True)
        assert [e.source for e in snap.endpoints] == ["arp", "dhcp", "dhcp"]
        assert await d.apply("reboot", {}) == "Reboot started"
        msg = await d.apply("flow_export", {"protocol": "netflow9", "enabled": True, "collector": "10.0.0.5", "port": 2055})
        assert msg == "NetFlow enabled on interfaces lan"
        setconfig = next(b for m, p, b in api.sent if p.endswith("/setconfig"))
        assert setconfig["netflow"]["capture"] == {"interfaces": "lan", "egress_only": "", "version": "v9",
                                                   "targets": "10.0.0.5:2055"}
        with pytest.raises(UnsupportedAction):
            await d.apply("flow_export", {"protocol": "sflow", "enabled": True, "collector": "x", "port": 1})
        with pytest.raises(UnsupportedAction):
            await d.apply("hostname", {"name": "x"})
        assert await d.backup() == "<opnsense/>"
        await d.close()
    run(go())


def test_http_errors_and_optional_answers():
    api = FakeApi({"/denied": httpx.Response(403), "/broken": httpx.Response(500, text="boom"),
                   "/text": "not json", ("POST", "/plain"): "ok"})
    d = api.attach(OpnsenseDriver(Target("192.0.2.1"), Credentials(username="u", password="p")))

    async def go():
        assert await d._get("/denied", optional=True) is None
        assert await d._get("/text", optional=True) is None
        with pytest.raises(ExternalServiceError, match="denied access"):
            await d._get("/denied")
        with pytest.raises(ExternalServiceError, match="returned 500"):
            await d._get("/broken")
        with pytest.raises(ExternalServiceError, match="not JSON"):
            await d._get("/text")
        assert await d._send("POST", "/plain") == "ok"
        with pytest.raises(ExternalServiceError):
            await d._send("POST", "/broken")
        with pytest.raises(ExternalServiceError):
            await d.backup()  # /api/core/backup/download/this answers 404

    def unreachable(req):
        raise httpx.ConnectError("refused")

    async def down():
        d2 = FakeApi({"/x": unreachable}).attach(OpnsenseDriver(Target("192.0.2.9"), Credentials()))
        assert await d2._get("/x", optional=True) is None
        with pytest.raises(ExternalServiceError, match="unreachable"):
            await d2._get("/x")
    run(go())
    run(down())


def test_pfsense():
    api = FakeApi({
        "/api/v2/status/system": {"data": {"platform": "Netgate 4100", "serial": "S1", "uptime": "2 days 01:00:00",
                                           "cpu_usage": 3.5, "mem_usage": 40}},
        "/api/v2/system/version": {"data": {"version": "2.7.2"}},
        "/api/v2/system/hostname": {"data": {"hostname": "pf"}},
        "/api/v2/status/interfaces": {"data": [{"hwif": "igc0", "descr": "WAN", "macaddr": "00:08:a2:00:00:01",
                                                "status": "up", "ipaddr": "203.0.113.2", "subnet": 29,
                                                "inbytes": 5, "outbytes": 6}]},
        "/api/v2/diagnostics/arp_table": {"data": [{"mac_address": "aa:bb:cc:00:00:01", "ip_address": "192.168.1.5",
                                                    "interface": "igc1"}]},
        "/api/v2/status/dhcp_server/leases": {"data": [{"mac": "aa:bb:cc:00:00:02", "ip": "192.168.1.6",
                                                        "hostname": "nas", "if": "lan"}]},
        ("PATCH", "/api/v2/system/hostname"): {"data": {}},
        ("PATCH", "/api/v2/system/dns"): {"data": {}},
        ("POST", "/api/v2/diagnostics/reboot"): httpx.Response(502),
        ("POST", "/api/v2/diagnostics/command_prompt"): {"data": {"output": "<pfsense/>"}},
    })
    d = api.attach(PfsenseDriver(Target("192.0.2.1"), Credentials(api_key="key")))
    assert d._client_kw() == {"headers": {"X-API-Key": "key"}}
    assert PfsenseDriver(Target("192.0.2.1"), Credentials(username="u", password="p"))._client_kw() == {"auth": ("u", "p")}

    async def go():
        snap = await d.collect()
        assert (snap.facts.hostname, snap.facts.model, snap.facts.version, snap.facts.cpu_percent) == ("pf", "Netgate 4100", "2.7.2", 3.5)
        assert snap.interfaces[0].addresses == ["203.0.113.2/29"] and snap.interfaces[0].oper_up
        assert [e.source for e in snap.endpoints] == ["arp", "dhcp"]
        assert await d.apply("hostname", {"name": "pf2"}) == "Done"
        assert await d.apply("dns", {"servers": ["1.1.1.1"]}) == "Done"
        assert await d.apply("reboot", {}) == "Reboot started"  # the connection drops while it reboots
        assert await d.backup() == "<pfsense/>"
        with pytest.raises(UnsupportedAction):
            await d.apply("ntp", {"servers": []})
    run(go())


def test_fortigate():
    calls = {"netflow": 0}

    def netflow(req):
        calls["netflow"] += 1
        body = json.loads(req.content)
        # FortiOS 7.0 has a list of collectors, 6.4 a single one: the first form is refused there.
        return httpx.Response(500 if "collectors" in body and body["collectors"] else 200, json={})

    api = FakeApi({
        "/api/v2/monitor/system/status": {"version": "v7.2.8", "serial": "FG100F", "results": {"hostname": "fgt",
                                                                                              "model_name": "FortiGate 100F"}},
        "/api/v2/monitor/system/performance/status": {"results": {"uptime": 3600, "mem": {"used": 30, "total": 120}}},
        "/api/v2/monitor/system/interface": {"results": {
            "port1": {"ip": "192.168.1.99", "mask": 24, "mac": "00:09:0f:00:00:01", "link": True, "speed": 1000,
                      "rx_bytes": 1, "tx_bytes": 2, "alias": "LAN"},
            "port2": {"ip": "0.0.0.0", "mask": 0, "link": False}}},
        "/api/v2/monitor/network/lldp/neighbors": {"results": [{"port_name": "port1", "system_name": "sw",
                                                                "chassis_id": "aa:00:00:00:00:01", "port_id": "Gi0/1",
                                                                "addresses": [{"address": "192.168.1.2"}]}]},
        "/api/v2/monitor/network/arp": {"results": [{"mac": "aa:bb:cc:00:00:01", "ip": "192.168.1.5",
                                                     "interface": "port1"}]},
        "/api/v2/monitor/system/dhcp": {"results": [{"mac": "aa:bb:cc:00:00:02", "ip": "192.168.1.6", "hostname": "nas",
                                                     "interface": "port1"}]},
        ("PUT", "/api/v2/cmdb/system/global"): {},
        ("PUT", "/api/v2/cmdb/system/interface/port1"): {},
        ("PUT", "/api/v2/cmdb/system/dns"): {},
        ("PUT", "/api/v2/cmdb/system/ntp"): {},
        ("PUT", "/api/v2/cmdb/system/netflow"): netflow,
        ("PUT", "/api/v2/cmdb/system/sflow"): {},
        ("POST", "/api/v2/monitor/system/os/reboot"): httpx.Response(500),
        "/api/v2/monitor/system/config/backup": "#config-version=FG100F",
    })
    d = api.attach(FortigateDriver(Target("192.0.2.1"), Credentials(api_key="tok")))
    assert d._client_kw() == {"headers": {"Authorization": "Bearer tok"}}

    async def go():
        snap = await d.collect()
        assert (snap.facts.hostname, snap.facts.model, snap.facts.memory_percent, snap.facts.uptime_seconds) == ("fgt", "FortiGate 100F", 25.0, 3600)
        assert snap.interfaces[0].addresses == ["192.168.1.99/24"] and snap.interfaces[1].addresses == []
        assert snap.neighbors[0].remote_ip == "192.168.1.2" and len(snap.endpoints) == 2
        assert await d.apply("hostname", {"name": "fgt2"}) == "Done"
        assert await d.apply("interface", {"name": "port1", "enabled": False, "description": "x"}) == "Done"
        assert ("PUT", "/api/v2/cmdb/system/interface/port1", {"status": "down", "description": "x"}) in api.sent
        await d.apply("dns", {"servers": ["1.1.1.1"]})
        assert ("PUT", "/api/v2/cmdb/system/dns", {"primary": "1.1.1.1", "secondary": ""}) in api.sent
        await d.apply("ntp", {"servers": ["pool.ntp.org", "time.google.com"]})
        assert "Collector set" in await d.apply("flow_export", {"protocol": "netflow9", "enabled": True,
                                                                "collector": "10.0.0.5", "port": 2055})
        assert calls["netflow"] == 2
        await d.apply("flow_export", {"protocol": "sflow", "enabled": False, "collector": "", "port": 0})
        assert ("PUT", "/api/v2/cmdb/system/sflow", {"collectors": []}) in api.sent
        assert await d.apply("reboot", {}) == "Reboot started"
        assert await d.backup() == "#config-version=FG100F"
        with pytest.raises(UnsupportedAction):
            await d.apply("endpoint_block", {})
    run(go())


def keenetic(routes):
    """A Keenetic that accepts the session cookie it hands out."""
    def auth(req):
        return httpx.Response(200, headers={"Set-Cookie": "session=1; Path=/"})
    return FakeApi({("GET", "/auth"): auth, **routes})


def test_keenetic_actions_and_backup():
    api = keenetic({
        ("POST", "/rci/"): lambda req: httpx.Response(200, json=[
            {"parse": {"status": [{"status": "message"}], "message": ["! running-config", "system name home"]}}]),
    })
    d = api.attach(KeeneticDriver(Target("192.0.2.5", transport="http"), Credentials(username="admin", password="pw")),
                   "http://192.0.2.5")

    async def go():
        await d._login()  # the web interface answers 200: the session is open already
        for action, p, first in [("hostname", {"name": "home"}, "system name home"),
                                 ("interface", {"name": "Gi1", "enabled": True, "description": "uplink"}, "interface Gi1 up"),
                                 ("dns", {"servers": ["1.1.1.1"]}, "ip name-server 1.1.1.1"),
                                 ("ntp", {"servers": ["pool.ntp.org"]}, "ntp server pool.ntp.org"),
                                 ("endpoint_block", {"mac": "aa:bb:cc:00:00:01", "blocked": False},
                                  "ip hotspot host aa:bb:cc:00:00:01 permit")]:
            assert await d.apply(action, p) == "Done"
            sent = api.sent[-1][2]
            assert sent[0] == {"parse": first} and sent[-1] == {"parse": "system configuration save"}
        assert await d.apply("reboot", {}) == "Reboot started"
        assert '"parse"' in await d.apply("command", {"command": "show version"})
        assert await d.backup() == "! running-config\nsystem name home"
        with pytest.raises(UnsupportedAction):
            await d.apply("flow_export", {})
    run(go())


def test_keenetic_errors():
    no_challenge = FakeApi({("GET", "/auth"): httpx.Response(401)})
    d = no_challenge.attach(KeeneticDriver(Target("192.0.2.5"), Credentials(username="a", password="b")))
    with pytest.raises(ExternalServiceError, match="not a Keenetic"):
        run(d._login())
    wrong = FakeApi({("GET", "/auth"): httpx.Response(401, headers={"X-NDM-Realm": "R", "X-NDM-Challenge": "C"}),
                     ("POST", "/auth"): httpx.Response(401)})
    d = wrong.attach(KeeneticDriver(Target("192.0.2.5"), Credentials(username="a", password="b")))
    with pytest.raises(ExternalServiceError, match="wrong login"):
        run(d._login())
    failing = keenetic({("POST", "/rci/"): [{"parse": {"status": [{"status": "error", "message": "bad command"}]}}]})
    d = failing.attach(KeeneticDriver(Target("192.0.2.5"), Credentials()))
    with pytest.raises(ExternalServiceError, match="bad command"):
        run(d.apply("hostname", {"name": "x"}))


LINUX = """#HOST
nas
#OS
NAME="Debian GNU/Linux"
VERSION_ID="12"
#UPTIME
12345.67 1000.00
#MODEL
ProLiant MicroServer
#MEM
MemTotal:        1000 kB
MemAvailable:     400 kB
#LOAD
0.50 0.40 0.30 1/100 1234
2
#LINK
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000\\    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq state UP mode DEFAULT group default qlen 1000\\    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff
#ADDR
2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0
#DEV
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 10 1 0 0 0 0 0 0 10 1 0 0 0 0 0 0
  eth0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0
#SPEED
/sys/class/net/eth0/speed 1000
/sys/class/net/lo/speed -1
#ARP
IP address       HW type     Flags       HW address            Mask     Device
192.168.1.1      0x1         0x2         00:0c:42:00:00:01     *        eth0
192.168.1.9      0x1         0x0         00:00:00:00:00:00     *        eth0
#LEASES
1700000000 aa:bb:cc:00:00:01 192.168.1.30 phone *
1700000000 aa:bb:cc:00:00:02 192.168.1.31 * *
#LLDP
{"lldp": {"interface": [{"eth0": {"chassis": {"sw1": {"id": {"value": "aa:00:00:00:00:09"}, "mgmt-ip": ["192.168.1.2"],
 "descr": "Switch"}}, "port": {"id": {"value": "Gi0/3"}}}}]}}
"""


def test_parse_linux_full():
    snap = parse_linux(LINUX)
    f = snap.facts
    assert (f.hostname, f.os, f.version, f.model, f.uptime_seconds) == ("nas", "Debian GNU/Linux", "12", "ProLiant MicroServer", 12345)
    assert (f.memory_percent, f.cpu_percent, f.mac) == (60.0, 25.0, "52:54:00:12:34:56")
    eth = snap.interfaces[0]
    assert (eth.name, eth.speed_mbps, eth.rx_bytes, eth.tx_bytes, eth.addresses) == ("eth0", 1000, 1000, 2000, ["192.168.1.20/24"])
    assert [(e.ip, e.source, e.hostname) for e in snap.endpoints] == [
        ("192.168.1.1", "arp", ""), ("192.168.1.30", "dhcp", "phone"), ("192.168.1.31", "dhcp", "")]
    n = snap.neighbors[0]
    assert (n.remote_name, n.remote_mac, n.remote_ip, n.remote_interface) == ("sw1", "aa:00:00:00:00:09", "192.168.1.2", "Gi0/3")


def test_parse_linux_without_ip_link():
    snap = parse_linux("#HOST\nbox\n#DEV\nh1\nh2\n  eth1: 5 0 0 0 0 0 0 0 6 0 0 0 0 0 0 0\n#LLDP\nnot json")
    assert [(i.name, i.rx_bytes, i.tx_bytes) for i in snap.interfaces] == [("eth1", 5, 6)] and snap.neighbors == []


class Shell:
    """Answers the commands of a Linux driver: the first matching prefix wins; the commands are recorded."""

    def __init__(self, answers: dict[str, str]):
        self.answers = answers
        self.ran: list[str] = []

    async def __call__(self, cmd: str, timeout: float = 60) -> str:
        self.ran.append(cmd)
        for prefix, out in self.answers.items():
            if cmd.startswith(prefix):
                if isinstance(out, Exception):
                    raise out
                return out
        return ""


def test_linux_driver():
    d = LinuxDriver(Target("192.0.2.20", transport="ssh"), Credentials(username="root", password="x"), "HPE")
    d.run = Shell({"echo '#HOST'": LINUX, "(sleep 1; reboot)": ExternalServiceError("closed")})

    async def go():
        snap = await d.collect()
        assert snap.facts.vendor == "HPE"
        assert await d.apply("hostname", {"name": "nas2"}) == "Done"
        assert await d.apply("interface", {"name": "eth0 (LAN)", "enabled": False}) == "Done"
        assert d.run.ran[-1] == "ip link set dev eth0 down"
        with pytest.raises(UnsupportedAction):
            await d.apply("interface", {"name": "eth0", "description": "x"})
        assert await d.apply("reboot", {}) == "Reboot started"
        assert await d.apply("command", {"command": "uptime"}) == ""
        with pytest.raises(UnsupportedAction):
            await d.apply("dns", {"servers": []})
    run(go())


def test_linux_run_keeps_the_learned_host_key():
    d = LinuxDriver(Target("192.0.2.20", transport="ssh"), Credentials())

    class Ssh:
        host_key = "ssh-ed25519 AAAA"

        async def run(self, cmd, timeout=60):
            return "ok"

        async def close(self):
            self.closed = True
    d._ssh = Ssh()
    assert run(d.run("true")) == "ok" and d.learned_host_key == "ssh-ed25519 AAAA"
    run(d.close())
    assert d._ssh.closed


HOSTAPD = """#hostapd.wlan0
{"freq": 2412, "clients": {"aa:bb:cc:00:00:05": {"signal": -55}, "aa:bb:cc:00:00:06": {}}}
"ssid": "Home",
#hostapd.wlan1
not json
"""


def test_openwrt_driver():
    d = OpenWrtDriver(Target("192.0.2.21", transport="ssh"), Credentials(username="root"))
    d.run = Shell({"echo '#HOST'": LINUX, "ubus call system board": json.dumps(
        {"model": "GL.iNet GL-MT6000", "hostname": "glinet", "release": {"version": "23.05.3"}}),
        "for i in $(ubus list": HOSTAPD, "[ -f /etc/config/softflowd ]": "", "uci export": "package system"})

    async def go():
        snap = await d.collect()
        assert (snap.facts.os, snap.facts.model, snap.facts.version, snap.facts.hostname) == ("OpenWrt", "GL.iNet GL-MT6000", "23.05.3", "glinet")
        wifi = [e for e in snap.endpoints if e.source == "wifi"]
        assert [(e.mac, e.ssid, e.signal, e.interface) for e in wifi] == [
            ("aa:bb:cc:00:00:05", "Home", -55, "wlan0"), ("aa:bb:cc:00:00:06", "Home", None, "wlan0")]
        for action, p in [("hostname", {"name": "gw"}), ("dns", {"servers": ["1.1.1.1", "9.9.9.9"]}),
                          ("ntp", {"servers": ["pool.ntp.org"]})]:
            assert await d.apply(action, p) == "Done"
        assert "uci add_list dhcp.@dnsmasq[0].server='9.9.9.9'" in d.run.ran[-2]
        msg = await d.apply("flow_export", {"protocol": "ipfix", "enabled": True, "collector": "10.0.0.5", "port": 2055})
        assert msg == "softflowd enabled: 10.0.0.5:2055" and "export_version='10'" in d.run.ran[-1]
        with pytest.raises(UnsupportedAction):
            await d.apply("flow_export", {"protocol": "sflow", "enabled": True, "collector": "x", "port": 1})
        assert await d.apply("backup", {}) == "package system"
        assert await d.apply("command", {"command": "uptime"}) == ""  # the Linux actions stay
    run(go())


def test_openwrt_without_softflowd_and_board():
    d = OpenWrtDriver(Target("192.0.2.21", transport="ssh"), Credentials())
    d.run = Shell({"echo '#HOST'": LINUX, "ubus call system board": "not json", "[ -f /etc/config/softflowd ]": "NOPKG"})

    async def go():
        snap = await d.collect()
        assert snap.facts.model == "ProLiant MicroServer"  # what the Linux part found
        with pytest.raises(ExternalServiceError, match="softflowd is not installed"):
            await d.apply("flow_export", {"protocol": "netflow5", "enabled": False, "collector": "x", "port": 1})
    run(go())
