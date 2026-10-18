"""SNMP v2c / v3 — the driver of last resort: any switch, router, AP, NAS or printer with standard MIBs
(SNMPv2-MIB, IF-MIB, IP-MIB, BRIDGE-MIB, Q-BRIDGE-MIB, LLDP-MIB). Used for consumer TP-Link / Netgear / D-Link /
ASUS, RouterOS 6 and anything without an API."""
from __future__ import annotations

import asyncio

from pysnmp.hlapi.v3arch.asyncio import (CommunityData, ContextData, Integer, ObjectIdentity, ObjectType, OctetString,
                                         SnmpEngine, UdpTransportTarget, UsmUserData, bulk_walk_cmd, get_cmd, set_cmd,
                                         usm3DESEDEPrivProtocol, usmAesCfb128Protocol, usmAesCfb256Protocol,
                                         usmDESPrivProtocol, usmHMAC192SHA256AuthProtocol, usmHMAC384SHA512AuthProtocol,
                                         usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol, usmNoAuthProtocol, usmNoPrivProtocol)

from ...domain.errors import ExternalServiceError
from .base import DeviceDriver, Endpoint, Facts, Interface, Neighbor, Snapshot, UnsupportedAction, norm_ip, norm_mac

SYS = "1.3.6.1.2.1.1"
OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0", "sysObjectID": "1.3.6.1.2.1.1.2.0", "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
}
IF = {"descr": "1.3.6.1.2.1.2.2.1.2", "type": "1.3.6.1.2.1.2.2.1.3", "mtu": "1.3.6.1.2.1.2.2.1.4",
      "speed": "1.3.6.1.2.1.2.2.1.5", "mac": "1.3.6.1.2.1.2.2.1.6", "admin": "1.3.6.1.2.1.2.2.1.7",
      "oper": "1.3.6.1.2.1.2.2.1.8", "in": "1.3.6.1.2.1.2.2.1.10", "out": "1.3.6.1.2.1.2.2.1.16",
      "name": "1.3.6.1.2.1.31.1.1.1.1", "hcin": "1.3.6.1.2.1.31.1.1.1.6", "hcout": "1.3.6.1.2.1.31.1.1.1.10",
      "highspeed": "1.3.6.1.2.1.31.1.1.1.15", "alias": "1.3.6.1.2.1.31.1.1.1.18"}
IP_ADDR_IF = "1.3.6.1.2.1.4.20.1.2"
IP_ADDR_MASK = "1.3.6.1.2.1.4.20.1.3"
ARP = "1.3.6.1.2.1.4.22.1.2"
FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
QFDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
BRIDGE_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"
LLDP_LOC_PORT = "1.0.8802.1.1.2.1.3.7.1.3"
LLDP_REM = {"chassis": "1.0.8802.1.1.2.1.4.1.1.5", "port": "1.0.8802.1.1.2.1.4.1.1.7", "portdesc": "1.0.8802.1.1.2.1.4.1.1.8",
            "name": "1.0.8802.1.1.2.1.4.1.1.9", "desc": "1.0.8802.1.1.2.1.4.1.1.10"}
AUTH = {"md5": usmHMACMD5AuthProtocol, "sha": usmHMACSHAAuthProtocol, "sha256": usmHMAC192SHA256AuthProtocol,
        "sha512": usmHMAC384SHA512AuthProtocol}
PRIV = {"des": usmDESPrivProtocol, "3des": usm3DESEDEPrivProtocol, "aes": usmAesCfb128Protocol, "aes256": usmAesCfb256Protocol}

# sysObjectID enterprise number → vendor, for discovery and display
ENTERPRISES = {9: "Cisco", 14988: "MikroTik", 11: "HP", 25506: "HPE (Comware)", 47196: "HPE Aruba", 14823: "Aruba",
               674: "Dell", 6027: "Dell (Force10)", 2636: "Juniper", 2011: "Huawei", 890: "Zyxel", 11863: "TP-Link",
               4526: "Netgear", 171: "D-Link", 41112: "Ubiquiti", 12356: "Fortinet", 35047: "Eltex", 8072: "Linux (net-snmp)",
               6574: "Synology", 30065: "Arista", 1916: "Extreme", 1991: "Brocade", 25053: "Ruckus", 2620: "Check Point",
               25461: "Palo Alto", 12325: "pfSense / FreeBSD", 4413: "Broadcom", 24681: "QNAP", 3955: "Linksys",
               2021: "UC Davis (net-snmp)", 9303: "Pulse", 17713: "Cambium", 10002: "Ubiquiti (UniFi)"}


def _str(v) -> str:
    try:
        raw = v.asOctets()
        try:
            text = raw.decode()
            # Text with line breaks is text (a sysDescr of several lines); other control bytes mean binary data.
            return text if all(c.isprintable() or c in "\t\r\n" for c in text) else raw.hex()
        except UnicodeDecodeError:
            return raw.hex()
    except AttributeError:
        return v.prettyPrint()


