"""Managed network devices: registry, credentials in OpenBao, polling, settings and clients (endpoints)."""
from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import ipaddress
import logging
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..adapters.devices import PLATFORMS, platform
from ..adapters.devices import discovery
from ..adapters.devices.base import Credentials, Snapshot, Target, norm_ip, norm_mac, validate_action
from ..adapters.oui import OuiDatabase
from ..adapters.ssh import KEY_SECRET
from ..db.base import Database
from ..db.models import EndpointSighting, NetworkDevice, NetworkEndpoint
from ..domain.errors import DomainError, NotFound
from ..domain.models import Severity
from ..ports import SecretStore
from .alerts import AlertService
from .audit import AuditService
from .common import Actor, EventBus, utcnow
from .settings import SettingsService

log = logging.getLogger(__name__)
ROLES = ("router", "switch", "firewall", "ap", "controller", "server", "other")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")
SOURCE_RANK = {"wifi": 5, "controller": 4, "bridge": 3, "dhcp": 2, "arp": 1}
ADMIN_ACTIONS = {"reboot", "command", "backup"}
FRESH_SECONDS = 1800


def _secret_path(device_id) -> str:
    return f"devices/{device_id}"


class DeviceService:
    def __init__(self, db: Database, secrets: SecretStore, audit: AuditService, alerts: AlertService, bus: EventBus,
                 settings: SettingsService, oui: OuiDatabase, defaults: dict):
        self._db = db
        self._secrets = secrets
        self._audit = audit
        self._alerts = alerts
        self._bus = bus
        self._settings = settings
        self.oui = oui
        self._defaults = defaults
        self._sem = asyncio.Semaphore(6)
        self._running: set[uuid.UUID] = set()

    # ---------- presentation ----------
    @staticmethod
    def platforms() -> list[dict]:
        return [p.describe() for p in PLATFORMS.values()]

    def _view(self, d: NetworkDevice, creds: dict | None = None, full: bool = False) -> dict:
        p = PLATFORMS.get(d.platform)
        transport = d.transport or (next(iter(p.transports)) if p else "")
        out = {
            "id": str(d.id), "name": d.name, "platform": d.platform, "platform_title": p.title if p else d.platform,
            "vendor": (d.facts or {}).get("vendor") or (p.vendor if p else ""), "host": d.host, "port": d.port,
            "transport": transport, "verify_tls": d.verify_tls, "site": d.site, "location": d.location, "role": d.role,
            "tags": d.tags or [], "note": d.note, "enabled": d.enabled, "poll_seconds": d.poll_seconds,
            "flow_exporter_ip": d.flow_exporter_ip, "use_system_key": d.use_system_key,
            "ssh_host_key": bool(d.ssh_host_key), "status": d.status, "last_poll_at": d.last_poll_at,
            "last_ok_at": d.last_ok_at, "last_error": d.last_error, "facts": d.facts or {},
            "capabilities": p.capabilities(transport) if p else [],
            "counts": {"interfaces": len(d.interfaces or []), "neighbors": len(d.neighbors or []),
                       "children": len(d.children or []),
                       "interfaces_up": sum(1 for i in d.interfaces or [] if i.get("oper_up"))},
            "created_at": d.created_at, "created_by": d.created_by,
        }
        if creds is not None:
            out["credentials"] = {k: bool(v) for k, v in creds.items() if k in Credentials.SECRET_FIELDS}
            out["credentials"].update({k: v for k, v in creds.items()
                                       if k in ("username", "snmp_version", "snmp_user", "snmp_auth_protocol",
                                                "snmp_priv_protocol")})
        if full:
            out.update(interfaces=d.interfaces or [], neighbors=d.neighbors or [], children=d.children or [])
        return out

    async def list(self) -> list[dict]:
        async with self._db.session() as s:
            rows = (await s.execute(select(NetworkDevice).order_by(NetworkDevice.name))).scalars().all()
        return [self._view(d) for d in rows]

    async def _require(self, s, device_id) -> NetworkDevice:
        d = await s.get(NetworkDevice, uuid.UUID(str(device_id)))
        if not d:
            raise NotFound("Device not found")
        return d

    async def get(self, device_id) -> dict:
        async with self._db.session() as s:
            d = await self._require(s, device_id)
            eps = (await s.execute(select(NetworkEndpoint).where(NetworkEndpoint.device_id == d.id)
                                   .order_by(NetworkEndpoint.last_seen.desc()).limit(500))).scalars().all()
        creds = await self._secrets.get(_secret_path(d.id)) or {}
        out = self._view(d, creds, full=True)
        out["endpoints"] = [self._endpoint_view(e) for e in eps]
        return out

    # ---------- CRUD ----------
    def _validate(self, data: dict) -> dict:
        p = platform(data.get("platform", ""))
        host = str(data.get("host", "")).strip()
        if not host or not (norm_ip(host) or HOST_RE.fullmatch(host)):
            raise DomainError("Enter the IP address or host name of the device")
        transport = data.get("transport") or next(iter(p.transports))
        if transport not in p.transports:
            raise DomainError(f"{p.title}: available transports: {', '.join(p.transports)}")
        role = data.get("role") or p.kind
        if role not in ROLES:
            raise DomainError("Unknown device role")
        port = data.get("port")
        if port is not None and not (1 <= int(port) <= 65535):
            raise DomainError("Invalid port")
        exporter = str(data.get("flow_exporter_ip") or "").strip()
        if exporter and not norm_ip(exporter):
            raise DomainError("The flow exporter address must be an IP address")
        return {"platform": p.key, "host": host, "transport": transport, "role": role, "port": int(port) if port else None,
                "flow_exporter_ip": exporter}

    @staticmethod
    def _clean_creds(raw: dict | None) -> dict:
        out = {}
        for k, v in (raw or {}).items():
            if k in Credentials.__dataclass_fields__ and v not in (None, ""):
                out[k] = str(v)
        return out

    async def create(self, actor: Actor, data: dict) -> dict:
        v = self._validate(data)
        name = str(data.get("name") or "").strip() or v["host"]
        d = NetworkDevice(name=name[:128], verify_tls=bool(data.get("verify_tls")), site=str(data.get("site") or "")[:128],
                          location=str(data.get("location") or "")[:128], tags=[t[:32] for t in data.get("tags") or []][:16],
                          note=str(data.get("note") or ""), enabled=data.get("enabled", True) is not False,
                          poll_seconds=max(60, int(data.get("poll_seconds") or 300)),
                          use_system_key=bool(data.get("use_system_key")), created_by=actor.username, **v)
        creds = self._clean_creds(data.get("credentials"))
        async with self._db.session() as s:
            s.add(d)
            await s.flush()
            if creds:
                await self._secrets.put(_secret_path(d.id), creds)
            self._audit.record(s, actor, "device.created", "device", d.id, d.name, platform=d.platform, host=d.host)
        self._bus.publish("devices", {"id": str(d.id), "action": "created"})
        asyncio.get_running_loop().create_task(self.poll(d.id))
        return self._view(d, creds)

    async def update(self, actor: Actor, device_id, data: dict) -> dict:
        async with self._db.session() as s:
            d = await self._require(s, device_id)
            merged = {"platform": d.platform, "host": d.host, "transport": d.transport, "role": d.role, "port": d.port,
                      "flow_exporter_ip": d.flow_exporter_ip}
            merged.update({k: v for k, v in data.items() if k in merged})
            if "platform" in data and data["platform"] != d.platform and "transport" not in data:
                merged["transport"] = ""
            v = self._validate(merged)
            host_changed = v["host"] != d.host or v["port"] != d.port
            for k, val in v.items():
                setattr(d, k, val)
            for k in ("name", "site", "location", "note"):
                if k in data and data[k] is not None:
                    setattr(d, k, str(data[k])[:128] if k != "note" else str(data[k]))
            if "tags" in data:
                d.tags = [t[:32] for t in data["tags"] or []][:16]
            for k in ("verify_tls", "enabled", "use_system_key"):
                if k in data:
                    setattr(d, k, bool(data[k]))
            if "poll_seconds" in data:
                d.poll_seconds = max(60, int(data["poll_seconds"] or 300))
            if host_changed:
                d.ssh_host_key = ""
            creds = await self._secrets.get(_secret_path(d.id)) or {}
            if "credentials" in data or data.get("clear_credentials"):
                creds.update(self._clean_creds(data.get("credentials")))
                for k in data.get("clear_credentials") or []:
                    creds.pop(k, None)
                await self._secrets.put(_secret_path(d.id), creds)
            self._audit.record(s, actor, "device.updated", "device", d.id, d.name,
                               fields=",".join(sorted(k for k in data if k != "credentials")),
                               credentials="changed" if data.get("credentials") else "")
        self._bus.publish("devices", {"id": str(d.id), "action": "updated"})
        return self._view(d, creds)

    async def delete(self, actor: Actor, device_id) -> None:
        async with self._db.session() as s:
            d = await self._require(s, device_id)
            await s.delete(d)
            self._audit.record(s, actor, "device.deleted", "device", d.id, d.name, host=d.host)
        await self._secrets.delete(_secret_path(device_id))
        await self._alerts.resolve(f"device.down.{device_id}")
        self._bus.publish("devices", {"id": str(device_id), "action": "deleted"})

    async def reset_host_key(self, actor: Actor, device_id) -> None:
        async with self._db.session() as s:
            d = await self._require(s, device_id)
            d.ssh_host_key = ""
            self._audit.record(s, actor, "device.host_key_reset", "device", d.id, d.name)

    async def set_positions(self, positions: dict[str, list[float] | None]) -> None:
        async with self._db.session() as s:
            for key, xy in positions.items():
                try:
                    d = await s.get(NetworkDevice, uuid.UUID(key))
                except ValueError:
                    continue
                if d:
                    d.map_x, d.map_y = (float(xy[0]), float(xy[1])) if xy else (None, None)

    # ---------- driver plumbing ----------
    async def _credentials(self, d: NetworkDevice, override: dict | None = None) -> Credentials:
        raw = dict(await self._secrets.get(_secret_path(d.id)) or {}) if d.id else {}
        raw.update(override or {})
        if d.use_system_key and not raw.get("private_key"):
            key = await self._secrets.get(KEY_SECRET) or {}
            raw["private_key"] = key.get("private_key", "")
            raw.setdefault("username", self._defaults.get("router_ssh_user", "admin"))
        return Credentials.from_dict(raw)

    @staticmethod
    def _target(d: NetworkDevice) -> Target:
        return Target(host=d.host, port=d.port, transport=d.transport, verify_tls=d.verify_tls, site=d.site,
                      ssh_host_key=d.ssh_host_key)

    async def _collect(self, d: NetworkDevice, creds: Credentials) -> tuple[Snapshot, str]:
        drv = platform(d.platform).driver(self._target(d), creds)
        try:
            snap = await asyncio.wait_for(drv.collect(), 180)
            return snap, drv.learned_host_key
        finally:
            await drv.close()

    async def test(self, data: dict) -> dict:
        """Connects with unsaved parameters and returns what would be collected."""
        v = self._validate(data)
        d = NetworkDevice(id=None, name="test", verify_tls=bool(data.get("verify_tls")), site=str(data.get("site") or ""),
                          use_system_key=bool(data.get("use_system_key")), ssh_host_key="", **v)
        if data.get("id"):
            async with self._db.session() as s:
                saved = await self._require(s, data["id"])
            d.id, d.ssh_host_key = saved.id, saved.ssh_host_key if saved.host == d.host else ""
        creds = await self._credentials(d, self._clean_creds(data.get("credentials")))
        try:
            snap, _ = await self._collect(d, creds)
        except asyncio.TimeoutError:
            raise DomainError("The device did not respond within 3 minutes")
        f = snap.facts
        return {"ok": True, "facts": dataclasses.asdict(f), "interfaces": len(snap.interfaces),
                "neighbors": len(snap.neighbors), "endpoints": len(snap.endpoints), "children": len(snap.children)}

    # ---------- polling ----------
    async def poll_due(self) -> None:
        now = utcnow()
        async with self._db.session() as s:
            rows = (await s.execute(select(NetworkDevice).where(NetworkDevice.enabled))).scalars().all()
        due = [d.id for d in rows if d.id not in self._running and
               (not d.last_poll_at or (now - d.last_poll_at).total_seconds() >= d.poll_seconds - 5)]
        await asyncio.gather(*(self.poll(i) for i in due), return_exceptions=True)

    async def poll(self, device_id) -> dict:
        device_id = uuid.UUID(str(device_id))
        if device_id in self._running:
            raise DomainError("A poll is already running")
        self._running.add(device_id)
        try:
            async with self._sem:
                return await self._poll(device_id)
        finally:
            self._running.discard(device_id)

    async def _poll(self, device_id: uuid.UUID) -> dict:
        async with self._db.session() as s:
            d = await self._require(s, device_id)
        started = utcnow()
        error = ""
        snap = None
        learned = ""
        try:
            snap, learned = await self._collect(d, await self._credentials(d))
        except asyncio.TimeoutError:
            error = "Poll timed out (3 min)"
        except DomainError as e:
            error = e.message
        except Exception as e:  # a driver bug must not stop the poller
            log.exception("poll %s failed", d.name)
            error = f"{type(e).__name__}: {e}"
        async with self._db.session() as s:
            d = await self._require(s, device_id)
            d.last_poll_at = started
            if snap is None:
                d.failures += 1
                d.status = "down" if d.failures >= 2 else "error"
                d.last_error = error[:2000]
            else:
                if learned and not d.ssh_host_key:
                    d.ssh_host_key = learned
                elapsed = (started - d.last_ok_at).total_seconds() if d.last_ok_at else 0
                d.interfaces = self._with_rates(d.interfaces or [], snap, elapsed)
                d.facts = dataclasses.asdict(snap.facts)
                d.neighbors = [dataclasses.asdict(n) for n in snap.neighbors]
                d.children = [dataclasses.asdict(c) for c in snap.children]
                d.status, d.failures, d.last_error, d.last_ok_at = "up", 0, "", started
                if d.name == d.host and snap.facts.hostname:
                    d.name = snap.facts.hostname[:128]
            name, failures = d.name, d.failures
        if snap is not None:
            await self._store_endpoints(device_id, snap)
            await self._alerts.resolve(f"device.down.{device_id}")
        elif failures >= 2:
            await self._alerts.raise_(f"device.down.{device_id}", Severity.CRITICAL, f"Device is unreachable: {name}",
                                      error, str(device_id), link=f"/devices/{device_id}")
        self._bus.publish("devices", {"id": str(device_id), "action": "polled", "status": "up" if snap else "error"})
        return {"ok": snap is not None, "error": error}

    @staticmethod
    def _with_rates(previous: list[dict], snap: Snapshot, elapsed: float) -> list[dict]:
        prev = {i.get("name"): i for i in previous}
        out = []
        for i in snap.interfaces:
            row = dataclasses.asdict(i)
            old = prev.get(i.name) or {}
            for key, counter in (("rx_bps", "rx_bytes"), ("tx_bps", "tx_bytes")):
                a, b = old.get(counter), row.get(counter)
                if elapsed > 0 and a is not None and b is not None and b >= a:
                    row[key] = round((b - a) * 8 / elapsed, 1)
                else:
                    row[key] = None
            if row.get("speed_mbps") and (row.get("rx_bps") is not None or row.get("tx_bps") is not None):
                row["utilization"] = round(max(row.get("rx_bps") or 0, row.get("tx_bps") or 0) / (row["speed_mbps"] * 1e4), 1)
            out.append(row)
        return out

    async def _store_endpoints(self, device_id: uuid.UUID, snap: Snapshot) -> None:
        now = utcnow()
        merged: dict[tuple[str, str], dict] = {}
        for e in snap.endpoints:
            if not e.mac or e.mac.startswith("01:00:5e") or e.mac.startswith("33:33") or e.mac == "ff:ff:ff:ff:ff:ff":
                continue
            key = (e.mac, e.source)
            cur = merged.setdefault(key, {"mac": e.mac, "source": e.source})
            for f in ("ip", "hostname", "interface", "ssid", "attached_to", "port"):
                val = getattr(e, f)
                if val and not cur.get(f):
                    cur[f] = str(val)[:128 if f != "port" else 64]
            for f in ("signal", "vlan"):
                if getattr(e, f) is not None and cur.get(f) is None:
                    cur[f] = getattr(e, f)
            cur["blocked"] = cur.get("blocked", False) or e.blocked
        async with self._db.session() as s:
            await s.execute(delete(EndpointSighting).where(EndpointSighting.device_id == device_id))
            for row in merged.values():
                s.add(EndpointSighting(device_id=device_id, seen_at=now, **row))
        await self.rebuild_endpoints({m for m, _ in merged})

    async def rebuild_endpoints(self, macs: set[str]) -> None:
        """Merges the fresh sightings of each MAC into one endpoint: best attachment point, any known IP and name."""
        if not macs:
            return
        now = utcnow()
        macs_l = sorted(macs)
        async with self._db.session() as s:
            for i in range(0, len(macs_l), 500):
                chunk = macs_l[i:i + 500]
                rows = (await s.execute(select(EndpointSighting).where(EndpointSighting.mac.in_(chunk)))).scalars().all()
                by_mac: dict[str, list[EndpointSighting]] = {}
                for r in rows:
                    if (now - r.seen_at).total_seconds() <= FRESH_SECONDS:
                        by_mac.setdefault(r.mac, []).append(r)
                if not by_mac:
                    continue
                # Polls of several devices run concurrently and can see the same new MAC: create rows race-free.
                await s.execute(insert(NetworkEndpoint).values(
                    [{"mac": m, "first_seen": now, "last_seen": now, "vendor": self.oui.vendor(m)} for m in sorted(by_mac)]
                ).on_conflict_do_nothing(index_elements=["mac"]))
                existing = {e.mac: e for e in (await s.execute(
                    select(NetworkEndpoint).where(NetworkEndpoint.mac.in_(sorted(by_mac)))
                    .order_by(NetworkEndpoint.mac).with_for_update())).scalars().all()}
                for mac, sights in by_mac.items():
                    best = max(sights, key=lambda r: (SOURCE_RANK.get(r.source, 0), r.seen_at))
                    e = existing[mac]
                    e.ip = next((r.ip for r in sorted(sights, key=lambda r: SOURCE_RANK.get(r.source, 0), reverse=True)
                                 if r.ip), e.ip or "")
                    e.hostname = next((r.hostname for r in sights if r.hostname), e.hostname or "")
                    e.device_id, e.interface, e.source = best.device_id, best.interface, best.source
                    e.attached_to, e.port, e.ssid = best.attached_to, best.port, best.ssid
                    e.signal, e.vlan = best.signal, best.vlan if best.vlan is not None else e.vlan
                    e.blocked = any(r.blocked for r in sights)
                    e.last_seen = max(r.seen_at for r in sights)
                    if not e.vendor:
                        e.vendor = self.oui.vendor(mac)

    # ---------- settings ----------
    async def apply(self, actor: Actor, device_id, action: str, params: dict) -> dict:
        p = validate_action(action, params) if action != "flow_export" or (params or {}).get("collector") else {}
        if action in ADMIN_ACTIONS and actor.role != "admin":
            raise DomainError("Only an administrator can do this")
        if action == "flow_export" and not (params or {}).get("collector"):
            params = {**(params or {}), "collector": await self._settings.get("flow_collector_host")
                      or self._defaults.get("collector_host", ""),
                      "port": (params or {}).get("port") or await self._settings.get("flow_collector_port")}
            p = validate_action(action, params)
        async with self._db.session() as s:
            d = await self._require(s, device_id)
        pl = platform(d.platform)
        if action not in pl.capabilities(d.transport):
            raise DomainError(f"{pl.title}: '{action}' is not supported over {d.transport}" if d.transport
                              else f"{pl.title}: '{action}' is not supported")
        drv = pl.driver(self._target(d), await self._credentials(d))
        ok, result = False, ""
        try:
            result = await asyncio.wait_for(drv.backup() if action == "backup" else drv.apply(action, p), 300)
            ok = True
        finally:
            await drv.close()
            async with self._db.session() as s:
                details = {k: (v if k != "command" else str(v)[:300]) for k, v in p.items() if k not in ("password",)}
                self._audit.record(s, actor, f"device.{action}", "device", d.id, d.name, ok=ok, **{
                    k: str(v) for k, v in details.items()})
        if action not in ("command", "backup", "reboot"):
            asyncio.get_running_loop().create_task(self._repoll_later(d.id))
        return {"ok": True, "output": str(result)[:200_000]}

    async def _repoll_later(self, device_id) -> None:
        await asyncio.sleep(3)
        try:
            await self.poll(device_id)
        except DomainError:
            pass

    # ---------- endpoints ----------
    def _endpoint_view(self, e: NetworkEndpoint, names: dict | None = None) -> dict:
        age = (utcnow() - e.last_seen).total_seconds() if e.last_seen else None
        return {"mac": e.mac, "ip": e.ip, "hostname": e.hostname, "alias": e.alias, "name": e.alias or e.hostname or "",
                "note": e.note, "vendor": e.vendor, "device_id": str(e.device_id) if e.device_id else None,
                "device_name": (names or {}).get(e.device_id, ""), "interface": e.interface, "source": e.source,
                "attached_to": e.attached_to, "port": e.port, "ssid": e.ssid, "signal": e.signal, "vlan": e.vlan,
                "blocked": e.blocked, "first_seen": e.first_seen, "last_seen": e.last_seen,
                "online": age is not None and age <= self._offline_seconds}

    _offline_seconds = 900

    async def endpoints(self, q: str = "", online: bool | None = None, device_id: str | None = None,
                        limit: int = 2000) -> list[dict]:
        self._offline_seconds = int(await self._settings.get("endpoint_offline_minutes")) * 60
        async with self._db.session() as s:
            stmt = select(NetworkEndpoint).order_by(NetworkEndpoint.last_seen.desc()).limit(limit)
            if device_id:
                stmt = stmt.where(NetworkEndpoint.device_id == uuid.UUID(device_id))
            rows = (await s.execute(stmt)).scalars().all()
            devices = (await s.execute(select(NetworkDevice))).scalars().all()
            names = {d.id: d.name for d in devices}
            infra = self._infra_macs(devices)
        out = [self._endpoint_view(e, names) for e in rows if e.mac not in infra]
        if q:
            ql = q.lower()
            out = [e for e in out if any(ql in str(e.get(k) or "").lower()
                                         for k in ("mac", "ip", "hostname", "alias", "vendor", "device_name", "ssid"))]
        if online is not None:
            out = [e for e in out if e["online"] == online]
        return out

    @staticmethod
    def _infra_macs(devices) -> set[str]:
        """MACs of network equipment (managed devices, controller children, LLDP / CDP neighbours): not clients."""
        out: set[str] = set()
        for d in devices:
            out.add(((d.facts or {}).get("mac") or "").lower())
            out.update((i.get("mac") or "").lower() for i in d.interfaces or [])
            out.update((c.get("mac") or "").lower() for c in d.children or [])
            out.update((n.get("remote_mac") or "").lower() for n in d.neighbors or [])
        out.discard("")
        return out

    async def update_endpoint(self, actor: Actor, mac: str, data: dict) -> dict:
        mac = norm_mac(mac)
        async with self._db.session() as s:
            e = await s.get(NetworkEndpoint, mac)
            if not e:
                raise NotFound("Network client not found")
            if "alias" in data:
                e.alias = str(data["alias"] or "")[:128]
            if "note" in data:
                e.note = str(data["note"] or "")
            self._audit.record(s, actor, "endpoint.updated", "endpoint", mac, e.alias or e.hostname or mac)
        return self._endpoint_view(e)

    async def forget_endpoint(self, actor: Actor, mac: str) -> None:
        mac = norm_mac(mac)
        async with self._db.session() as s:
            await s.execute(delete(EndpointSighting).where(EndpointSighting.mac == mac))
            await s.execute(delete(NetworkEndpoint).where(NetworkEndpoint.mac == mac))
            self._audit.record(s, actor, "endpoint.deleted", "endpoint", mac, mac)

    async def block_endpoint(self, actor: Actor, mac: str, blocked: bool) -> dict:
        """Blocks a client on the device that can do it: its controller / router with endpoint_block."""
        mac = norm_mac(mac)
        async with self._db.session() as s:
            sights = (await s.execute(select(EndpointSighting).where(EndpointSighting.mac == mac))).scalars().all()
            devices = {d.id: d for d in (await s.execute(select(NetworkDevice))).scalars().all()}
        # The best attachment point first (controller over a router's DHCP lease), as in rebuild_endpoints.
        sights = sorted(sights, key=lambda r: (SOURCE_RANK.get(r.source, 0), r.seen_at), reverse=True)
        for sight in sights:
            d = devices.get(sight.device_id)
            if d and "endpoint_block" in platform(d.platform).capabilities(d.transport):
                return await self.apply(actor, d.id, "endpoint_block", {"mac": mac, "blocked": blocked})
        raise DomainError("No device can block this client (UniFi, Omada or Keenetic is required)")

    async def purge_endpoints(self, days: int) -> None:
        async with self._db.session() as s:
            cutoff = utcnow() - dt.timedelta(days=days)
            await s.execute(delete(NetworkEndpoint).where(NetworkEndpoint.last_seen < cutoff, NetworkEndpoint.alias == ""))
            await s.execute(delete(EndpointSighting).where(EndpointSighting.seen_at < cutoff))

    # ---------- discovery ----------
    async def discover(self, target: str, community: str = "public") -> list[dict]:
        target = target.strip()
        try:
            net = ipaddress.ip_network(target, strict=False)
        except ValueError:
            if not HOST_RE.fullmatch(target):
                raise DomainError("Enter an address, a host name or a subnet (up to /22)")
            return [await discovery.probe(target, community)]
        try:
            found = await discovery.scan(str(net), community) if net.num_addresses > 1 else \
                [await discovery.probe(str(net.network_address), community)]
        except ValueError as e:
            raise DomainError(str(e))
        async with self._db.session() as s:
            known = {d.host for d in (await s.execute(select(NetworkDevice))).scalars().all()}
        for f in found:
            f["known"] = f["host"] in known
        return found

    async def seed_router(self) -> None:
        """First start: registers the home MikroTik the panel already manages over SSH."""
        host = self._defaults.get("router_host")
        if not host:
            return
        async with self._db.session() as s:
            if (await s.execute(select(NetworkDevice.id).limit(1))).first():
                return
            s.add(NetworkDevice(name="MikroTik", platform="mikrotik", host=host, transport="ssh", role="router",
                                use_system_key=True, created_by="system", poll_seconds=300,
                                note="Added automatically: the router from ROUTER_HOST"))

    async def exporter_map(self) -> dict[str, dict]:
        """Exporter address (IPv4 or IPv6) → device, for flow enrichment: the configured exporter IP, the host and
        every interface address."""
        async with self._db.session() as s:
            rows = (await s.execute(select(NetworkDevice))).scalars().all()
        out: dict[str, dict] = {}
        for d in rows:
            addrs = [d.flow_exporter_ip, d.host] + [a.split("/")[0] for i in d.interfaces or [] for a in i.get("addresses") or []]
            for a in addrs:
                ip = norm_ip(a)
                if ip and not ip.startswith("fe80:") and ip not in out:
                    out[ip] = {"id": str(d.id), "name": d.name,
                               "interfaces": [(i.get("index"), i.get("name"), i.get("speed_mbps") or 0)
                                              for i in d.interfaces or [] if i.get("index")]}
        return out

    async def local_networks(self) -> list[str]:
        """Subnets of device interfaces treated as local by the flow collector: private IPv4 subnets and IPv6 LAN
        prefixes (ULA and global /48–/64; link-local is always local in the collector)."""
        async with self._db.session() as s:
            rows = (await s.execute(select(NetworkDevice.interfaces))).scalars().all()
        nets = set()
        for ifaces in rows:
            for i in ifaces or []:
                for a in i.get("addresses") or []:
                    try:
                        n = ipaddress.ip_interface(a).network
                    except ValueError:
                        continue
                    if n.is_loopback or n.is_link_local:
                        continue
                    if (n.version == 4 and n.is_private and 16 <= n.prefixlen <= 30) or (n.version == 6 and 48 <= n.prefixlen <= 64):
                        nets.add(str(n))
        return sorted(nets)
