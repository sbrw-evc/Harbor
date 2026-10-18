"""Parsers of network OS CLI output (Cisco IOS / IOS-XE / NX-OS, Dell OS10 / N-series, HPE Comware, Huawei VRP,
ArubaOS-Switch / CX, Zyxel, TP-Link JetStream, Eltex). They are deliberately tolerant: vendors and versions
differ in spacing and wording, so each parser looks for patterns rather than exact layouts."""
from __future__ import annotations

import re

from .base import Endpoint, Interface, Neighbor, norm_ip, norm_mac, to_int

IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
# Possessive quantifiers: none of the parts gives characters back, so a long token cannot make the match backtrack.
IFACE_TOKEN = re.compile(r"^(?:[A-Za-z][A-Za-z\-]*+\s?\d[\w/.:\-]*+|\d++(?:/\d++)*+|[A-Za-z]++\d++[\w/.:\-]*+|Po\d++|lag\d++)$")
NOT_IFACE = {"dynamic", "static", "learned", "arpa", "self", "cpu", "management", "permanent", "delete-on-reset",
             "secure", "drop", "router", "sticky", "d", "s", "l", "i", "igmp", "config", "mgmt"}
DURATION_UNITS = {"year": 31536000, "week": 604800, "day": 86400, "hour": 3600, "minute": 60, "second": 1}


def parse_uptime(text: str) -> int | None:
    """'1 year, 2 weeks, 3 days, 4 hours, 5 minutes' or '10 days 3:04:05' → seconds."""
    if not text:
        return None
    total = 0
    found = False
    for n, unit in re.findall(r"(\d++)\s*+(year|week|day|hour|minute|second)s?", text, re.I):
        total += int(n) * DURATION_UNITS[unit.lower()]
        found = True
    clock = re.search(r"(\d++):(\d{2}):(\d{2})", text)
    if clock:
        h, m, s = (int(x) for x in clock.groups())
        total += h * 3600 + m * 60 + s
        found = True
    return total if found else None


def first(pattern: str, text: str, flags=re.I | re.M, group: int = 1, default: str = "") -> str:
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else default


# ---------- facts ----------

def parse_version(text: str) -> dict:
    """Facts from `show version` / `display version` / `show system` of the common network OSes."""
    out = {
        "hostname": first(r"^(\S+)\s+uptime is", text) or first(r"^\s*(?:System Name|Hostname|Host Name|sysname)\s*[:=]\s*(.+)$", text),
        "version": first(r"(?:Cisco IOS.*?|NX-OS.*?|Software)[, ]+Version\s+([^\s,]+)", text)
        or first(r"^\s*(?:OS Version|Software Version|Firmware Version|Software revision|ROM Version|Version)\s*[:]\s*(\S+)", text)
        or first(r"Version\s+(\d[\w.()\-]*)", text),
        "model": first(r"^\s*(?:Model number|Model Number|Model Name|Product Name|Model|System Type|Device Type|Hardware)\s*[:]\s*(.+)$", text)
        or first(r"^cisco\s+(\S+).*processor", text) or first(r"^(?:HPE?|H3C|HUAWEI)\s+(\S+(?:\s\S+)?)\s+uptime is", text),
        "serial": first(r"^\s*(?:System serial number|Serial Number|Serial No|Serial-Number|Processor board ID|Device serial number|Chassis Serial Number)\s*[:]?\s*(\S+)", text),
        "mac": norm_mac(first(r"(?:Base (?:ethernet )?MAC (?:Address|Addr)|MAC Address|System MAC)\s*[:]\s*(\S+)", text)),
    }
    up = first(r"uptime is\s+(.+)$", text) or first(r"^\s*(?:Up\s?Time|System Up Time|Uptime)\s*[:]\s*(.+)$", text)
    out["uptime_seconds"] = parse_uptime(up)
    return out


# ---------- interfaces ----------