def _mac(v) -> str:
    try:
        raw = v.asOctets()
        if len(raw) == 6:
            return ":".join(f"{b:02x}" for b in raw)
    except AttributeError:
        pass
    return norm_mac(v.prettyPrint())


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def vendor_from_oid(oid: str) -> str:
    parts = oid.lstrip(".").split(".")
    if len(parts) > 6 and parts[:6] == ["1", "3", "6", "1", "4", "1"]:
        return ENTERPRISES.get(int(parts[6]), f"enterprise {parts[6]}")
    return ""


class SnmpClient:
    def __init__(self, host: str, port: int, creds, timeout: float = 3, retries: int = 1):
        self.host, self.port, self._timeout, self._retries = host, port, timeout, retries
        self._engine = SnmpEngine()
        if creds.snmp_version == "3":
            self._auth = UsmUserData(creds.snmp_user, creds.snmp_auth_password or None, creds.snmp_priv_password or None,
                                     authProtocol=AUTH.get(creds.snmp_auth_protocol, usmHMACSHAAuthProtocol)
                                     if creds.snmp_auth_password else usmNoAuthProtocol,
                                     privProtocol=PRIV.get(creds.snmp_priv_protocol, usmAesCfb128Protocol)
                                     if creds.snmp_priv_password else usmNoPrivProtocol)
        else:
            self._auth = CommunityData(creds.snmp_community or "public", mpModel=0 if creds.snmp_version == "1" else 1)
        self._target = None

    async def _t(self):
        if self._target is None:
            self._target = await UdpTransportTarget.create((self.host, self.port), timeout=self._timeout, retries=self._retries)
        return self._target

    async def get(self, *oids: str) -> dict[str, object]:
        err, status, _, binds = await get_cmd(self._engine, self._auth, await self._t(), ContextData(),
                                              *(ObjectType(ObjectIdentity(o)) for o in oids))
        if err:
            raise ExternalServiceError(f"{self.host}: SNMP: {err}")
        if status:
            raise ExternalServiceError(f"{self.host}: SNMP: {status.prettyPrint()}")
        return {str(b[0]): b[1] for b in binds if b[1].__class__.__name__ not in ("NoSuchObject", "NoSuchInstance")}

    async def walk(self, oid: str, limit: int = 20000) -> dict[str, object]:
        """Returns {index suffix: value} under the subtree."""
        out: dict[str, object] = {}
        prefix = oid + "."
        async for err, status, _, binds in bulk_walk_cmd(self._engine, self._auth, await self._t(), ContextData(), 0, 40,
                                                         ObjectType(ObjectIdentity(oid)), lexicographicMode=False):
            if err:
                if out:
                    break
                raise ExternalServiceError(f"{self.host}: SNMP: {err}")
            if status:
                break
            for name, value in binds:
                key = str(name)
                if not key.startswith(prefix):
                    return out
                out[key[len(prefix):]] = value
            if len(out) >= limit:
                break
        return out

    async def set(self, oid: str, value) -> None:
        err, status, _, _ = await set_cmd(self._engine, self._auth, await self._t(), ContextData(),
                                          ObjectType(ObjectIdentity(oid), value))
        if err or status:
            raise ExternalServiceError(f"{self.host}: SNMP SET: {err or status.prettyPrint()} "
                                       "(a community / user with write access is required)")

    def close(self) -> None:
        try:
            self._engine.close_dispatcher()
        except Exception:
            pass


