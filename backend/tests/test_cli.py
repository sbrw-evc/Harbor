"""SSH CLI drivers (cli.py): the profiles and their templates, Cisco-like polling and Junos, against a fake shell."""
import asyncio
import json

import pytest
from test_drivers import CDP, IOS_INTERFACES, IOS_VERSION, LLDP

from app.adapters.devices.base import Credentials, Target, UnsupportedAction
from app.adapters.devices.cli import PROFILES, CliDriver, JunosDriver
from app.domain.errors import ExternalServiceError

MAC_TABLE = """          Mac Address Table
-------------------------------------------
Vlan    Mac Address       Type        Ports
----    -----------       --------    -----
  10    aabb.cc00.0001    DYNAMIC     Gi1/0/7
  10    000c.4200.0001    DYNAMIC     Gi1/0/1
"""
ARP = """Protocol  Address          Age (min)  Hardware Addr   Type   Interface
Internet  192.168.0.50            1   aabb.cc00.0001  ARPA   Vlan1
"""


class FakeSsh:
    """shell(): one answer per command (by exact command, else ""); run(): by command prefix. Records every call."""

    def __init__(self, shell: dict | None = None, run: dict | None = None, fail: Exception | None = None):
        self.shell_answers, self.run_answers, self.fail = shell or {}, run or {}, fail
        self.sessions: list[list[str]] = []
        self.answers: list[dict | None] = []
        self.host_key = "ssh-rsa AAAA"
        self.closed = False

    async def shell(self, commands, timeout=60, enable="", answers=None):
        self.sessions.append(list(commands))
        self.answers.append(answers)
        if self.fail:
            raise self.fail
        return [self.shell_answers.get(c, "") for c in commands]

    async def run(self, cmd, timeout=60):
        self.sessions.append([cmd])
        for prefix, out in self.run_answers.items():
            if cmd.startswith(prefix):
                return out
        return ""

    async def close(self):
        self.closed = True


def cli(profile="cisco_ios", **ssh):
    d = CliDriver(Target("192.0.2.3", transport="ssh"), Credentials(username="admin", password="x"), "", profile)
    d._ssh = FakeSsh(**ssh)
    return d


def run(coro):
    return asyncio.run(coro)


def test_ios_collect():
    d = cli(shell={"show version": IOS_VERSION, "show interfaces": IOS_INTERFACES, "show lldp neighbors detail": LLDP,
                   "show cdp neighbors detail": CDP, "show ip arp": ARP, "show arp": "% Invalid input detected",
                   "show mac address-table": MAC_TABLE})
    snap = run(d.collect())
    assert d._ssh.sessions[0][0] == "terminal length 0" and d.learned_host_key == "ssh-rsa AAAA"
    assert (snap.facts.hostname, snap.facts.vendor, snap.facts.model) == ("core-sw", "Cisco", "WS-C2960X-48TS-L")
    assert [i.name for i in snap.interfaces][:2] == ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2"]
    assert {n.remote_name for n in snap.neighbors} == {"gw", "ap-hall.local"}
    macs = [(e.mac, e.source) for e in snap.endpoints]
    assert ("aa:bb:cc:00:00:01", "arp") in macs and ("aa:bb:cc:00:00:01", "bridge") in macs
    assert ("00:0c:42:00:00:01", "bridge") not in macs  # learned on the LLDP uplink
    assert "reboot" in d.capabilities and "flow_export" in d.capabilities


def test_collect_falls_back_and_takes_the_mac_of_an_interface():
    d = cli("cisco_nxos", shell={"show version": "", "show interface": "% Invalid command",
                                 "show interface brief": IOS_INTERFACES})
    snap = run(d.collect())
    assert snap.facts.mac == snap.interfaces[0].mac != "" and snap.neighbors == [] and snap.endpoints == []


SAMPLES = {"hostname": {"name": "sw-1"}, "interface": {"name": "Gi1/0/2", "enabled": False, "description": "spare"},
           "dns": {"servers": ["1.1.1.1", "9.9.9.9"]}, "ntp": {"servers": ["pool.ntp.org", "time.google.com"]},
           "flow_export": {"protocol": "sflow", "enabled": True, "collector": "10.0.0.5", "port": 6343}}


