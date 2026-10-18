"""Finds network devices and guesses their platform by SSH banner, web interface and SNMP sysObjectID."""
from __future__ import annotations

import asyncio
import ipaddress
import re

import httpx

from .base import Credentials
from .snmp import SnmpClient, _str, vendor_from_oid

SSH_HINTS = [(r"ROSSSH", "mikrotik"), (r"Cisco", "cisco_ios"), (r"HUAWEI|VRP", "huawei"), (r"Comware", "hp_comware"),
             (r"FortiSSH|Fortinet", "fortigate"), (r"Zyxel|ZyXEL", "zyxel"), (r"dropbear", "openwrt"),
             (r"OpenSSH.*(Debian|Ubuntu|Raspbian|FreeBSD)", "linux"), (r"lancom", "snmp")]
WEB_HINTS = [(r"RouterOS|mikrotik", "mikrotik"), (r"OPNsense", "opnsense"), (r"pfSense", "pfsense"),
             (r"LuCI|OpenWrt", "openwrt"), (r"Keenetic", "keenetic"), (r"Omada", "omada"), (r"UniFi|ubnt", "unifi"),
             (r"FortiGate|Fortinet", "fortigate"), (r"Zyxel|ZyXEL", "zyxel"), (r"TP-?Link|Archer|Deco", "tplink"),
             (r"iDRAC|Integrated Lights-Out|iLO|XClarity|Redfish", "redfish"), (r"Aruba|ArubaOS", "aruba_cx"),
             (r"ProCurve", "aruba_os"), (r"Netgear", "netgear"), (r"D-Link", "dlink"), (r"EdgeOS|EdgeRouter", "edgeos"),
             (r"Synology|DiskStation", "linux"), (r"Proxmox", "linux"), (r"Cisco", "cisco_ios"), (r"Huawei", "huawei"),
             (r"JUNOS|Juniper", "juniper"), (r"Eltex", "eltex"), (r"Dell", "dell_os10")]
SNMP_HINTS = {"MikroTik": "mikrotik", "Cisco": "cisco_ios", "HP": "aruba_os", "HPE (Comware)": "hp_comware",
              "HPE Aruba": "aruba_cx", "Aruba": "aruba_os", "Dell": "dell_nseries", "Dell (Force10)": "dell_os10",
              "Juniper": "juniper", "Huawei": "huawei", "Zyxel": "zyxel", "TP-Link": "tplink", "Netgear": "netgear",
              "D-Link": "dlink", "Fortinet": "fortigate", "Eltex": "eltex", "Ubiquiti": "edgeos",
              "Linux (net-snmp)": "linux", "Synology": "linux"}
PORTS = (22, 80, 443, 8043, 8443)


async def _open(host: str, port: int, timeout: float = 1.5) -> bool | str:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    banner = ""
    if port == 22:
        try:
            banner = (await asyncio.wait_for(reader.readline(), 2)).decode(errors="replace").strip()
        except (OSError, asyncio.TimeoutError):
            pass
    writer.close()
    return banner or True


async def _web(host: str, port: int) -> tuple[str, dict]:
    scheme = "http" if port == 80 else "https"
    # Only the banner of a device is read, with no credentials: devices answer with self-signed certificates, and
    # discovery has to see them before anyone could trust their certificates.
    async with httpx.AsyncClient(verify=False, timeout=4, follow_redirects=True) as http:  # NOSONAR
        base = f"{scheme}://{host}:{port}"
        extra: dict = {}
        try:
            r = await http.get(base + "/")
            text = " ".join([r.headers.get("server", ""), r.headers.get("www-authenticate", ""), r.text[:4000]])
        except httpx.HTTPError:
            return "", extra
        for path, key in (("/auth", "keenetic"), ("/api/info", "omada"), ("/redfish/v1", "redfish")):
            try:
                rr = await http.get(base + path)
            except httpx.HTTPError:
                continue
            if key == "keenetic" and rr.headers.get("X-NDM-Realm"):
                text += " Keenetic"
            elif key == "omada" and "omadacId" in rr.text:
                text += " Omada"
            elif key == "redfish" and "RedfishVersion" in rr.text:
                text += " Redfish"
        return text, extra


async def probe(host: str, community: str = "public") -> dict:
    """Open ports, banners and platform guesses for one address."""
    results = await asyncio.gather(*(_open(host, p) for p in PORTS))
    ports = {p: r for p, r in zip(PORTS, results) if r}
    guesses: dict[str, int] = {}
    evidence: list[str] = []
    banner = ports.get(22)
    if isinstance(banner, str):
        evidence.append(banner)
        for pattern, key in SSH_HINTS:
            if re.search(pattern, banner, re.I):
                guesses[key] = guesses.get(key, 0) + 2
    for port in (443, 80, 8043, 8443):
        if port in ports:
            text, _ = await _web(host, port)
            for pattern, key in WEB_HINTS:
                if re.search(pattern, text, re.I):
                    guesses[key] = guesses.get(key, 0) + 3
                    evidence.append(f"web:{port} {key}")
                    break
    snmp = {}
    try:
        client = SnmpClient(host, 161, Credentials(snmp_community=community), timeout=1, retries=0)
        try:
            got = await client.get("1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0", "1.3.6.1.2.1.1.5.0")
        finally:
            client.close()
        vals = list(got.values())
        if vals:
            oid = got.get("1.3.6.1.2.1.1.2.0")
            vendor = vendor_from_oid(oid.prettyPrint()) if oid is not None else ""
            snmp = {"descr": _str(vals[0])[:200], "vendor": vendor,
                    "name": _str(got["1.3.6.1.2.1.1.5.0"]) if "1.3.6.1.2.1.1.5.0" in got else ""}
            if vendor in SNMP_HINTS:
                guesses[SNMP_HINTS[vendor]] = guesses.get(SNMP_HINTS[vendor], 0) + 3
            for pattern, key in WEB_HINTS:
                if re.search(pattern, snmp["descr"], re.I):
                    guesses[key] = guesses.get(key, 0) + 2
                    break
    except Exception:
        pass
    ranked = [k for k, _ in sorted(guesses.items(), key=lambda kv: -kv[1])]
    if not ranked and snmp:
        ranked = ["snmp"]
    return {"host": host, "reachable": bool(ports or snmp), "ports": sorted(ports), "evidence": evidence[:6],
            "snmp": snmp, "platforms": ranked[:4], "name": snmp.get("name", "")}


async def scan(target: str, community: str = "public", limit: int = 1024) -> list[dict]:
    """Probes every address of a subnet (at most /22) and returns those that answered."""
    net = ipaddress.ip_network(target, strict=False)
    if net.num_addresses > limit:
        raise ValueError(f"The network is too large: no more than {limit} addresses")
    hosts = [str(h) for h in (net.hosts() if net.num_addresses > 1 else [net.network_address])]
    sem = asyncio.Semaphore(48)

    async def one(h: str):
        async with sem:
            return await probe(h, community)

    found = await asyncio.gather(*(one(h) for h in hosts))
    return [f for f in found if f["reachable"]]
