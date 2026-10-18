"""Firewalls and home / SMB routers with an HTTP API or a Linux shell: OPNsense, pfSense, FortiGate, Keenetic,
OpenWrt and generic Linux (Debian / Ubuntu / VyOS / EdgeOS / Proxmox / ASUS Merlin / Synology)."""
from __future__ import annotations

import hashlib
import json
import re

import httpx

from ...domain.errors import ExternalServiceError
from .base import (DeviceDriver, Endpoint, Facts, Interface, Neighbor, Snapshot, UnsupportedAction, norm_ip, norm_mac,
                   to_bool, to_int)
from .parsers import parse_uptime
from .transport import SshTransport, http_client, http_error


def _mask_bits(mask) -> int:
    try:
        return sum(bin(int(x)).count("1") for x in str(mask).split(".")) if "." in str(mask) else int(mask)
    except ValueError:
        return 32


class HttpApiDriver(DeviceDriver):
    default_transport = "https"
    default_port: int | None = None

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.http = http_client(self.target, default_port=self.default_port, **self._client_kw())

    def _client_kw(self) -> dict:
        return {}

    async def _get(self, path: str, optional: bool = False, **params):
        try:
            r = await self.http.get(path, params=params or None)
            if optional and r.status_code in (400, 403, 404, 405, 501):
                return None
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            if optional:
                return None
            raise http_error(self.target.host, e) from e
        except ValueError as e:
            if optional:
                return None
            raise ExternalServiceError(f"{self.target.host}: the API response is not JSON") from e

    async def _send(self, method: str, path: str, body=None, timeout: float = 30):
        try:
            r = await self.http.request(method, path, json=body, timeout=timeout)
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                return r.text
        except httpx.HTTPError as e:
            raise http_error(self.target.host, e) from e

    async def close(self) -> None:
        await self.http.aclose()


# ---------------- OPNsense ----------------

