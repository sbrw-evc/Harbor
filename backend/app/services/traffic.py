from __future__ import annotations

import datetime as dt
import logging
import time

from ..db.base import Database
from ..db.models import ConnectionEvent
from ..db.repositories import ClientRepo, ConnectionRepo, TrafficRepo
from ..domain.errors import ExternalServiceError
from ..domain.models import Severity
from ..ports import GeoLocator, VpnBackend
from .alerts import AlertService
from .clients import ONLINE_SECONDS, VpnClientService
from .common import EventBus, utcnow

log = logging.getLogger(__name__)


class TrafficCollector:
    def __init__(self, db: Database, backends: list[VpnBackend], clients: VpnClientService, geo: GeoLocator,
                 alerts: AlertService, bus: EventBus):
        self._db = db
        self._backends = backends
        self._clients = clients
        self._geo = geo
        self._alerts = alerts
        self._bus = bus
        self._last_poll: float | None = None

    async def poll(self) -> None:
        runtime, ok = await self._collect()
        if not ok and not runtime:
            self._bus.publish("live", {"server_ok": False})
            return

        now_mono = time.monotonic()
        elapsed = (now_mono - self._last_poll) if self._last_poll else None
        self._last_poll = now_mono
        now = utcnow()

        async with self._db.session() as s:
            known = {c.public_key for c in await ClientRepo(s).all()}
        if set(runtime) - known:
            await self._clients.sync()

        rates: dict[str, dict] = {}
        sum_rx = sum_tx = online = 0
        async with self._db.session() as s:
            clients = {c.public_key: c for c in await ClientRepo(s).all()}
            traffic, conns = TrafficRepo(s), ConnectionRepo(s)
            for pub, peer in runtime.items():
                c = clients.get(pub)
                if c is None:
                    continue
                baseline = c.raw_rx == 0 and c.raw_tx == 0 and c.total_rx == 0 and c.total_tx == 0
                drx = peer.rx - c.raw_rx if peer.rx >= c.raw_rx else peer.rx
                dtx = peer.tx - c.raw_tx if peer.tx >= c.raw_tx else peer.tx
                c.raw_rx, c.raw_tx = peer.rx, peer.tx
                if not baseline and (drx or dtx):
                    c.total_rx += drx
                    c.total_tx += dtx
                    await traffic.add(c.id, now, drx, dtx)
                    if elapsed:
                        rates[str(c.id)] = {"rx": round(dtx / elapsed), "tx": round(drx / elapsed)}
                        sum_rx += dtx
                        sum_tx += drx
                if peer.latest_handshake:
                    c.last_handshake_at = dt.datetime.fromtimestamp(peer.latest_handshake, dt.timezone.utc)
                    if (now - c.last_handshake_at).total_seconds() < ONLINE_SECONDS:
                        online += 1
                if peer.endpoint and peer.endpoint != c.last_endpoint:
                    self._record_endpoint(conns, c, peer.endpoint_ip, peer.endpoint)
                elif peer.endpoint_ip and c.lat is None:
                    await self._backfill_geo(conns, c, peer.endpoint_ip)
        self._bus.publish("live", {
            "t": now.isoformat(), "elapsed": bool(elapsed), "server_ok": ok, "online": online,
            "rx": round(sum_rx / elapsed) if elapsed else 0, "tx": round(sum_tx / elapsed) if elapsed else 0,
            "clients": rates,
        })

    async def _collect(self) -> tuple[dict, bool]:
        runtime, ok = {}, True
        for b in self._backends:
            if not await b.configured():
                await self._alerts.resolve(b.alert_key)
                continue
            try:
                runtime.update(await b.runtime())
            except ExternalServiceError as e:
                ok = False
                await self._alerts.raise_(b.alert_key, Severity.CRITICAL, f"VPN server {b.title} is unreachable", e.message)
                continue
            await self._alerts.resolve(b.alert_key)
        return runtime, ok

    def _record_endpoint(self, conns: ConnectionRepo, c, ip: str | None, endpoint: str) -> None:
        c.last_endpoint = endpoint
        geo = self._geo.lookup(ip) if ip else None
        port = int(endpoint.rsplit(":", 1)[1]) if ":" in endpoint else None
        if geo:
            c.country_code, c.country, c.city, c.lat, c.lon = geo.country_code, geo.country, geo.city, geo.lat, geo.lon
        conns.add(ConnectionEvent(client_id=c.id, ip=ip or "", port=port,
                                  country_code=geo.country_code if geo else None, country=geo.country if geo else None,
                                  city=geo.city if geo else None, lat=geo.lat if geo else None,
                                  lon=geo.lon if geo else None))
        self._bus.publish("connection", {"client": c.name, "ip": ip, "country": geo.country if geo else None})

    async def _backfill_geo(self, conns: ConnectionRepo, c, ip: str) -> None:
        geo = self._geo.lookup(ip)
        if not geo or geo.lat is None:
            return
        c.country_code, c.country, c.city, c.lat, c.lon = geo.country_code, geo.country, geo.city, geo.lat, geo.lon
        await conns.fill_geo(c.id, ip, geo)

    async def purge(self, retention_days: int) -> None:
        before = utcnow() - dt.timedelta(days=retention_days)
        # On TimescaleDB whole chunks go first (cheap); the DELETE below takes what is left of the boundary chunk.
        await self._db.drop_chunks(("traffic_buckets", "connection_events"), before)
        async with self._db.session() as s:
            await TrafficRepo(s).purge(before)
            await ConnectionRepo(s).purge(before)
