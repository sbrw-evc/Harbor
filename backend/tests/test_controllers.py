"""Controllers of many devices (controllers.py): Omada, UniFi, Meraki and Redfish against fake APIs."""
import asyncio

import httpx
import pytest
from httpfake import FakeApi

from app.adapters.devices.base import Credentials, Target, UnsupportedAction
from app.adapters.devices.controllers import MerakiDriver, OmadaDriver, RedfishDriver, UnifiDriver, _bits
from app.domain.errors import ExternalServiceError

CID = "c0ffee"
SITE = f"/openapi/v1/{CID}/sites/s2"


def omada(over=None):
    routes = {
        "/api/info": {"result": {"omadacId": CID, "name": "Office", "controllerVer": "5.14.26"}},
        ("POST", "/openapi/authorize/token"): {"result": {"accessToken": "AT"}},
        f"/openapi/v1/{CID}/sites": {"errorCode": 0, "result": {"data": [{"siteId": "s1", "name": "Default"},
                                                                        {"siteId": "s2", "name": "Branch"}]}},
        f"{SITE}/devices": {"errorCode": 0, "result": {"data": [
            {"mac": "AA-00-00-00-00-01", "name": "ap-hall", "model": "EAP650", "type": "AP", "ip": "192.168.1.30",
             "status": 14, "firmwareVersion": "1.0", "uplinkDeviceMac": "AA-00-00-00-00-02", "uplinkDevicePort": "8",
             "clientNum": 3},
            {"mac": "AA-00-00-00-00-02", "name": "sw", "showModel": "SG2210", "type": "switch", "statusCategory": "1"},
            {"mac": "", "name": "broken"}]}},
        f"{SITE}/clients": {"errorCode": 0, "result": {"data": [
            {"mac": "BB-00-00-00-00-01", "ip": "192.168.1.50", "name": "phone", "wireless": True, "ssid": "Office",
             "rssi": -60, "apMac": "AA-00-00-00-00-01", "vid": 10},
            {"mac": "BB-00-00-00-00-02", "hostName": "nas", "wireless": False, "switchMac": "AA-00-00-00-00-02",
             "port": 5, "block": True},
            {"mac": None}]}},
        ("POST", f"{SITE}/devices/AA-00-00-00-00-01/reboot"): {"errorCode": 0},
        ("POST", f"{SITE}/clients/BB-00-00-00-00-01/block"): {"errorCode": 0},
    }
    routes.update(over or {})
    return FakeApi(routes)


def run(coro):
    return asyncio.run(coro)


def test_omada():
    api = omada()
    d = api.attach(OmadaDriver(Target("192.0.2.1", site="branch"), Credentials(api_key="id", api_secret="secret")))

    async def go():
        snap = await d.collect()
        assert d._site == "s2" and d.http.headers["Authorization"] == "AccessToken=AT"
        assert snap.facts.version == "5.14.26" and snap.facts.extra == {"site": "s2", "devices": 2, "clients": 2}
        ap, sw = snap.children
        assert (ap.kind, ap.online, ap.uplink_mac, ap.uplink_port, ap.clients) == ("ap", True, "aa:00:00:00:00:02", "8", 3)
        assert (sw.model, sw.online) == ("SG2210", True)
        phone, nas = snap.endpoints
        assert (phone.ssid, phone.signal, phone.attached_to, phone.vlan) == ("Office", -60, "aa:00:00:00:00:01", 10)
        assert (nas.ssid, nas.signal, nas.attached_to, nas.port, nas.blocked) == ("", None, "aa:00:00:00:00:02", "5", True)
        assert await d.apply("reboot", {"mac": "aa:00:00:00:00:01"}) == "Reboot started"
        assert await d.apply("endpoint_block", {"mac": "bb:00:00:00:00:01", "blocked": True}) == "Client blocked"
        with pytest.raises(ExternalServiceError, match="Specify the MAC"):
            await d.apply("reboot", {})
        with pytest.raises(UnsupportedAction):
            await d.apply("hostname", {"name": "x"})
    run(go())
    token_calls = [r for r in api.requests if r.url.path == "/openapi/authorize/token"]
    assert len(token_calls) == 1 and token_calls[0].url.params["grant_type"] == "client_credentials"