class OpnsenseDriver(HttpApiDriver):
    """OPNsense REST API with an API key / secret (System → Access → Users → API keys)."""
    capabilities = frozenset({"flow_export", "reboot", "backup"})

    def _client_kw(self) -> dict:
        return {"auth": (self.creds.api_key or self.creds.username, self.creds.api_secret or self.creds.password)}

    async def collect(self) -> Snapshot:
        info = await self._get("/api/diagnostics/system/systemInformation", optional=True) or {}
        tm = await self._get("/api/diagnostics/system/systemTime", optional=True) or {}
        res = await self._get("/api/diagnostics/system/systemResources", optional=True) or {}
        versions = info.get("versions") or []
        mem = res.get("memory") or {}
        total, used = to_int(mem.get("total")), to_int(mem.get("used"))
        facts = Facts(hostname=str(info.get("name", "")).split(".")[0], vendor="Deciso", model="OPNsense", os="OPNsense",
                      version=(versions[0].split(" ")[1] if versions and " " in versions[0] else ""),
                      uptime_seconds=parse_uptime(str(tm.get("uptime", ""))),
                      memory_percent=round(used * 100 / total, 1) if total else None)
        names = await self._get("/api/diagnostics/interface/getInterfaceNames", optional=True) or {}
        cfg = await self._get("/api/diagnostics/interface/getInterfaceConfig") or {}
        traffic = (await self._get("/api/diagnostics/traffic/interface", optional=True) or {}).get("interfaces", {})
        by_dev = {v.get("device"): v for v in traffic.values() if isinstance(v, dict)}
        interfaces = []
        for dev, c in cfg.items():
            if not isinstance(c, dict):
                continue
            t = by_dev.get(dev, {})
            interfaces.append(Interface(
                name=dev, description=names.get(dev, t.get("name", "")), mac=norm_mac(c.get("macaddr")),
                admin_up="up" in (c.get("flags") or []), oper_up=c.get("status") in ("active", "associated", None)
                and "up" in (c.get("flags") or []), mtu=to_int(c.get("mtu"), None),
                addresses=[f"{a.get('ipaddr')}/{a.get('subnetbits')}" for a in c.get("ipv4") or [] if a.get("ipaddr")],
                rx_bytes=to_int(t.get("bytes received"), None) if t else None,
                tx_bytes=to_int(t.get("bytes transmitted"), None) if t else None))
        endpoints = []
        for a in await self._get("/api/diagnostics/interface/getArp", optional=True) or []:
            if norm_mac(a.get("mac")):
                endpoints.append(Endpoint(mac=norm_mac(a.get("mac")), ip=norm_ip(a.get("ip")),
                                          hostname=str(a.get("hostname", "")), interface=str(a.get("intf", "")), source="arp"))
        for path in ("/api/dhcpv4/leases/searchLease", "/api/kea/leases4/search", "/api/dnsmasq/leases/search"):
            rows = (await self._get(path, optional=True) or {}).get("rows") or []
            for lease in rows:
                mac = norm_mac(lease.get("mac") or lease.get("hwaddr"))
                if mac:
                    endpoints.append(Endpoint(mac=mac, ip=norm_ip(lease.get("address")),
                                              hostname=str(lease.get("hostname", "")),
                                              interface=str(lease.get("if_descr") or lease.get("if") or ""), source="dhcp"))
        return Snapshot(facts, interfaces, [], endpoints)

    async def apply(self, action: str, p: dict) -> str:
        if action == "reboot":
            await self._send("POST", "/api/core/system/reboot", {})
            return "Reboot started"
        if action == "flow_export":
            if p["protocol"] == "sflow":
                raise UnsupportedAction("OPNsense", "flow_export (sflow)")
            traffic = (await self._get("/api/diagnostics/traffic/interface", optional=True) or {}).get("interfaces", {})
            ifaces = ",".join(k for k in traffic) or "lan,wan"
            version = {"netflow5": "v5", "netflow9": "v9", "ipfix": "v9"}[p["protocol"]]
            await self._send("POST", "/api/diagnostics/netflow/setconfig", {"netflow": {"capture": {
                "interfaces": ifaces if p["enabled"] else "", "egress_only": "", "version": version,
                "targets": f"{p['collector']}:{p['port']}" if p["enabled"] else ""}}})
            await self._send("POST", "/api/diagnostics/netflow/reconfigure", {})
            return f"NetFlow {'enabled' if p['enabled'] else 'disabled'} on interfaces {ifaces}"
        raise UnsupportedAction("OPNsense", action)

    async def backup(self) -> str:
        try:
            r = await self.http.get("/api/core/backup/download/this")
            r.raise_for_status()
            return r.text
        except httpx.HTTPError as e:
            raise http_error(self.target.host, e) from e


# ---------------- pfSense ----------------