@pytest.mark.parametrize("key", sorted(PROFILES))
def test_every_template_of_every_profile(key):
    profile = PROFILES[key]
    for action in profile.templates:
        d = cli(key)
        p = dict(SAMPLES[action])
        if action == "flow_export" and key == "cisco_ios":
            p.update(protocol="netflow9", interfaces=["Gi1/0/1"])
        assert run(d.apply(action, p)) == "Done", (key, action)
        sent = d._ssh.sessions[-1]
        assert sent[:len(profile.setup) + len(profile.enter)] == list(profile.setup) + list(profile.enter)
        assert sent[-len(profile.save):] == list(profile.save) and d._ssh.answers[-1] == profile.answers


def test_template_variants():
    from app.adapters.devices.cli import _aruba_iface, _cisco_iface, _comware_iface, _ios_flow
    assert _cisco_iface({"name": "Gi1", "enabled": True, "description": ""}) == ["interface Gi1", "no shutdown", "no description", "exit"]
    assert _aruba_iface({"name": "1", "enabled": True, "description": 'a "b"'}) == ["interface 1", "enable", 'name "a b"', "exit"]
    assert _aruba_iface({"name": "1", "description": ""}) == ["interface 1", "no name", "exit"]
    assert _comware_iface({"name": "GE1", "enabled": True, "description": ""}) == ["interface GE1", "undo shutdown", "undo description", "quit"]
    assert _ios_flow({"enabled": False}) == ["no flow exporter NC-EXPORT"]
    with pytest.raises(ValueError):
        _ios_flow({"enabled": True, "protocol": "sflow"})
    for key in ("aruba_os", "hp_comware"):
        assert PROFILES[key].templates["flow_export"]({"protocol": "sflow", "enabled": False})[0].startswith(("no sflow", "undo sflow"))


def test_apply_errors_command_reboot_backup():
    d = cli(shell={"hostname sw-1": "% Invalid input detected at '^' marker.", "show clock": "12:00", "show running-config": "!cfg"})

    async def go():
        with pytest.raises(ExternalServiceError, match="Invalid input"):
            await d.apply("hostname", {"name": "sw-1"})
        with pytest.raises(UnsupportedAction):
            await d.apply("flow_export", {"protocol": "sflow", "enabled": True, "collector": "x", "port": 1})
        with pytest.raises(UnsupportedAction):
            await d.apply("endpoint_block", {})
        assert await d.apply("command", {"command": "show clock"}) == "12:00"
        assert await d.backup() == "!cfg"
        assert await d.apply("reboot", {}) == "Reboot started"
        assert d._ssh.sessions[-1][-2:] == ["write memory", "reload"]
        await d.close()
        assert d._ssh.closed
    run(go())
    dropped = cli(fail=ExternalServiceError("closed"))
    assert run(dropped.apply("reboot", {})) == "Reboot started"
    assert "reboot" in CliDriver(Target("h"), Credentials(), "", "huawei_vrp").capabilities


def junos_json(obj):
    return "\n" + json.dumps(obj)


def wrap(v):
    return [{"data": v}]


