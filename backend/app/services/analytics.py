import datetime as dt
import uuid

from ..db.base import Database
from ..db.repositories import ClientRepo, ConnectionRepo, TrafficRepo
from ..domain.models import ClientStatus
from ..ports import GeoLocator
from .alerts import AlertService
from .clients import ONLINE_SECONDS
from .common import current as current_value, utcnow
from .settings import SettingsService

RANGES = {
    "1h": (dt.timedelta(hours=1), dt.timedelta(minutes=5), "5 min"),
    "24h": (dt.timedelta(hours=24), dt.timedelta(minutes=15), "15 min"),
    "7d": (dt.timedelta(days=7), dt.timedelta(hours=2), "2 h"),
    "30d": (dt.timedelta(days=30), dt.timedelta(days=1), "1 day"),
    "90d": (dt.timedelta(days=90), dt.timedelta(days=1), "1 day"),
}


class AnalyticsService:
    def __init__(self, db: Database, live, alerts: AlertService, settings: SettingsService,
                 geo: GeoLocator, server_ip: str):
        self._geo = geo
        self._server_ip = server_ip
        self._db = db
        self._live = live
        self._alerts = alerts
        self._settings = settings

    @staticmethod
    def _range(name: str):
        if name not in RANGES:
            name = "24h"
        delta, step, _ = RANGES[name]
        return utcnow() - delta, step

    async def summary(self) -> dict:
        now = utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._db.session() as s:
            clients = await ClientRepo(s).all()
            tr = TrafficRepo(s)
            day, week, month = await tr.total(today), await tr.total(now - dt.timedelta(days=7)), await tr.total(now - dt.timedelta(days=30))
        online = sum(1 for c in clients if c.last_handshake_at and (now - c.last_handshake_at).total_seconds() < ONLINE_SECONDS
                     and c.status == ClientStatus.ACTIVE)
        live = self._live.history[-1] if self._live.history else {"rx": 0, "tx": 0}
        gateway = next((c for c in clients if c.system), None)
        return {
            "clients_total": len(clients),
            "clients_active": sum(c.status == ClientStatus.ACTIVE for c in clients),
            "clients_blocked": sum(c.status == ClientStatus.BLOCKED for c in clients),
            "clients_missing": sum(c.status == ClientStatus.MISSING for c in clients),
            "online": online,
            "traffic_today": sum(day), "traffic_7d": sum(week), "traffic_30d": sum(month),
            "rate_rx": live["rx"], "rate_tx": live["tx"],
            "server_ok": self._live.server_ok,
            "gateway_online": bool(gateway and gateway.last_handshake_at
                                   and (now - gateway.last_handshake_at).total_seconds() < ONLINE_SECONDS),
            "alerts": await self._alerts.counts(),
        }

    def live(self) -> list[dict]:
        return list(self._live.history)

    async def series(self, range_: str, client_id: uuid.UUID | None = None) -> dict:
        since, step = self._range(range_)
        async with self._db.session() as s:
            rows = await TrafficRepo(s).series(since, step, client_id)
        label = RANGES.get(range_, RANGES["24h"])[2]
        return {"step": label, "points": [{"t": r["t"], "rx": r["tx"], "tx": r["rx"]} for r in rows]}

    async def top(self, range_: str, limit: int = 10) -> list[dict]:
        since, _ = self._range(range_)
        async with self._db.session() as s:
            totals = await TrafficRepo(s).totals_by_client(since)
            clients = {c.id: c for c in await ClientRepo(s).all()}
        rows = [{"id": str(cid), "name": clients[cid].name if cid in clients else "?", "rx": t[1], "tx": t[0],
                 "total": t[0] + t[1]} for cid, t in totals.items()]
        return sorted(rows, key=lambda r: r["total"], reverse=True)[:limit]

    async def heatmap(self, range_: str) -> list[dict]:
        since, _ = self._range(range_ if range_ in ("7d", "30d", "90d") else "30d")
        async with self._db.session() as s:
            return await TrafficRepo(s).heatmap(since, await self._settings.get("timezone"))

    async def geo(self, range_: str) -> dict:
        since, _ = self._range(range_ if range_ != "1h" else "24h")
        async with self._db.session() as s:
            conns = ConnectionRepo(s)
            points, countries = await conns.points(since), await conns.by_country(since)
            clients = {str(c.id): c for c in await ClientRepo(s).all()}
            recent = await conns.recent(30)
        now = utcnow()
        current = [{"id": str(c.id), "name": c.name, "lat": c.lat, "lon": c.lon, "city": c.city, "country": c.country,
                    "online": bool(c.last_handshake_at and (now - c.last_handshake_at).total_seconds() < ONLINE_SECONDS)}
                   for c in clients.values() if c.lat is not None]
        for p in points:
            p["clients"] = [clients[i].name for i in p.pop("client_ids") if i in clients]
        srv = self._geo.lookup(current_value(self._server_ip) or "")
        server = {"lat": srv.lat, "lon": srv.lon, "city": srv.city, "country": srv.country} if srv and srv.lat else None
        return {"points": points, "countries": countries, "current": current, "server": server,
                "recent": [{"ts": e.ts.isoformat(), "client": clients[str(e.client_id)].name if str(e.client_id) in clients else "?",
                            "ip": e.ip, "city": e.city, "country": e.country, "country_code": e.country_code} for e in recent]}