class SnmpDriver(DeviceDriver):
    capabilities = frozenset({"hostname", "interface"})
    default_transport = "snmp"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.snmp = SnmpClient(self.target.host, self.target.port if self.target.transport == "snmp" and self.target.port else 161,
                               self.creds)

    async def collect(self) -> Snapshot:
        s = await self.snmp.get(*OIDS.values())
        by = {k: s.get(v) for k, v in OIDS.items()}
        descr = _str(by["sysDescr"]) if by["sysDescr"] is not None else ""
        oid = by["sysObjectID"].prettyPrint() if by["sysObjectID"] is not None else ""
        ticks = _int(by["sysUpTime"])
        facts = Facts(hostname=_str(by["sysName"]) if by["sysName"] is not None else "",
                      vendor=self.vendor or vendor_from_oid(oid), model=descr.split("\n")[0][:120], os="SNMP",
                      uptime_seconds=ticks // 100 if ticks is not None else None, extra={"sysObjectID": oid, "sysDescr": descr[:500]})
        cols = dict(zip(IF, await asyncio.gather(*(self.snmp.walk(o) for o in IF.values()), return_exceptions=True)))
        cols = {k: (v if isinstance(v, dict) else {}) for k, v in cols.items()}
        addrs: dict[str, list[str]] = {}
        ip_if, ip_mask = await self.snmp.walk(IP_ADDR_IF), await self.snmp.walk(IP_ADDR_MASK)
        for ip, ifidx in ip_if.items():
            mask = ip_mask.get(ip)
            bits = sum(bin(int(x)).count("1") for x in mask.prettyPrint().split(".")) if mask is not None else 32
            addrs.setdefault(str(_int(ifidx)), []).append(f"{ip}/{bits}")
        interfaces = []
        names: dict[str, str] = {}
        for idx, d in cols["descr"].items():
            name = _str(cols["name"][idx]) if idx in cols["name"] else _str(d)
            names[idx] = name
            hs = _int(cols["highspeed"].get(idx))
            sp = _int(cols["speed"].get(idx))
            rx = _int(cols["hcin"].get(idx)) if idx in cols["hcin"] else _int(cols["in"].get(idx))
            tx = _int(cols["hcout"].get(idx)) if idx in cols["hcout"] else _int(cols["out"].get(idx))
            interfaces.append(Interface(name=name, index=int(idx), description=_str(cols["alias"][idx]) if idx in cols["alias"] else "",
                                        type=str(_int(cols["type"].get(idx)) or ""), mac=_mac(cols["mac"][idx]) if idx in cols["mac"] else "",
                                        admin_up=_int(cols["admin"].get(idx)) == 1, oper_up=_int(cols["oper"].get(idx)) == 1,
                                        mtu=_int(cols["mtu"].get(idx)), speed_mbps=hs or (sp // 1_000_000 if sp else None) or None,
                                        addresses=addrs.get(idx, []), rx_bytes=rx, tx_bytes=tx))
        facts.mac = next((i.mac for i in interfaces if i.mac and i.type == "6"), "")

        neighbors = []
        loc = {k: _str(v) for k, v in (await self.snmp.walk(LLDP_LOC_PORT)).items()}
        rem = {k: await self.snmp.walk(v) for k, v in LLDP_REM.items()}
        for key, name in rem["name"].items():  # key = timemark.localPort.index
            parts = key.split(".")
            local = loc.get(parts[1], parts[1]) if len(parts) >= 3 else ""
            neighbors.append(Neighbor(local_interface=local, remote_name=_str(name),
                                      remote_mac=_mac(rem["chassis"][key]) if key in rem["chassis"] else "",
                                      remote_interface=_str(rem["port"].get(key)) if key in rem["port"] else "",
                                      remote_platform=_str(rem["desc"].get(key))[:120] if key in rem["desc"] else "",
                                      protocol="lldp"))
        endpoints = []
        for key, mac in (await self.snmp.walk(ARP)).items():  # ifIndex.a.b.c.d
            parts = key.split(".")
            if len(parts) == 5 and _mac(mac):
                endpoints.append(Endpoint(mac=_mac(mac), ip=norm_ip(".".join(parts[1:])), interface=names.get(parts[0], ""),
                                          source="arp"))
        bridge_if = {k: str(_int(v)) for k, v in (await self.snmp.walk(BRIDGE_IFINDEX)).items()}
        uplinks = {n.local_interface for n in neighbors}
        fdb = await self.snmp.walk(QFDB_PORT) or await self.snmp.walk(FDB_PORT)
        for key, port in fdb.items():  # [vlan.]m1.m2.m3.m4.m5.m6
            parts = key.split(".")
            if len(parts) < 6:
                continue
            mac = ":".join(f"{int(x):02x}" for x in parts[-6:])
            vlan = int(parts[0]) if len(parts) == 7 else None
            iface = names.get(bridge_if.get(str(_int(port)), ""), "")
            if iface and iface not in uplinks and _int(port):
                endpoints.append(Endpoint(mac=mac, interface=iface, vlan=vlan, source="bridge"))
        return Snapshot(facts, interfaces, neighbors, endpoints)

    async def apply(self, action: str, p: dict) -> str:
        if action == "hostname":
            await self.snmp.set(OIDS["sysName"], OctetString(p["name"]))
            return "Done"
        if action == "interface" and p.get("enabled") is not None:
            names = {_str(v): k for k, v in (await self.snmp.walk(IF["name"])).items()} or \
                {_str(v): k for k, v in (await self.snmp.walk(IF["descr"])).items()}
            idx = names.get(p["name"])
            if not idx:
                raise ExternalServiceError(f"Interface {p['name']} not found")
            await self.snmp.set(f"{IF['admin']}.{idx}", Integer(1 if p["enabled"] else 2))
            if p.get("description") is not None:
                await self.snmp.set(f"{IF['alias']}.{idx}", OctetString(p["description"]))
            return "Done"
        raise UnsupportedAction(self.vendor or "SNMP", action)

    async def close(self) -> None:
        self.snmp.close()
