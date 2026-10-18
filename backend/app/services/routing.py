from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
import json
from collections import Counter

import httpx

from ..db.base import Database
from ..db.repositories import SettingRepo
from ..domain.errors import DomainError, ExternalServiceError
from ..domain.models import Severity
from ..domain.net import HOST_RE
from ..ports import RouterGateway
from .alerts import AlertService
from .audit import AuditService
from .common import Actor, current, utcnow

DOH_RESOLVERS = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
CUSTOM_KEY = "_custom_prefixes"
SITE_LIST_KEY = "_site_list"
# The special-purpose ranges of RFC 6890 never go through the tunnel: they are the definition, not addresses to reach.
EXCLUDE = [ipaddress.ip_network(x) for x in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",  # NOSONAR
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/3")]  # NOSONAR


class SiteListCompiler:
    def __init__(self, vpn_server_ip):
        self._vpn_server_ip = vpn_server_ip

    @property
    def _exclude(self) -> list:
        ip = current(self._vpn_server_ip)
        try:
            return EXCLUDE + [ipaddress.ip_network(f"{ip}/32")]
        except ValueError:  # not set yet, or a host name
            return list(EXCLUDE)

    def compile(self, raw: bytes) -> tuple[list[str], dict, list[tuple[str, str, str]]]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise DomainError(f"The file is not JSON: {e}")
        if not isinstance(data, dict):
            raise DomainError("Expected an object {site: {...}}")
        nets, groups, per_site = [], Counter(), []
        for key, site in data.items():
            if not isinstance(site, dict):
                continue
            name, group = site.get("name") or key, site.get("group") or "other"
            groups[group] += 1
            repl = (site.get("replace") or {}).get("cidr4") or {}
            cidrs = [r for c in site.get("cidr4") or [] for r in repl.get(c, [c])]
            own = []
            for x in (site.get("ip4") or []) + cidrs:
                if ":" in x:
                    continue
                try:
                    n = ipaddress.ip_network(x, strict=False)
                except ValueError:
                    continue
                if n.prefixlen >= 8 and not any(n.overlaps(e) for e in self._exclude):
                    own.append(n)
            nets.extend(own)
            per_site.extend((str(n), name, group) for n in ipaddress.collapse_addresses(own))
        collapsed = [str(n) for n in ipaddress.collapse_addresses(nets)]
        coverage = sum(ipaddress.ip_network(n).num_addresses for n in collapsed) / 2 ** 32 * 100
        summary = {"sites": len(data), "prefixes": len(collapsed), "coverage_percent": round(coverage, 2),
                   "groups": dict(groups.most_common()), "site_names": sorted(data.keys())}
        return collapsed, summary, per_site


class RoutingService:
    def __init__(self, db: Database, router: RouterGateway, compiler: SiteListCompiler,
                 alerts: AlertService, audit: AuditService, topology: dict):
        self._db = db
        self._router = router
        self._compiler = compiler
        self._alerts = alerts
        self._audit = audit
        self._topology = topology
        self._list_cache: tuple[float, list] | None = None
        self.flows = None

    async def status(self) -> dict:
        try:
            st = await self._router.status()
        except ExternalServiceError as e:
            return {"reachable": False, "error": e.message, **self._topology}
        return {"reachable": True, **st, **self._topology}

    async def monitor(self) -> None:
        st = await self.status()
        if not st["reachable"]:
            await self._alerts.raise_("router.unreachable", Severity.WARNING, "Router is unreachable over SSH", st["error"])
            return
        await self._alerts.resolve("router.unreachable")
        if st.get("tunnel_active"):
            await self._alerts.resolve("router.tunnel_down")
        else:
            await self._alerts.raise_("router.tunnel_down", Severity.CRITICAL, "Tunnel on the router is down",
                                      "Sites from the list go directly through the ISP (fallback route)")

    async def apply_site_list(self, actor: Actor, raw: bytes) -> dict:
        cidrs, summary, per_site = await asyncio.to_thread(self._compiler.compile, raw)
        if not cidrs:
            raise DomainError("The file has no IPv4 addresses")
        customs = await self.custom_list()
        count = await self._router.replace_address_list(sorted(set(cidrs) | {c["cidr"] for c in customs}))
        self._list_cache = None
        if self.flows:
            await self.flows.load_sites(per_site + self.custom_sites(customs))
        meta = {k: v for k, v in summary.items() if k != "site_names"}
        async with self._db.session() as s:
            await SettingRepo(s).put(SITE_LIST_KEY, summary)
            self._audit.record(s, actor, "routing.site_list_applied", "router", "", "vpn-sites", **meta)
        return {**meta, "applied": count}

    async def site_list(self) -> dict | None:
        async with self._db.session() as s:
            return (await SettingRepo(s).get_all()).get(SITE_LIST_KEY)

    async def custom_list(self) -> list[dict]:
        async with self._db.session() as s:
            return (await SettingRepo(s).get_all()).get(CUSTOM_KEY) or []

    async def _save_custom(self, items: list[dict]) -> None:
        async with self._db.session() as s:
            await SettingRepo(s).put(CUSTOM_KEY, items)

    @staticmethod
    def custom_sites(items: list[dict]) -> list[tuple[str, str, str]]:
        rows = []
        for i in items:
            label = i["comment"] or i["cidr"]
            if i.get("kind") == "domain":
                rows.extend((f"{ip}/32", label, "custom") for ip in i.get("ips") or [])
            else:
                rows.append((i["cidr"], label, "custom"))
        return rows

    @staticmethod
    def _host(target: str) -> str:
        target = target.strip().lower()
        scheme, sep, rest = target.partition("://")  # a pasted link: only its host is wanted
        if sep and scheme in ("http", "https"):
            target = rest
        return target.split("/")[0].split(":")[0].rstrip(".")

    async def _parse_target(self, target: str) -> tuple[str, str, list[str]]:
        try:
            net = ipaddress.ip_network(target.strip(), strict=False)
        except ValueError:
            net = None
        if net is not None:
            if net.version != 4 or any(net.overlaps(e) for e in EXCLUDE):
                raise DomainError("Only public IPv4 addresses are allowed")
            return str(net), "prefix", []
        host = self._host(target)
        if not HOST_RE.fullmatch(host):
            raise DomainError("Enter a domain (example.com), an IPv4 address or a subnet (203.0.113.0/24)")
        ips = await self._public_ips(host)
        if not ips:
            raise DomainError(f"{host} points only to local addresses and cannot be routed through the VPN")
        return host, "domain", ips

    async def add_custom(self, actor: Actor, target: str, comment: str) -> dict:
        cidr, kind, ips = await self._parse_target(target)
        items = await self.custom_list()
        if any(i["cidr"] == cidr for i in items):
            raise DomainError("This address is already added")
        await self._router.add_address(cidr)
        item = {"cidr": cidr, "kind": kind, "comment": comment.strip()[:128], "added_by": actor.username,
                "added_at": utcnow().isoformat()}
        if ips:
            item["ips"] = ips
        await self._save_custom(items + [item])
        self._list_cache = None
        if self.flows:
            await self.flows.add_sites(self.custom_sites([item]))
        async with self._db.session() as s:
            self._audit.record(s, actor, "routing.custom_added", "router", "", cidr, comment=item["comment"], kind=kind)
        return item

    async def _public_ips(self, host: str) -> list[str]:
        return [ip for ip in await self._resolve(host) if not any(ipaddress.ip_address(ip) in e for e in EXCLUDE)]

    async def refresh_domains(self) -> None:
        resolved = {}
        for i in await self.custom_list():
            if i.get("kind") == "domain":
                try:
                    resolved[i["cidr"]] = await self._public_ips(i["cidr"])
                except DomainError:
                    continue
        items, changed = await self.custom_list(), []
        for i in items:
            ips = resolved.get(i["cidr"])
            if ips and set(ips) != set(i.get("ips") or []):
                changed.append((dict(i), i))
                i["ips"] = ips
        if not changed:
            return
        await self._save_custom(items)
        if self.flows:
            for old, new in changed:
                for prefix, _, _ in self.custom_sites([old]):
                    await self.flows.remove_site(prefix)
                await self.flows.add_sites(self.custom_sites([new]))

    async def remove_custom(self, actor: Actor, cidr: str) -> None:
        items = await self.custom_list()
        item = next((i for i in items if i["cidr"] == cidr), None)
        if not item:
            raise DomainError("Address not found")
        await self._router.remove_address(cidr)
        await self._save_custom([i for i in items if i["cidr"] != cidr])
        self._list_cache = None
        if self.flows:
            for prefix, _, _ in self.custom_sites([item]):
                await self.flows.remove_site(prefix)
        async with self._db.session() as s:
            self._audit.record(s, actor, "routing.custom_removed", "router", "", cidr)

    async def _router_networks(self) -> list:
        now = time.monotonic()
        if not self._list_cache or now - self._list_cache[0] > 120:
            nets = [ipaddress.ip_network(a, strict=False) for a in await self._router.list_addresses()]
            self._list_cache = (now, nets)
        return self._list_cache[1]

    @staticmethod
    async def _resolve(name: str) -> list[str]:
        ips: set[str] = set()
        async with httpx.AsyncClient(timeout=6) as http:
            for url in DOH_RESOLVERS:
                try:
                    r = await http.get(url, params={"name": name, "type": "A"}, headers={"accept": "application/dns-json"})
                    ips |= {a["data"] for a in r.json().get("Answer", []) if a.get("type") == 1}
                except (httpx.HTTPError, ValueError):
                    continue
        if not ips:
            try:
                infos = await asyncio.to_thread(socket.getaddrinfo, name, 443, socket.AF_INET, socket.SOCK_STREAM)
                ips = {i[4][0] for i in infos}
            except socket.gaierror:
                raise DomainError(f"Could not resolve {name}")
        return sorted(ips)

    async def check(self, target: str) -> dict:
        target = self._host(target)
        if not target:
            raise DomainError("Enter a domain or an IP address")
        try:
            ips = [str(ipaddress.ip_address(target))]
        except ValueError:
            ips = await self._resolve(target)
        nets = await self._router_networks()
        result = []
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            match = next((n for n in nets if addr in n), None)
            result.append({"ip": ip, "via_vpn": match is not None, "prefix": str(match) if match else None})
        rule = next((i["cidr"] for i in await self.custom_list() if i.get("kind") == "domain" and i["cidr"] == target), None)
        return {"target": target, "ips": result, "rule": rule, "via_vpn": bool(rule) or any(r["via_vpn"] for r in result)}