def parse_show_interfaces(text: str) -> list[Interface]:
    """Full `show interface(s)` of IOS / IOS-XE / NX-OS / OS10 / Eltex / Aruba CX."""
    blocks = re.split(r"(?m)^(?=\S+ is (?:administratively down|up|down|deleted|disabled))", text)
    result = []
    for b in blocks:
        head = re.match(r"(\S+) is (administratively down|up|down|disabled)(?:.*?line protocol is (\w+))?", b)
        if not head:
            continue
        name, state, proto = head.group(1), head.group(2), head.group(3)
        admin_state = first(r"admin state is (\w+)", b)
        admin_up = state != "administratively down" and admin_state.lower() != "down" and state != "disabled"
        oper_up = (proto or state) == "up"
        bw = to_int(first(r"BW (\d+) Kbit", b), 0)
        speed = to_int(first(r"(\d+)\s*(?:Mb/s|Mbps)", b), 0) or (bw // 1000 if bw else 0)
        if not speed:
            g = first(r"(\d+)\s*Gb/s", b)
            speed = int(g) * 1000 if g else 0
        rx = first(r"(\d+) packets input, (\d+) bytes", b, group=2) or first(r"(\d+) input packets\s+(\d+) bytes", b, group=2) \
            or first(r"Input statistics:\s*\n?\s*(\d+) packets, (\d+) octets", b, group=2)
        tx = first(r"(\d+) packets output, (\d+) bytes", b, group=2) or first(r"(\d+) output packets\s+(\d+) bytes", b, group=2) \
            or first(r"Output statistics:\s*\n?\s*(\d+) packets, (\d+) octets", b, group=2)
        addrs = re.findall(r"Internet address is (\S+)", b)
        result.append(Interface(
            name=name, description=first(r"^\s*Description:\s*(.*)$", b), type=first(r"Hardware(?: is|:)\s*([^,\n]+)", b),
            mac=norm_mac(first(r"address(?: is|:)\s*([0-9a-fA-F.:\-]{12,17})", b)), admin_up=admin_up, oper_up=oper_up,
            speed_mbps=speed or None, mtu=to_int(first(r"MTU (\d+)", b), 0) or None, addresses=addrs,
            rx_bytes=to_int(rx, None) if rx else None, tx_bytes=to_int(tx, None) if tx else None))
    return result


def parse_status_table(text: str) -> list[Interface]:
    """Brief tables: `show interfaces status`, `display interface brief`, `show interface brief`, `show interfaces brief`.

    The first token of a row is the port; state words anywhere in the row give admin / oper state; a speed token
    (1000, 1G, 10G(a), a-1000, 1000FDx) gives the speed."""
    result = []
    for line in text.splitlines():
        tokens = line.replace("|", " ").split()
        if len(tokens) < 2 or not IFACE_TOKEN.match(tokens[0]) or tokens[0].lower() in ("port", "interface"):
            continue
        low = [t.lower() for t in tokens[1:]]
        if not any(t in low for t in ("up", "down", "connected", "notconnect", "notconnected", "disabled", "err-disabled",
                                      "*down", "adm", "admdown", "inactive", "yes", "no", "sfp-absent")):
            continue
        admin_up = not any(t in low for t in ("disabled", "*down", "adm", "admdown", "administratively"))
        oper_up = any(t in low for t in ("up", "connected")) and admin_up
        speed = None
        for t in low:
            m = re.fullmatch(r"(?:a-)?(\d+)(g|m)?(?:\(a\))?(?:fdx|hdx|-full|-half|bps|mbps)?", t)
            if not m:
                continue
            n = int(m.group(1)) * (1000 if m.group(2) == "g" else 1)
            if n in (10, 100, 1000, 2500, 5000, 10000, 25000, 40000, 50000, 100000):
                speed = n
                break
        result.append(Interface(name=tokens[0], admin_up=admin_up, oper_up=oper_up, speed_mbps=speed))
    return result


# ---------- neighbours ----------

def parse_cdp_detail(text: str) -> list[Neighbor]:
    out = []
    for b in re.split(r"(?m)^-{5,}\s*$", text):
        name = first(r"Device ID:\s*(\S+)", b)
        local = first(r"Interface:\s*([^,\n]+),", b)
        if not name or not local:
            continue
        out.append(Neighbor(local_interface=local.strip(), remote_name=name.split("(")[0],
                            remote_ip=norm_ip(first(r"(?:IP(?:v4)? [Aa]ddress|IP address):\s*(\S+)", b)),
                            remote_interface=first(r"Port ID \(outgoing port\):\s*(\S+)", b),
                            remote_platform=first(r"Platform:\s*([^,\n]+)", b), protocol="cdp"))
    return out


def parse_lldp_detail(text: str) -> list[Neighbor]:
    """`show lldp neighbors detail` (IOS, NX-OS, OS10, Eltex), `display lldp neighbor-information` (Comware, VRP),
    `show lldp neighbor-info detail` (Aruba CX), `show lldp info remote-device <port>` (ArubaOS-Switch)."""
    out = []
    parts = re.split(r"(?mi)^(?=\s*(?:-{5,}|Local Intf:|Local Port id:|Local Port\s*:|LLDP neighbor-information of port|"
                     r"Interface\s+\S+ has \d+ neighbou?r|Port\s*:\s*\S+\s*$|Local Interface\s*:))", text)
    for b in parts:
        local = first(r"^\s*(?:Local Intf|Local Port id|Local Port|Local Interface)\s*:\s*(\S+)", b) \
            or first(r"LLDP neighbor-information of port \d+\[(\S+)\]", b) or first(r"Interface\s+(\S+) has \d+ neighbou?r", b) \
            or first(r"^\s*Port\s*:\s*(\S+)\s*$", b)
        chassis = first(r"Chassis (?:id|ID)\s*:\s*(\S+)", b)
        sysname = first(r"(?:System Name|SysName|System name|Neighbor System Name)\s*:\s*(.+)$", b)
        if not local or not (chassis or sysname):
            continue
        mgmt = first(r"(?:Management Address(?:es)?|Mgmt address|Management address)\s*(?:\(\w+\))?\s*:?\s*(?:\n\s*)?(?:IP(?:v4)?\s*:\s*)?(\d{1,3}(?:\.\d{1,3}){3})", b)
        out.append(Neighbor(local_interface=local, remote_name=sysname.strip('"'), remote_mac=norm_mac(chassis),
                            remote_ip=norm_ip(mgmt), protocol="lldp",
                            remote_interface=first(r"^\s*Port (?:id|ID)\s*:\s*(\S+)", b),
                            remote_platform=first(r"System Description\s*:\s*(?:\n\s*)?(.+)$", b)[:120]))
    return out


def parse_columns(text: str, columns: dict[str, str]) -> list[dict]:
    """Fixed-width table: finds the header row containing the given labels and slices rows by header positions.

    columns maps a header label (case-insensitive) to a field name."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        low = line.lower()
        found = [(low.find(label.lower()), label) for label in columns if label.lower() in low]
        if len(found) < max(2, len(columns) - 1):
            continue
        found.sort()
        rows = []
        for row in lines[idx + 1:]:
            if not row.strip() or set(row.strip()) <= set("-+= |"):
                continue
            rec = {}
            for i, (pos, label) in enumerate(found):
                end = found[i + 1][0] if i + 1 < len(found) else None
                rec[columns[label]] = row[pos:end].strip(" |") if len(row) > pos else ""
            rows.append(rec)
        return rows
    return []


def parse_lldp_table(text: str) -> list[Neighbor]:
    """Short LLDP tables (Comware list, VRP brief, ArubaOS-Switch remote-device, JetStream, Zyxel, Eltex)."""
    for cols in (
        {"System Name": "name", "Local Interface": "local", "Chassis ID": "chassis", "Port ID": "port"},
        {"Local Intf": "local", "Neighbor Dev": "name", "Neighbor Intf": "port"},
        {"LocalPort": "local", "ChassisId": "chassis", "PortId": "port", "SysName": "name"},
        {"Local Port": "local", "Device ID": "name", "Port ID": "port"},
        {"Port": "local", "Device ID": "chassis", "Port ID": "port", "System Name": "name"},
        {"Local Port": "local", "Chassis ID": "chassis", "Port ID": "port", "System Name": "name"},
    ):
        rows = parse_columns(text, cols)
        if rows:
            return [Neighbor(local_interface=r.get("local", ""), remote_name=r.get("name", ""),
                             remote_mac=norm_mac(r.get("chassis")), remote_interface=r.get("port", ""), protocol="lldp")
                    for r in rows if r.get("local") and (r.get("name") or r.get("chassis"))]
    return []


# ---------- ARP and MAC tables ----------

def _iface_tokens(tokens: list[str], skip: set[str]) -> list[str]:
    return [t for t in tokens if t not in skip and t.lower() not in NOT_IFACE and IFACE_TOKEN.match(t)
            and not IPV4.fullmatch(t) and not norm_mac(t)]


def parse_arp(text: str) -> list[Endpoint]:
    """Any ARP table: a row with an IPv4 address and a MAC; the interface is the last interface-like token."""
    out = []
    for line in text.splitlines():
        tokens = line.split()
        ip = next((t for t in tokens if IPV4.fullmatch(t)), "")
        mac = next((norm_mac(t) for t in tokens if norm_mac(t)), "")
        if not ip or not mac or mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
            continue
        ifaces = [t for t in _iface_tokens(tokens, {ip}) if not t.isdigit() or "/" in t]
        out.append(Endpoint(mac=mac, ip=norm_ip(ip), interface=ifaces[-1] if ifaces else "", source="arp"))
    return out


def parse_mac_table(text: str, uplinks: set[str] | None = None) -> list[Endpoint]:
    """Any MAC address table: a row with a MAC, a VLAN number and a port. Rows on uplink ports are dropped."""
    uplinks = {u.lower() for u in uplinks or set()}
    out = []
    for line in text.splitlines():
        tokens = line.split()
        macs = [t for t in tokens if norm_mac(t)]
        if not macs:
            continue
        low = line.lower()
        mac = norm_mac(macs[0])
        rest = [t for t in tokens if t != macs[0]]
        vlan = next((int(t) for t in rest if t.isdigit() and 0 < int(t) < 4095), None)
        ports = [t for t in _iface_tokens(rest, set()) if not (t.isdigit() and vlan is not None and int(t) == vlan)]
        if not ports:
            continue
        port = ports[-1]
        if port.lower() in uplinks or "cpu" in low.split():
            continue
        out.append(Endpoint(mac=mac, interface=port, vlan=vlan, source="bridge"))
    return out


def shorten_iface(name: str) -> str:
    """GigabitEthernet1/0/1 → Gi1/0/1 so the same port matches across LLDP, CDP and tables."""
    for long, short in (("TenGigabitEthernet", "Te"), ("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"),
                        ("TwentyFiveGigE", "Twe"), ("HundredGigE", "Hu"), ("FortyGigabitEthernet", "Fo"),
                        ("Ethernet", "Eth"), ("Port-channel", "Po"), ("Bridge-Aggregation", "BAGG"),
                        ("Ten-GigabitEthernet", "XGE"), ("GE", "GE")):
        if name.startswith(long) and long != short:
            return short + name[len(long):]
    return name