JUNOS = {
    "show system information": junos_json({"system-information": [{"host-name": wrap("ex1"), "hardware-model": wrap("ex2300-24t"),
                                                                    "os-version": wrap("21.4R3"), "serial-number": wrap("JW123")}]}),
    "show system uptime": junos_json({"multi-routing-engine-results": [{"multi-routing-engine-item": [{"system-uptime-information": [
        {"uptime-information": [{"up-time": wrap("2 days, 3:04:05")}]}]}]}]}),
    "show interfaces extensive": junos_json({"interface-information": [{"physical-interface": [
        {"name": wrap("ge-0/0/0"), "description": wrap("uplink"), "current-physical-address": wrap("2c:6b:f5:00:00:01"),
         "admin-status": wrap("up"), "oper-status": wrap("up"), "mtu": wrap("1514"), "speed": wrap("1000mbps"),
         "traffic-statistics": [{"input-bytes": wrap("100"), "output-bytes": wrap("200")}],
         "logical-interface": [{"address-family": [{"address-family-name": wrap("inet"), "interface-address": [
             {"ifa-local": wrap("10.0.0.1"), "ifa-destination": wrap("10.0.0/24")}]}]}]},
        {"name": wrap("xe-0/1/0"), "speed": wrap("10Gbps"), "admin-status": wrap("down")}]}]}),
    "show lldp neighbors": junos_json({"lldp-neighbors-information": [{"lldp-neighbor-information": [
        {"lldp-local-port-id": wrap("ge-0/0/0"), "lldp-remote-system-name": wrap("gw"),
         "lldp-remote-chassis-id": wrap("00:0c:42:00:00:01"), "lldp-remote-port-description": wrap("ether2")}]}]}),
    "show arp no-resolve": junos_json({"arp-table-information": [{"arp-table-entry": [
        {"mac-address": wrap("aa:bb:cc:00:00:01"), "ip-address": wrap("10.0.0.50"), "interface-name": wrap("irb.0")},
        {"mac-address": wrap("")}]}]}),
    "show ethernet-switching table": junos_json({"l2ng-l2ald-rtb-macdb": [{"l2ng-l2ald-mac-entry-vlan": [{"l2ng-mac-entry": [
        {"l2ng-l2-mac-address": wrap("aa:bb:cc:00:00:02"), "l2ng-l2-mac-logical-interface": wrap("ge-0/0/5.0")},
        {"l2ng-l2-mac-address": wrap("00:0c:42:00:00:01"), "l2ng-l2-mac-logical-interface": wrap("ge-0/0/0.0")}]}]}]}),
    "show configuration": "set system host-name ex1",
    "show chassis": "Chassis JN123",
}


def junos(**ssh):
    d = JunosDriver(Target("192.0.2.4", transport="ssh"), Credentials(username="admin", password="x"))
    d._ssh = FakeSsh(**ssh)
    return d


def test_junos_collect():
    snap = run(junos(run=JUNOS).collect())
    f = snap.facts
    assert (f.hostname, f.model, f.version, f.serial, f.uptime_seconds) == ("ex1", "ex2300-24t", "21.4R3", "JW123", 2 * 86400 + 3 * 3600 + 4 * 60 + 5)
    ge, xe = snap.interfaces
    assert (ge.speed_mbps, ge.rx_bytes, ge.addresses, ge.oper_up) == (1000, 100, ["10.0.0.1/24"], True)
    assert (xe.speed_mbps, xe.admin_up, xe.rx_bytes) == (10000, False, None)
    assert [(n.local_interface, n.remote_name, n.remote_interface) for n in snap.neighbors] == [("ge-0/0/0", "gw", "ether2")]
    assert [(e.mac, e.source, e.interface) for e in snap.endpoints] == [
        ("aa:bb:cc:00:00:01", "arp", "irb.0"), ("aa:bb:cc:00:00:02", "bridge", "ge-0/0/5")]


def test_junos_without_data():
    snap = run(junos(run={"show system information": "error: not json {"}).collect())
    assert snap.facts.hostname == "" and snap.interfaces == [] and snap.endpoints == []
    assert JunosDriver._d([], "x", default="-") == "-" and JunosDriver._d({"a": 5}, "a", "b", default="-") == "-"


def test_junos_apply():
    d = junos(run=JUNOS)

    async def go():
        for action, p, line in [("hostname", {"name": "ex2"}, "set system host-name ex2"),
                                ("interface", {"name": "ge-0/0/1", "enabled": True, "description": "ap"}, "delete interfaces ge-0/0/1 disable"),
                                ("dns", {"servers": ["1.1.1.1"]}, "delete system name-server"),
                                ("ntp", {"servers": ["pool.ntp.org"]}, "set system ntp server pool.ntp.org")]:
            assert await d.apply(action, p) == "Done"
            s = d._ssh.sessions[-1]
            assert s[0] == "configure private" and s[1] == line and s[-1] == "commit and-quit"
        assert await d.apply("reboot", {}) == "Reboot started"
        assert await d.apply("command", {"command": "show chassis"}) == "Chassis JN123"
        assert await d.backup() == "set system host-name ex1"
        with pytest.raises(UnsupportedAction):
            await d.apply("flow_export", {})
        await d.close()
    run(go())
    bad = junos(shell={"set system host-name x": "syntax error, expecting <command>"})
    with pytest.raises(ExternalServiceError, match="Junos: syntax error"):
        run(bad.apply("hostname", {"name": "x"}))
    dropped = junos(fail=ExternalServiceError("closed"))
    assert run(dropped.apply("reboot", {})) == "Reboot started"