def test_omada_failures():
    def collect(api, site=""):
        d = api.attach(OmadaDriver(Target("192.0.2.1", site=site), Credentials(api_key="id", api_secret="s")))
        return run(d.collect())

    with pytest.raises(ExternalServiceError, match="not an Omada controller"):
        collect(omada({"/api/info": {"result": {}}}))
    with pytest.raises(ExternalServiceError, match="bad secret"):
        collect(omada({("POST", "/openapi/authorize/token"): {"msg": "bad secret"}}))
    with pytest.raises(ExternalServiceError, match="denied access"):
        collect(omada({("POST", "/openapi/authorize/token"): httpx.Response(401)}))
    with pytest.raises(ExternalServiceError, match="no sites"):
        collect(omada({f"/openapi/v1/{CID}/sites": {"errorCode": 0, "result": {"data": []}}}))
    with pytest.raises(ExternalServiceError, match="Omada: forbidden"):
        collect(omada({f"/openapi/v1/{CID}/sites": {"errorCode": -1, "msg": "forbidden"}}))
    # No site asked for: the first one; the clients may be refused without failing the poll.
    snap = collect(omada({f"/openapi/v1/{CID}/sites/s1/devices": {"errorCode": 0, "result": {"data": []}},
                            f"/openapi/v1/{CID}/sites/s1/clients": {"errorCode": -1, "msg": "no rights"}}))
    assert snap.facts.extra["site"] == "s1" and snap.endpoints == []


def unifi(over=None, prefix="/proxy/network"):
    base = f"{prefix}/api/s/default"
    routes = {
        f"{base}/stat/sysinfo": {"data": [{"hostname": "udm", "version": "8.1.113", "uptime": 500}]},
        f"{base}/stat/device": {"data": [
            {"mac": "f0:9f:c2:00:00:01", "name": "USW", "model": "USW-24", "type": "usw", "ip": "192.168.1.2",
             "state": 1, "version": "7.0", "num_sta": 4, "uplink": {"uplink_mac": "f0:9f:c2:00:00:09", "uplink_remote_port": 1},
             "port_table": [{"port_idx": 1, "name": "Port 1", "up": True, "speed": 1000, "rx_bytes": 1, "tx_bytes": 2}]},
            {"name": "no mac"}]},
        f"{base}/stat/sta": {"data": [
            {"mac": "aa:bb:cc:00:00:01", "ip": "192.168.1.40", "hostname": "pc", "is_wired": True,
             "sw_mac": "f0:9f:c2:00:00:01", "sw_port": 3, "rx_bytes-r": 10.5, "vlan": 20},
            {"mac": "aa:bb:cc:00:00:02", "last_ip": "192.168.1.41", "name": "phone", "is_wired": False,
             "essid": "Home", "rssi": -50, "ap_mac": "f0:9f:c2:00:00:05", "blocked": True},
            {"mac": ""}]},
        ("POST", f"{base}/cmd/devmgr"): {"data": []},
        ("POST", f"{base}/cmd/stamgr"): {"data": []},
    }
    routes.update(over or {})
    return FakeApi(routes)


def test_unifi_os_login_and_collect():
    api = unifi({("POST", "/api/auth/login"): httpx.Response(200, headers={"x-csrf-token": "csrf1"})})
    d = api.attach(UnifiDriver(Target("192.0.2.1"), Credentials(username="admin", password="pw")))

    async def go():
        snap = await d.collect()
        assert d.http.headers["X-CSRF-Token"] == "csrf1"
        assert (snap.facts.hostname, snap.facts.version, snap.facts.uptime_seconds) == ("udm", "8.1.113", 500)
        sw = snap.children[0]
        assert (sw.kind, sw.online, sw.uplink_mac, sw.uplink_port, sw.clients) == ("switch", True, "f0:9f:c2:00:00:09", "1", 4)
        assert snap.interfaces[0].name == "USW/1" and snap.interfaces[0].speed_mbps == 1000
        pc, phone = snap.endpoints
        assert (pc.attached_to, pc.port, pc.vlan, pc.rx_rate, pc.ssid) == ("f0:9f:c2:00:00:01", "3", 20, 10.5, "")
        assert (phone.ip, phone.ssid, phone.signal, phone.attached_to, phone.blocked) == ("192.168.1.41", "Home", -50, "f0:9f:c2:00:00:05", True)
        assert await d.apply("reboot", {"mac": "F0:9F:C2:00:00:01"}) == "Reboot started"
        assert api.sent[-1][2] == {"cmd": "restart", "mac": "f0:9f:c2:00:00:01"}
        assert await d.apply("endpoint_block", {"mac": "aa:bb:cc:00:00:02", "blocked": False}) == "Client unblocked"
        assert api.sent[-1][2] == {"cmd": "unblock-sta", "mac": "aa:bb:cc:00:00:02"}
        with pytest.raises(ExternalServiceError, match="Specify the MAC"):
            await d.apply("reboot", {})
        with pytest.raises(UnsupportedAction):
            await d.apply("dns", {})
    run(go())