class PfsenseDriver(HttpApiDriver):
    """pfSense with the pfSense-pkg-RESTAPI package (v2): an API key in X-API-Key or a user and password."""
    capabilities = frozenset({"hostname", "dns", "reboot", "command", "backup"})

    def _client_kw(self) -> dict:
        if self.creds.api_key:
            return {"headers": {"X-API-Key": self.creds.api_key}}
        return {"auth": (self.creds.username, self.creds.password)}

    async def _data(self, path: str, optional: bool = False):
        r = await self._get(path, optional=optional)
        return (r or {}).get("data") if isinstance(r, dict) else r

    async def collect(self) -> Snapshot:
        st = await self._data("/api/v2/status/system", optional=True) or {}
        ver = await self._data("/api/v2/system/version", optional=True) or {}
        host = await self._data("/api/v2/system/hostname", optional=True) or {}
        facts = Facts(hostname=str(host.get("hostname", "")), vendor="Netgate", model=str(st.get("platform", "pfSense")),
                      os="pfSense", version=str(ver.get("version", "")), serial=str(st.get("serial", "")),
                      uptime_seconds=parse_uptime(str(st.get("uptime", ""))),
                      cpu_percent=float(st["cpu_usage"]) if st.get("cpu_usage") is not None else None,
                      memory_percent=float(st["mem_usage"]) if st.get("mem_usage") is not None else None)
        interfaces = []
        for i in await self._data("/api/v2/status/interfaces") or []:
            addr = [f"{i['ipaddr']}/{i.get('subnet') or 32}"] if i.get("ipaddr") else []
            interfaces.append(Interface(name=str(i.get("hwif") or i.get("name")), description=str(i.get("descr", "")),
                                        mac=norm_mac(i.get("macaddr")), admin_up=to_bool(i.get("enable", True)),
                                        oper_up=str(i.get("status")) in ("up", "associated"), addresses=addr,
                                        rx_bytes=to_int(i.get("inbytes"), None), tx_bytes=to_int(i.get("outbytes"), None)))
        endpoints = []
        for a in await self._data("/api/v2/diagnostics/arp_table", optional=True) or []:
            if norm_mac(a.get("mac_address")):
                endpoints.append(Endpoint(mac=norm_mac(a.get("mac_address")), ip=norm_ip(a.get("ip_address")),
                                          hostname=str(a.get("hostname", "")), interface=str(a.get("interface", "")),
                                          source="arp"))
        for lease in await self._data("/api/v2/status/dhcp_server/leases", optional=True) or []:
            if norm_mac(lease.get("mac")):
                endpoints.append(Endpoint(mac=norm_mac(lease.get("mac")), ip=norm_ip(lease.get("ip")),
                                          hostname=str(lease.get("hostname", "")), interface=str(lease.get("if", "")),
                                          source="dhcp"))
        return Snapshot(facts, interfaces, [], endpoints)

    async def apply(self, action: str, p: dict) -> str:
        if action == "hostname":
            await self._send("PATCH", "/api/v2/system/hostname", {"hostname": p["name"]})
        elif action == "dns":
            await self._send("PATCH", "/api/v2/system/dns", {"dnsserver": p["servers"]})
        elif action == "reboot":
            try:
                await self._send("POST", "/api/v2/diagnostics/reboot", {}, timeout=10)
            except ExternalServiceError:
                pass
            return "Reboot started"
        elif action == "command":
            r = await self._send("POST", "/api/v2/diagnostics/command_prompt", {"command": p["command"]}, timeout=120)
            return str(((r or {}).get("data") or {}).get("output", "")) if isinstance(r, dict) else str(r)
        else:
            raise UnsupportedAction("pfSense", action)
        return "Done"

    async def backup(self) -> str:
        return await self.apply("command", {"command": "cat /cf/conf/config.xml"})


# ---------------- FortiGate ----------------

