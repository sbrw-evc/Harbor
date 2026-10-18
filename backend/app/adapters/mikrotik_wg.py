from __future__ import annotations

import ipaddress
import re
import time

from ..domain.errors import ExternalServiceError
from ..domain.models import PeerEntry, PeerRuntime
from ..ports import CommandRunner

# Router-side marker kept from the Amnezia Control era: existing WireGuard objects are found by it.
COMMENT = "amnezia-control"
_KEY = re.compile(r"[A-Za-z0-9+/]{42,43}=")
_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1, "ms": 0}
_ERROR = re.compile(r"^\s*(failure:|syntax error|bad command|expected |input does not match|no such item|"
                    r"invalid value|ambiguous value)", re.M)


def parse_duration(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    total = 0
    clock = re.search(r"(\d++):(\d++):(\d++)", value)
    if clock:
        h, m, s = (int(x) for x in clock.groups())
        total += h * 3600 + m * 60 + s
        value = value[:clock.start()]
    for n, unit in re.findall(r"(\d++)(ms|w|d|h|m|s)", value):
        total += int(n) * _UNITS[unit]
    return total


def safe_comment(name: str) -> str:
    return f"{COMMENT}: " + (re.sub(r"[^A-Za-z0-9 _.@-]", "_", name).strip()[:48] or "client")


def _key(value: str) -> str:
    if not _KEY.fullmatch(value):
        raise ValueError("bad wireguard key")
    return value


class RouterOsRunner:
    def __init__(self, runner: CommandRunner):
        self._ssh = runner

    async def run(self, command: str, timeout: float = 30) -> str:
        out = await self._ssh.run(command, timeout=timeout)
        m = _ERROR.search(out)
        if m:
            raise ExternalServiceError(f"RouterOS: {out[m.start():].strip().splitlines()[0]}")
        return out


class MikrotikWireguard:
    def __init__(self, runner: CommandRunner, iface: str, network: str, port: int, mtu: int = 1420):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", iface):
            raise ValueError("bad interface name")
        self._ssh = RouterOsRunner(runner)
        self.iface = iface
        self.network = ipaddress.ip_network(network)
        self.server_ip = next(self.network.hosts())
        self.port = int(port)
        self.mtu = int(mtu)

    def _find(self) -> str:
        return f'[/interface wireguard peers find where interface="{self.iface}"]'

    def _peer(self, public_key: str) -> str:
        return f'[/interface wireguard peers find where interface="{self.iface}" and public-key="{_key(public_key)}"]'

    async def interface(self) -> dict | None:
        out = await self._ssh.run(
            f':local i [/interface wireguard find where name="{self.iface}"]; '
            f':if ([:len $i] > 0) do={{ :local r [/interface wireguard get $i]; '
            f':put ("key=" . ($r->"public-key")); :put ("port=" . ($r->"listen-port")); '
            f':put ("running=" . ($r->"running")); :put ("disabled=" . ($r->"disabled")); '
            f':put ("mtu=" . ($r->"mtu")) }}')
        info = dict(line.strip().split("=", 1) for line in out.splitlines() if "=" in line)
        if not info.get("key"):
            return None
        return {"public_key": info["key"], "listen_port": int(info.get("port") or 0),
                "running": info.get("running") == "true", "disabled": info.get("disabled") == "true",
                "mtu": int(info.get("mtu") or 0)}

    async def public_address(self) -> str:
        out = await self._ssh.run(
            ':local a [/ip cloud get public-address]; :if ([:len $a] = 0) do={ '
            ':foreach i in=[/ip address find where interface~"WAN" and dynamic] do={ '
            ':set a [:pick [/ip address get $i address] 0 [:find [/ip address get $i address] "/"]] } }; :put $a')
        return out.strip()

    async def setup(self) -> None:
        ip, port, iface = self.server_ip, self.port, self.iface
        first = ':local first [:pick [/ip firewall filter find where chain="input" and dynamic=no] 0]; '

        def input_rule(comment: str, spec: str) -> str:
            return (f':if ([:len [/ip firewall filter find where comment="{comment}"]] = 0) do={{ {first}'
                    f':if ([:len $first] > 0) do={{ /ip firewall filter add chain=input action=accept {spec} '
                    f'comment="{comment}" place-before=$first }} else={{ /ip firewall filter add chain=input '
                    f'action=accept {spec} comment="{comment}" }} }}; ')

        def mangle(comment: str, spec: str) -> str:
            return (f':if ([:len [/ip firewall mangle find where comment="{comment}"]] = 0) do={{ '
                    f'/ip firewall mangle add chain=prerouting {spec} in-interface={iface} comment="{comment}" }}; ')

        await self._ssh.run(
            f':if ([:len [/interface wireguard find where name="{iface}"]] = 0) do={{ '
            f'/interface wireguard add name={iface} listen-port={port} mtu={self.mtu} comment="{COMMENT}" }} '
            f'else={{ /interface wireguard set [find where name="{iface}"] listen-port={port} disabled=no }}; '
            f':if ([:len [/ip address find where interface="{iface}"]] = 0) do={{ '
            f'/ip address add address={ip}/{self.network.prefixlen} interface={iface} comment="{COMMENT}" }}; '
            + input_rule("amnezia: wireguard", f"protocol=udp dst-port={port}")
            + input_rule("amnezia: wireguard dns udp", f"protocol=udp dst-port=53 in-interface={iface}")
            + input_rule("amnezia: wireguard dns tcp", f"protocol=tcp dst-port=53 in-interface={iface}")
            + mangle("amnezia: mark new wg conns to vpn-sites",
                     "action=mark-connection new-connection-mark=to-vpn passthrough=yes connection-state=new "
                     "dst-address-list=vpn-sites")
            + mangle("amnezia: route wg conns via tunnel",
                     "action=mark-routing new-routing-mark=to-vpn passthrough=no dst-address-list=vpn-sites "
                     "connection-mark=to-vpn"), timeout=60)

    async def peers(self) -> list[dict]:
        out = await self._ssh.run(
            f':foreach p in={self._find()} do={{ :local r [/interface wireguard peers get $p]; '
            ':put (($r->"public-key") . "|" . ($r->"current-endpoint-address") . "|" . '
            '($r->"current-endpoint-port") . "|" . ($r->"last-handshake") . "|" . ($r->"rx") . "|" . ($r->"tx") . "|" . '
            '($r->"allowed-address") . "|" . ($r->"disabled") . "|" . ($r->"comment")) }', timeout=30)
        now = time.time()
        res = []
        for line in out.splitlines():
            f = line.strip().split("|", 8)
            if len(f) < 9 or not f[0]:
                continue
            ago = parse_duration(f[3])
            endpoint = f"{f[1]}:{f[2]}" if f[1] and f[1] != "0.0.0.0" else None
            res.append({"public_key": f[0], "endpoint": endpoint,
                        "latest_handshake": int(now - ago) if ago is not None else 0,
                        "rx": int(f[4] or 0), "tx": int(f[5] or 0), "address": f[6].replace(";", ","),
                        "disabled": f[7] == "true", "comment": f[8]})
        return res

    async def runtime(self) -> dict[str, PeerRuntime]:
        return {p["public_key"]: PeerRuntime(public_key=p["public_key"], endpoint=p["endpoint"],
                                             allowed_ips=p["address"], latest_handshake=p["latest_handshake"],
                                             rx=p["rx"], tx=p["tx"])
                for p in await self.peers()}

    async def peer_entries(self) -> list[PeerEntry]:
        return [PeerEntry(p["public_key"], p["address"], None, p["disabled"]) for p in await self.peers()]

    async def add_peer(self, public_key: str, psk: str, address: str, name: str) -> None:
        ipaddress.ip_interface(address)
        await self._ssh.run(
            f'/interface wireguard peers add interface={self.iface} public-key="{_key(public_key)}" '
            f'preshared-key="{_key(psk)}" allowed-address={address} comment="{safe_comment(name)}"')

    async def set_peer(self, public_key: str, *, disabled: bool | None = None, new_key: str | None = None,
                       name: str | None = None) -> None:
        parts = []
        if disabled is not None:
            parts.append(f"disabled={'yes' if disabled else 'no'}")
        if new_key:
            parts.append(f'public-key="{_key(new_key)}"')
        if name is not None:
            parts.append(f'comment="{safe_comment(name)}"')
        if parts:
            await self._ssh.run(f"/interface wireguard peers set {self._peer(public_key)} {' '.join(parts)}")

    async def remove_peer(self, public_key: str) -> None:
        await self._ssh.run(f"/interface wireguard peers remove {self._peer(public_key)}")