def test_unifi_classic_controller_and_api_key():
    api = unifi({("POST", "/api/auth/login"): httpx.Response(404), ("POST", "/api/login"): {}}, prefix="")
    d = api.attach(UnifiDriver(Target("192.0.2.1"), Credentials(username="admin", password="pw")))
    snap = run(d.collect())
    assert d._prefix == "" and snap.children[0].name == "USW"

    keyed = unifi()
    d = keyed.attach(UnifiDriver(Target("192.0.2.1"), Credentials(api_key="k")))
    run(d.collect())
    assert keyed.requests[0].headers["X-API-KEY"] == "k" and not any(r.url.path.endswith("login") for r in keyed.requests)

    for routes, match in [({("POST", "/api/auth/login"): httpx.Response(401)}, r"login failed \(401\)"),
                          ({("POST", "/api/auth/login"): httpx.Response(405), ("POST", "/api/login"): httpx.Response(400)},
                           r"login failed \(400\)")]:
        d = unifi(routes).attach(UnifiDriver(Target("192.0.2.1"), Credentials(username="a", password="b")))
        with pytest.raises(ExternalServiceError, match=match):
            run(d.collect())

    def refused(req):
        raise httpx.ConnectError("refused")
    d = unifi({("POST", "/api/auth/login"): refused}).attach(UnifiDriver(Target("192.0.2.1"), Credentials()))
    with pytest.raises(ExternalServiceError, match="unreachable"):
        run(d.collect())


def meraki(orgs=None):
    return FakeApi({
        "/api/v1/organizations": orgs if orgs is not None else [{"id": "1", "name": "Shop"}, {"id": "2", "name": "HQ"}],
        "/api/v1/organizations/2/devices": [
            {"mac": "e0:55:3d:00:00:01", "name": "MR46", "serial": "Q2-1", "model": "MR46", "productType": "wireless",
             "lanIp": "10.0.0.5", "firmware": "29.7", "networkId": "N1"},
            {"mac": "e0:55:3d:00:00:02", "serial": "Q2-2", "model": "MS120", "productType": "switch", "networkId": "N1"},
            {"serial": "Q2-3"}],
        "/api/v1/organizations/2/devices/statuses": [{"serial": "Q2-2", "status": "offline"}],
        "/api/v1/networks/N1/clients": [{"mac": "aa:bb:cc:00:00:01", "ip": "10.0.0.50", "description": "laptop",
                                         "ssid": "HQ", "recentDeviceMac": "e0:55:3d:00:00:01", "vlan": 30},
                                        {"mac": ""}],
        ("POST", "/api/v1/devices/Q2-1/reboot"): {"success": True},
    })