class FortigateDriver(HttpApiDriver):
    """Fortinet FortiGate (FortiOS 6.4+) REST API with a REST API administrator token."""
    capabilities = frozenset({"hostname", "interface", "dns", "ntp", "flow_export", "reboot", "backup"})

    def _client_kw(self) -> dict:
        return {"headers": {"Authorization": f"Bearer {self.creds.api_key}"}}

    async def _results(self, path: str, optional: bool = True):
        r = await self._get(path, optional=optional)
        return (r or {}).get("results") if isinstance(r, dict) else None

    async def collect(self) -> Snapshot:
        st = await self._get("/api/v2/monitor/system/status") or {}
        res = st.get("results") or {}
        perf = await self._results("/api/v2/monitor/system/performance/status") or {}
        mem = perf.get("mem") or {}
        facts = Facts(hostname=str(res.get("hostname", "")), vendor="Fortinet",
                      model=str(res.get("model_name") or res.get("model") or ""), os="FortiOS",
                      version=str(st.get("version", "")), serial=str(st.get("serial", "")),
                      uptime_seconds=to_int(perf.get("uptime"), None) if perf.get("uptime") is not None else None,
                      memory_percent=round(to_int(mem.get("used")) * 100 / to_int(mem.get("total")), 1)
                      if to_int(mem.get("total")) else None)
        interfaces = []
        for name, i in (await self._results("/api/v2/monitor/system/interface", optional=False) or {}).items():
            addr = [f"{i['ip']}/{_mask_bits(i.get('mask', 32))}"] if i.get("ip") and i.get("ip") != "0.0.0.0" else []
            interfaces.append(Interface(name=name, description=str(i.get("alias", "")), mac=norm_mac(i.get("mac")),
                                        oper_up=bool(i.get("link")), speed_mbps=to_int(i.get("speed"), None) or None,
                                        addresses=addr, rx_bytes=to_int(i.get("rx_bytes"), None),
                                        tx_bytes=to_int(i.get("tx_bytes"), None)))
        neighbors = [Neighbor(local_interface=str(n.get("port_name", "")), remote_name=str(n.get("system_name", "")),
                              remote_mac=norm_mac(n.get("chassis_id")), remote_interface=str(n.get("port_id", "")),
                              remote_ip=norm_ip((n.get("addresses") or [{}])[0].get("address") if n.get("addresses") else ""),
                              protocol="lldp")
                     for n in await self._results("/api/v2/monitor/network/lldp/neighbors") or []]
        endpoints = [Endpoint(mac=norm_mac(a.get("mac")), ip=norm_ip(a.get("ip")), interface=str(a.get("interface", "")),
                              source="arp") for a in await self._results("/api/v2/monitor/network/arp") or []
                     if norm_mac(a.get("mac"))]
        endpoints += [Endpoint(mac=norm_mac(d.get("mac")), ip=norm_ip(d.get("ip")), hostname=str(d.get("hostname", "")),
                               interface=str(d.get("interface", "")), source="dhcp")
                      for d in await self._results("/api/v2/monitor/system/dhcp") or [] if norm_mac(d.get("mac"))]
        return Snapshot(facts, interfaces, neighbors, endpoints)

    async def apply(self, action: str, p: dict) -> str:
        if action == "hostname":
            await self._send("PUT", "/api/v2/cmdb/system/global", {"hostname": p["name"]})
        elif action == "interface":
            body = {}
            if p.get("enabled") is not None:
                body["status"] = "up" if p["enabled"] else "down"
            if p.get("description") is not None:
                body["description"] = p["description"]
            await self._send("PUT", f"/api/v2/cmdb/system/interface/{p['name']}", body)
        elif action == "dns":
            s = p["servers"]
            await self._send("PUT", "/api/v2/cmdb/system/dns", {"primary": s[0], "secondary": s[1] if len(s) > 1 else ""})
        elif action == "ntp":
            await self._send("PUT", "/api/v2/cmdb/system/ntp", {
                "ntpsync": "enable", "type": "custom", "ntpserver": [{"id": i + 1, "server": s} for i, s in enumerate(p["servers"])]})
        elif action == "flow_export":
            path = "/api/v2/cmdb/system/sflow" if p["protocol"] == "sflow" else "/api/v2/cmdb/system/netflow"
            if not p["enabled"]:
                await self._send("PUT", path, {"collectors": []})
            else:
                try:
                    await self._send("PUT", path, {"collectors": [{"id": 1, "collector-ip": p["collector"],
                                                                   "collector-port": p["port"]}]})
                except ExternalServiceError:
                    await self._send("PUT", path, {"collector-ip": p["collector"], "collector-port": p["port"]})
            return "Collector set; enable netflow-sampler / sflow-sampler on the interfaces you need"
        elif action == "reboot":
            try:
                await self._send("POST", "/api/v2/monitor/system/os/reboot", {"event_log_message": "Harbor"})
            except ExternalServiceError:
                pass
            return "Reboot started"
        else:
            raise UnsupportedAction("FortiGate", action)
        return "Done"

    async def backup(self) -> str:
        try:
            r = await self.http.get("/api/v2/monitor/system/config/backup", params={"scope": "global"})
            r.raise_for_status()
            return r.text
        except httpx.HTTPError as e:
            raise http_error(self.target.host, e) from e


# ---------------- Keenetic ----------------