def test_meraki():
    api = meraki()
    d = api.attach(MerakiDriver(Target("api.meraki.com", site="hq"), Credentials(api_key="key")))
    assert d._client_kw()["headers"] == {"Authorization": "Bearer key"}

    async def go():
        snap = await d.collect()
        assert snap.facts.hostname == "HQ" and snap.facts.extra == {"organization": "2", "devices": 2}
        mr, ms = snap.children
        assert (mr.kind, mr.online, mr.ip, ms.kind, ms.online, ms.name) == ("ap", True, "10.0.0.5", "switch", False, "Q2-2")
        assert [(e.hostname, e.ssid, e.attached_to, e.vlan) for e in snap.endpoints] == [("laptop", "HQ", "e0:55:3d:00:00:01", 30)]
        assert await d.apply("reboot", {"mac": "E0:55:3D:00:00:01"}) == "Reboot started"
        with pytest.raises(ExternalServiceError, match="no device with this MAC"):
            await d.apply("reboot", {"mac": "00:00:00:00:00:99"})
        with pytest.raises(UnsupportedAction):
            await d.apply("hostname", {"name": "x"})
    run(go())
    d = meraki(orgs=[]).attach(MerakiDriver(Target("api.meraki.com"), Credentials(api_key="key")))
    with pytest.raises(ExternalServiceError, match="no organizations"):
        run(d.collect())


def redfish(over=None):
    s = "/redfish/v1/Systems/1"
    routes = {
        "/redfish/v1/Systems": {"Members": [{"@odata.id": s}]},
        s: {"HostName": "srv1", "Manufacturer": "Dell Inc.", "Model": "PowerEdge R650", "BiosVersion": "1.9.2",
            "SerialNumber": "ABC", "PowerState": "On", "Status": {"Health": "OK"},
            "ProcessorSummary": {"Count": 2, "Model": "Xeon"}, "MemorySummary": {"TotalSystemMemoryGiB": 256}},
        "/redfish/v1/Managers": {"Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC"}]},
        "/redfish/v1/Managers/iDRAC": {"FirmwareVersion": "7.00"},
        f"{s}/EthernetInterfaces": {"Members": [{"@odata.id": f"{s}/EthernetInterfaces/NIC1"}]},
        f"{s}/EthernetInterfaces/NIC1": {"Id": "NIC.1", "MACAddress": "b0:7b:25:00:00:01", "LinkStatus": "LinkUp",
                                         "SpeedMbps": 10000, "IPv4Addresses": [{"Address": "10.0.0.20", "SubnetMask": "255.255.255.0"},
                                                                               {"Address": ""}]},
        ("POST", f"{s}/Actions/ComputerSystem.Reset"): {},
        ("PATCH", s): {},
    }
    routes.update(over or {})
    return FakeApi(routes)


def test_redfish():
    api = redfish()
    d = api.attach(RedfishDriver(Target("192.0.2.30"), Credentials(username="root", password="calvin")))
    assert d._client_kw() == {"auth": ("root", "calvin")}

    async def go():
        snap = await d.collect()
        f = snap.facts
        assert (f.hostname, f.vendor, f.model, f.version, f.mac) == ("srv1", "Dell Inc.", "PowerEdge R650", "1.9.2", "b0:7b:25:00:00:01")
        assert f.extra == {"power": "On", "health": "OK", "bmc_firmware": "7.00", "cpus": 2, "cpu_model": "Xeon", "memory_gib": 256}
        nic = snap.interfaces[0]
        assert (nic.name, nic.oper_up, nic.speed_mbps, nic.addresses) == ("NIC.1", True, 10000, ["10.0.0.20/24"])
        assert await d.apply("reboot", {}) == "Reboot started"
        assert api.sent[-1][2] == {"ResetType": "GracefulRestart"}
        assert await d.apply("hostname", {"name": "srv2"}) == "Done"
        with pytest.raises(UnsupportedAction):
            await d.apply("dns", {})
    run(go())
    d = redfish({"/redfish/v1/Systems": {"Members": []}}).attach(RedfishDriver(Target("192.0.2.30"), Credentials()))
    with pytest.raises(ExternalServiceError, match="no systems"):
        run(d.collect())
    bare = redfish({"/redfish/v1/Managers": httpx.Response(404),
                      "/redfish/v1/Systems/1/EthernetInterfaces": httpx.Response(404)})
    snap = run(bare.attach(RedfishDriver(Target("192.0.2.30"), Credentials())).collect())
    assert snap.interfaces == [] and snap.facts.extra["bmc_firmware"] is None and snap.facts.mac == ""


def test_bits():
    assert _bits("255.255.0.0") == 16 and _bits(None) == 32