class KeeneticDriver(HttpApiDriver):
    """Keenetic (KeeneticOS 3.x / 4.x) RCI over the web interface; CLI commands are sent with {"parse": ...}."""
    capabilities = frozenset({"hostname", "interface", "dns", "ntp", "reboot", "endpoint_block", "command", "backup"})
    default_transport = "http"

    async def _login(self) -> None:
        r = await self.http.get("/auth")
        if r.status_code == 200:
            return
        realm, challenge = r.headers.get("X-NDM-Realm", ""), r.headers.get("X-NDM-Challenge", "")
        if not challenge:
            raise ExternalServiceError(f"{self.target.host}: this is not a Keenetic web interface")
        # The challenge-response of the Keenetic web interface is defined with MD5; the router decides, not the panel.
        md5 = hashlib.md5(f"{self.creds.username}:{realm}:{self.creds.password}".encode(),
                          usedforsecurity=False).hexdigest()
        sha = hashlib.sha256((challenge + md5).encode()).hexdigest()
        r = await self.http.post("/auth", json={"login": self.creds.username, "password": sha})
        if r.status_code != 200:
            raise ExternalServiceError(f"{self.target.host}: Keenetic: wrong login or password")

    async def _rci(self, path: str, optional: bool = False):
        try:
            if not self.http.cookies:
                await self._login()
            return await self._get(f"/rci/{path}", optional=optional)
        except httpx.HTTPError as e:
            raise http_error(self.target.host, e) from e

    async def _parse(self, *commands: str) -> list:
        if not self.http.cookies:
            await self._login()
        r = await self._send("POST", "/rci/", [{"parse": c} for c in commands], timeout=60)
        out = r if isinstance(r, list) else [r]
        for item in out:
            for st in (item.get("parse", {}) if isinstance(item, dict) else {}).get("status", []) or []:
                if st.get("status") == "error":
                    raise ExternalServiceError(f"Keenetic: {st.get('message', '')}")
        return out

    async def collect(self) -> Snapshot:
        ver = await self._rci("show/version") or {}
        sysinfo = await self._rci("show/system", optional=True) or {}
        mem = str(sysinfo.get("memory", ""))
        used, _, total = mem.partition("/")
        facts = Facts(hostname=str(sysinfo.get("hostname", "")), vendor="Keenetic", model=str(ver.get("model") or ver.get("device", "")),
                      os="KeeneticOS", version=str(ver.get("release") or ver.get("title", "")),
                      serial=str(ver.get("serial", "")), uptime_seconds=to_int(sysinfo.get("uptime"), None),
                      cpu_percent=float(sysinfo["cpuload"]) if sysinfo.get("cpuload") is not None else None,
                      memory_percent=round(to_int(used) * 100 / to_int(total), 1) if to_int(total) else None)
        interfaces = []
        for name, i in (await self._rci("show/interface") or {}).items():
            if not isinstance(i, dict):
                continue
            addr = [f"{i['address']}/{_mask_bits(i.get('mask', 32))}"] if i.get("address") else []
            interfaces.append(Interface(name=str(i.get("id", name)), description=str(i.get("description", "")),
                                        type=str(i.get("type", "")), mac=norm_mac(i.get("mac")),
                                        admin_up=str(i.get("state", "up")) == "up", oper_up=str(i.get("link", "")) == "up"
                                        or str(i.get("connected", "")) == "yes", mtu=to_int(i.get("mtu"), None),
                                        addresses=addr))
        endpoints = []
        hosts = (await self._rci("show/ip/hotspot", optional=True) or {}).get("host", [])
        for h in hosts if isinstance(hosts, list) else []:
            mac = norm_mac(h.get("mac"))
            if not mac:
                continue
            wifi = bool(h.get("ssid"))
            endpoints.append(Endpoint(mac=mac, ip=norm_ip(h.get("ip")), hostname=str(h.get("name") or h.get("hostname") or ""),
                                      interface=str((h.get("interface") or {}).get("id", "")) if isinstance(h.get("interface"), dict) else "",
                                      source="wifi" if wifi else "dhcp", ssid=str(h.get("ssid", "")),
                                      signal=to_int(h.get("rssi"), None) if h.get("rssi") is not None else None,
                                      blocked=str(h.get("access", "permit")) == "deny"))
        return Snapshot(facts, interfaces, [], endpoints)

    async def apply(self, action: str, p: dict) -> str:
        if action == "hostname":
            cmds = [f"system name {p['name']}"]
        elif action == "interface":
            cmds = []
            if p.get("enabled") is not None:
                cmds.append(f"interface {p['name']} {'up' if p['enabled'] else 'down'}")
            if p.get("description") is not None:
                cmds.append(f"interface {p['name']} description \"{p['description']}\"")
        elif action == "dns":
            cmds = [f"ip name-server {s}" for s in p["servers"]]
        elif action == "ntp":
            cmds = [f"ntp server {s}" for s in p["servers"]]
        elif action == "reboot":
            try:
                await self._parse("system reboot")
            except (ExternalServiceError, httpx.HTTPError):
                pass
            return "Reboot started"
        elif action == "endpoint_block":
            cmds = [f"ip hotspot host {p['mac']} {'deny' if p['blocked'] else 'permit'}"]
        elif action == "command":
            return json.dumps(await self._parse(p["command"]), ensure_ascii=False, indent=1)
        else:
            raise UnsupportedAction("Keenetic", action)
        await self._parse(*cmds, "system configuration save")
        return "Done"

    async def backup(self) -> str:
        out = await self._parse("show running-config")
        msg = (out[0].get("parse", {}) if out and isinstance(out[0], dict) else {}).get("message")
        return "\n".join(msg) if isinstance(msg, list) else json.dumps(out, ensure_ascii=False, indent=1)


# ---------------- Linux / OpenWrt over SSH ----------------

LINUX_SCRIPT = r"""echo '#HOST'; hostname; echo '#OS'; cat /etc/os-release 2>/dev/null; cat /etc/openwrt_release 2>/dev/null
echo '#UPTIME'; cat /proc/uptime; echo '#MODEL'; cat /tmp/sysinfo/model 2>/dev/null || cat /sys/class/dmi/id/product_name 2>/dev/null
echo '#MEM'; grep -E '^(MemTotal|MemAvailable|MemFree):' /proc/meminfo; echo '#LOAD'; cat /proc/loadavg; nproc 2>/dev/null
echo '#LINK'; ip -o link 2>/dev/null; echo '#ADDR'; ip -o -4 addr 2>/dev/null; echo '#DEV'; cat /proc/net/dev
echo '#SPEED'; for f in /sys/class/net/*/speed; do echo "$f $(cat $f 2>/dev/null)"; done
echo '#ARP'; cat /proc/net/arp; echo '#LEASES'; cat /tmp/dhcp.leases /var/lib/misc/dnsmasq.leases 2>/dev/null
echo '#LLDP'; lldpcli -f json show neighbors 2>/dev/null"""


def split_sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    cur = ""
    for line in text.splitlines():
        if re.fullmatch(r"#[A-Z]+", line.strip()):
            cur = line.strip()[1:]
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def parse_linux(text: str) -> Snapshot:
    s = split_sections(text)
    osr = dict(re.findall(r'^(\w+)=["\']?(.*?)["\']?$', s.get("OS", ""), re.M))
    mem = dict(re.findall(r"^(\w+):\s+(\d+)", s.get("MEM", ""), re.M))
    total = to_int(mem.get("MemTotal"))
    avail = to_int(mem.get("MemAvailable") or mem.get("MemFree"))
    load = s.get("LOAD", "").split()
    cpus = to_int(load[-1], 1) if len(load) > 5 else 1
    facts = Facts(hostname=s.get("HOST", "").strip(), os=osr.get("DISTRIB_ID") or osr.get("NAME", "Linux"),
                  version=osr.get("DISTRIB_RELEASE") or osr.get("VERSION_ID", ""), model=s.get("MODEL", "").strip(),
                  uptime_seconds=int(float(s.get("UPTIME", "0").split()[0] or 0)) if s.get("UPTIME") else None,
                  cpu_percent=round(min(100.0, float(load[0]) * 100 / max(cpus, 1)), 1) if load else None,
                  memory_percent=round((total - avail) * 100 / total, 1) if total else None)
    counters = {}
    for line in s.get("DEV", "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        v = rest.split()
        if len(v) >= 9:
            counters[name.strip()] = (int(v[0]), int(v[8]))
    speeds = {m.group(1): to_int(m.group(2), 0) for m in re.finditer(r"/sys/class/net/([^/]+)/speed (-?\d+)", s.get("SPEED", ""))}
    addrs: dict[str, list[str]] = {}
    for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+(\S+)", s.get("ADDR", ""), re.M):
        addrs.setdefault(m.group(1), []).append(m.group(2))
    interfaces = []
    for line in s.get("LINK", "").splitlines():
        # One line of `ip -o link`: "2: eth0: <FLAGS> mtu 1500 ... link/ether 52:54:00:12:34:56 brd ..."; the parts
        # are found one by one (the MAC used to be lost: a lazy match before an optional group took nothing).
        m = re.match(r"\d+:\s+([^:@\s]+)(?:@\S+)?:\s+<([^>]*)>", line)
        mtu = re.search(r"\bmtu (\d+)", line)
        if not m or not mtu:
            continue
        name, flags = m.group(1), m.group(2).split(",")
        if name == "lo":
            continue
        mac = re.search(r"link/\w+\s+([0-9a-f:]{17})", line)
        rx, tx = counters.get(name, (None, None))
        interfaces.append(Interface(name=name, mac=norm_mac(mac.group(1) if mac else ""), admin_up="UP" in flags,
                                    oper_up="LOWER_UP" in flags, mtu=int(mtu.group(1)), addresses=addrs.get(name, []),
                                    speed_mbps=speeds.get(name) if speeds.get(name, 0) > 0 else None, rx_bytes=rx, tx_bytes=tx))
    if not interfaces:
        interfaces = [Interface(name=n, rx_bytes=rx, tx_bytes=tx, addresses=addrs.get(n, [])) for n, (rx, tx) in counters.items() if n != "lo"]
    endpoints = []
    for line in s.get("ARP", "").splitlines()[1:]:
        v = line.split()
        if len(v) >= 6 and norm_mac(v[3]) and v[3] != "00:00:00:00:00:00" and v[2] != "0x0":
            endpoints.append(Endpoint(mac=norm_mac(v[3]), ip=norm_ip(v[0]), interface=v[5], source="arp"))
    for line in s.get("LEASES", "").splitlines():
        v = line.split()
        if len(v) >= 4 and norm_mac(v[1]):
            endpoints.append(Endpoint(mac=norm_mac(v[1]), ip=norm_ip(v[2]), hostname="" if v[3] == "*" else v[3], source="dhcp"))
    neighbors = []
    try:
        lldp = json.loads(s.get("LLDP") or "{}").get("lldp", {}).get("interface", [])
        for item in lldp if isinstance(lldp, list) else [lldp]:
            for local, n in item.items():
                chassis = next(iter((n.get("chassis") or {}).values()), {}) if isinstance(n.get("chassis"), dict) else {}
                neighbors.append(Neighbor(local_interface=local,
                                          remote_name=next(iter(n.get("chassis", {})), "") if isinstance(n.get("chassis"), dict) else "",
                                          remote_mac=norm_mac((chassis.get("id") or {}).get("value")),
                                          remote_ip=norm_ip((chassis.get("mgmt-ip") or [""])[0] if isinstance(chassis.get("mgmt-ip"), list) else chassis.get("mgmt-ip")),
                                          remote_interface=str(((n.get("port") or {}).get("id") or {}).get("value", "")),
                                          remote_platform=str(chassis.get("descr", ""))[:120], protocol="lldp"))
    except (ValueError, AttributeError):
        pass
    if not facts.mac:
        facts.mac = next((i.mac for i in interfaces if i.mac), "")
    return Snapshot(facts, interfaces, neighbors, endpoints)


class LinuxDriver(DeviceDriver):
    capabilities = frozenset({"hostname", "interface", "reboot", "command"})
    default_transport = "ssh"
    os_name = "Linux"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._ssh = SshTransport(self.target, self.creds)

    async def run(self, cmd: str, timeout: float = 60) -> str:
        try:
            return await self._ssh.run(cmd, timeout=timeout)
        finally:
            self.learned_host_key = self._ssh.host_key or self.learned_host_key

    async def collect(self) -> Snapshot:
        snap = parse_linux(await self.run(LINUX_SCRIPT))
        snap.facts.vendor = snap.facts.vendor or self.vendor
        return snap

    async def apply(self, action: str, p: dict) -> str:
        if action == "hostname":
            await self.run(f"hostnamectl set-hostname {p['name']} 2>/dev/null || hostname {p['name']}")
        elif action == "interface":
            if p.get("enabled") is None:
                raise UnsupportedAction(self.os_name, "interface description")
            await self.run(f"ip link set dev {p['name'].split()[0]} {'up' if p['enabled'] else 'down'}")
        elif action == "reboot":
            try:
                await self.run("(sleep 1; reboot) >/dev/null 2>&1 &", timeout=10)
            except ExternalServiceError:
                pass
            return "Reboot started"
        elif action == "command":
            return await self.run(p["command"], timeout=120)
        else:
            raise UnsupportedAction(self.os_name, action)
        return "Done"

    async def close(self) -> None:
        await self._ssh.close()


class OpenWrtDriver(LinuxDriver):
    """OpenWrt (and GL.iNet, Turris, Cudy, Xiaomi / TP-Link on OpenWrt) over SSH with ubus and uci."""
    capabilities = frozenset({"hostname", "interface", "dns", "ntp", "flow_export", "reboot", "command", "backup"})
    os_name = "OpenWrt"

    async def collect(self) -> Snapshot:
        snap = await super().collect()
        snap.facts.os = "OpenWrt"
        try:
            board = json.loads(await self.run("ubus call system board"))
            snap.facts.model = board.get("model") or snap.facts.model
            snap.facts.version = (board.get("release") or {}).get("version") or snap.facts.version
            snap.facts.hostname = board.get("hostname") or snap.facts.hostname
        except (ValueError, ExternalServiceError):
            pass
        raw = await self.run("for i in $(ubus list 'hostapd.*' 2>/dev/null); do echo \"#$i\"; "
                             "ubus call $i get_clients; ubus call $i get_status 2>/dev/null | grep '\"ssid\"'; done")
        for block in re.split(r"(?m)^#", raw)[1:]:
            name, _, body = block.partition("\n")
            ssid = first_json_value(body, "ssid")
            try:
                clients = json.loads(body[:body.rfind("}") + 1] if "}" in body else "{}").get("clients", {})
            except ValueError:
                clients = {}
            for mac, c in clients.items():
                snap.endpoints.append(Endpoint(mac=norm_mac(mac), interface=name.replace("hostapd.", ""), source="wifi",
                                               ssid=ssid, signal=to_int(c.get("signal"), None) if isinstance(c, dict) else None))
        return snap

    async def apply(self, action: str, p: dict) -> str:
        if action == "hostname":
            await self.run(f"uci set system.@system[0].hostname='{p['name']}' && uci commit system && /etc/init.d/system reload")
        elif action == "dns":
            await self.run("uci -q delete dhcp.@dnsmasq[0].server; " + "; ".join(
                f"uci add_list dhcp.@dnsmasq[0].server='{s}'" for s in p["servers"]) + "; uci commit dhcp && /etc/init.d/dnsmasq restart")
        elif action == "ntp":
            await self.run("uci -q delete system.ntp.server; " + "; ".join(
                f"uci add_list system.ntp.server='{s}'" for s in p["servers"]) + "; uci commit system && /etc/init.d/sysntpd restart")
        elif action == "flow_export":
            if p["protocol"] == "sflow":
                raise UnsupportedAction("OpenWrt", "flow_export (sflow)")
            version = {"netflow5": "5", "netflow9": "9", "ipfix": "10"}[p["protocol"]]
            out = await self.run(
                "[ -f /etc/config/softflowd ] || { echo NOPKG; exit 0; }; "
                f"uci set softflowd.@softflowd[0].enabled='{1 if p['enabled'] else 0}'; "
                f"uci set softflowd.@softflowd[0].host_port='{p['collector']}:{p['port']}'; "
                f"uci set softflowd.@softflowd[0].export_version='{version}'; "
                "uci set softflowd.@softflowd[0].interface=\"$(uci -q get network.lan.device || echo br-lan)\"; "
                "uci commit softflowd && /etc/init.d/softflowd enable && /etc/init.d/softflowd restart")
            if "NOPKG" in out:
                raise ExternalServiceError("softflowd is not installed on OpenWrt: run 'opkg update && opkg install softflowd'")
            return f"softflowd {'enabled' if p['enabled'] else 'disabled'}: {p['collector']}:{p['port']}"
        elif action == "backup":
            return await self.backup()
        else:
            return await super().apply(action, p)
        return "Done"

    async def backup(self) -> str:
        return await self.run("uci export")


def first_json_value(text: str, key: str) -> str:
    m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else ""
