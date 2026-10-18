from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import math
import socket

import httpx
from sqlalchemy import or_, select

from ..adapters.clickhouse import ClickHouseClient
from ..db.base import Database
from ..db.models import SavedFilter, SavedView
from ..db.repositories import SavedFilterRepo
from ..domain.errors import DomainError
from .audit import AuditService
from .common import Actor, utcnow
from .flowfilter import EXPORTER_NAME as EXPORTER_NAME_SQL, FIELDS, FilterParser, dimension, ip_key, ip_text

UNITS = {"bps": ("Bytes * 8", "bit/s"), "pps": ("Packets", "packets/s"), "fps": ("Flows", "flows/s")}
LIMIT_TYPES = ("avg", "max", "last")
GRAPHS = ("stacked", "stacked100", "lines", "grid", "sankey")


def _ts(v: dt.datetime) -> str:
    return v.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


AGG_COLUMNS = ("Direction", "Route", "ExporterAddress", "InIf", "OutIf", "LocalAddr", "RemoteAS", "RemoteASName",
               "RemoteCountry", "Proto", "ServicePort")
AGG_TABLES = {"flows_5m": (300, 90), "flows_1h": (3600, 730)}  # step seconds, TTL days
SERVICE_PORT = "if(LocalPort != 0 AND (RemotePort = 0 OR LocalPort < RemotePort), LocalPort, RemotePort)"


class FlowSchema:
    def __init__(self, ch: ClickHouseClient):
        self._ch = ch

    def _source(self, table: str) -> str:
        return (f"SOURCE(CLICKHOUSE(DB '{self._ch.database}' TABLE '{table}' USER '{self._ch.user}' "
                f"PASSWORD '{self._ch.password}'))")

    async def ensure(self, ttl_days: int) -> None:
        db = self._ch.database
        await self._ch.command(f"CREATE DATABASE IF NOT EXISTS {db}", database="default")
        await self._ch.command(f"""
            CREATE TABLE IF NOT EXISTS {db}.flows (
                TimeReceived DateTime CODEC(DoubleDelta, LZ4),
                Direction Enum8('out' = 1, 'in' = 2, 'internal' = 3),
                Route Enum8('direct' = 1, 'vpn' = 2),
                LocalAddr IPv6,
                RemoteAddr IPv6,
                Proto UInt8,
                LocalPort UInt16,
                RemotePort UInt16,
                RemoteAS UInt32,
                RemoteASName LowCardinality(String),
                RemoteCountry LowCardinality(String),
                Bytes UInt64,
                Packets UInt64,
                Flows UInt32 DEFAULT 1,
                ExporterAddress IPv6 DEFAULT toIPv6('::ffff:0.0.0.0'),
                InIf UInt32 DEFAULT 0,
                OutIf UInt32 DEFAULT 0,
                TCPFlags UInt8 DEFAULT 0,
                SamplingRate UInt32 DEFAULT 1,
                ServicePort UInt16 DEFAULT {SERVICE_PORT}
            ) ENGINE = MergeTree
            PARTITION BY toYYYYMMDD(TimeReceived)
            ORDER BY (TimeReceived, Direction, Route)
            TTL TimeReceived + INTERVAL {int(ttl_days)} DAY""")
        await self._ch.command(f"ALTER TABLE {db}.flows MODIFY TTL TimeReceived + INTERVAL {int(ttl_days)} DAY")
        # columns added after 1.0: old parts compute them from the DEFAULT expressions
        await self._ch.command(
            f"ALTER TABLE {db}.flows ADD COLUMN IF NOT EXISTS ExporterAddress IPv6 DEFAULT toIPv6('::ffff:0.0.0.0'), "
            "ADD COLUMN IF NOT EXISTS InIf UInt32 DEFAULT 0, ADD COLUMN IF NOT EXISTS OutIf UInt32 DEFAULT 0, "
            "ADD COLUMN IF NOT EXISTS TCPFlags UInt8 DEFAULT 0, ADD COLUMN IF NOT EXISTS SamplingRate UInt32 DEFAULT 1, "
            f"ADD COLUMN IF NOT EXISTS ServicePort UInt16 DEFAULT {SERVICE_PORT}")
        typ = await self._ch.query("SELECT type FROM system.columns WHERE database = currentDatabase() "
                                   "AND table = 'flows' AND name = 'Direction'")
        if typ and "internal" not in typ[0]["type"]:
            await self._ch.command(f"ALTER TABLE {db}.flows MODIFY COLUMN Direction "
                                   "Enum8('out' = 1, 'in' = 2, 'internal' = 3)")

        renamed = await self._migrate_ipv6(db)

        cols = ", ".join(AGG_COLUMNS)
        for table, (step, ttl) in AGG_TABLES.items():
            exists = await self._ch.query(f"SELECT count() AS n FROM system.tables WHERE database = currentDatabase() "
                                          f"AND name = '{table}'")
            await self._ch.command(f"""
                CREATE TABLE IF NOT EXISTS {db}.{table} (
                    TimeReceived DateTime, Direction Enum8('out' = 1, 'in' = 2, 'internal' = 3),
                    Route Enum8('direct' = 1, 'vpn' = 2), ExporterAddress IPv6, InIf UInt32, OutIf UInt32,
                    LocalAddr IPv6, RemoteAS UInt32, RemoteASName LowCardinality(String),
                    RemoteCountry LowCardinality(String), Proto UInt8, ServicePort UInt16,
                    Bytes UInt64, Packets UInt64, Flows UInt64
                ) ENGINE = SummingMergeTree((Bytes, Packets, Flows))
                PARTITION BY toYYYYMM(TimeReceived)
                ORDER BY (TimeReceived, {cols})
                TTL TimeReceived + INTERVAL {ttl} DAY""")
            select = (f"SELECT t AS TimeReceived, {cols}, sum(Bytes) AS Bytes, sum(Packets) AS Packets, "
                      f"sum(Flows) AS Flows FROM (SELECT toStartOfInterval(TimeReceived, INTERVAL {step} SECOND) AS t, "
                      f"{cols}, Bytes, Packets, Flows FROM {db}.flows) GROUP BY t, {cols}")
            await self._ch.command(f"CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.{table}_mv TO {db}.{table} AS {select}")
            if table in renamed:
                await self._ch.command(f"INSERT INTO {db}.{table} (TimeReceived, {cols}, Bytes, Packets, Flows) "
                                       f"SELECT TimeReceived, {cols}, Bytes, Packets, Flows FROM {db}.{table}_v4")
                await self._ch.command(f"DROP TABLE {db}.{table}_v4")
            elif not int(exists[0]["n"]):
                await self._ch.command(f"INSERT INTO {db}.{table} {select}")

        await self._ch.command(f"""
            CREATE TABLE IF NOT EXISTS {db}.site_prefixes (prefix String, site String, grp String)
            ENGINE = ReplacingMergeTree ORDER BY prefix""")
        await self._ch.command(f"""
            CREATE DICTIONARY IF NOT EXISTS {db}.sites (prefix String, site String, grp String)
            PRIMARY KEY prefix {self._source('site_prefixes')} LAYOUT(IP_TRIE()) LIFETIME(MIN 60 MAX 300)""")
        await self._ch.command(f"""
            CREATE TABLE IF NOT EXISTS {db}.threat_prefixes (prefix String, list String)
            ENGINE = ReplacingMergeTree ORDER BY prefix""")
        await self._ch.command(f"""
            CREATE DICTIONARY IF NOT EXISTS {db}.threats (prefix String, list String)
            PRIMARY KEY prefix {self._source('threat_prefixes')} LAYOUT(IP_TRIE()) LIFETIME(MIN 300 MAX 600)""")
        for table, attrs in (("hosts", "name String, mac String"), ("exporters", "name String, device_id String"),
                             ("ptr", "name String")):
            # a dictionary cannot share its name with a table: data lives in <name>_data. The key is the address as
            # text (IPv4 dotted, IPv6 as is), so both families are looked up the same way.
            await self._ch.command(f"CREATE TABLE IF NOT EXISTS {db}.{table}_data (addr String, {attrs}) "
                                   "ENGINE = ReplacingMergeTree ORDER BY addr")
            await self._ch.command(f"CREATE DICTIONARY IF NOT EXISTS {db}.{table} (addr String, {attrs}) PRIMARY KEY addr "
                                   f"{self._source(table + '_data')} LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(MIN 60 MAX 180)")
        await self._ch.command(f"CREATE TABLE IF NOT EXISTS {db}.if_names_data (addr String, idx UInt32, name String, "
                               "speed UInt64) ENGINE = ReplacingMergeTree ORDER BY (addr, idx)")
        await self._ch.command(f"CREATE DICTIONARY IF NOT EXISTS {db}.if_names (addr String, idx UInt32, name String, "
                               f"speed UInt64) PRIMARY KEY addr, idx {self._source('if_names_data')} "
                               "LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(MIN 60 MAX 180)")
        await self._ch.command(f"CREATE TABLE IF NOT EXISTS {db}.local_networks (prefix String) "
                               "ENGINE = ReplacingMergeTree ORDER BY prefix")



    async def _column_type(self, table: str, column: str) -> str:
        rows = await self._ch.query("SELECT type FROM system.columns WHERE database = currentDatabase() "
                                    f"AND table = '{table}' AND name = '{column}'")
        return rows[0]["type"] if rows else ""

    async def _migrate_ipv6(self, db: str) -> set[str]:
        """Up to 2.0 addresses were IPv4 columns and IPv6 flows were dropped by the collector. Addresses become IPv6
        columns (IPv4 kept as ::ffff:a.b.c.d); the aggregates have them in the sorting key, so they are rebuilt; the
        address-keyed dictionaries are recreated with text keys (the backend refills them within a minute)."""
        if await self._column_type("flows", "LocalAddr") == "IPv4":
            for table in AGG_TABLES:
                await self._ch.command(f"DROP VIEW IF EXISTS {db}.{table}_mv")
            await self._ch.command(
                f"ALTER TABLE {db}.flows MODIFY COLUMN LocalAddr IPv6, MODIFY COLUMN RemoteAddr IPv6, "
                "MODIFY COLUMN ExporterAddress IPv6 DEFAULT toIPv6('::ffff:0.0.0.0')")
        renamed = set()
        for table in AGG_TABLES:
            # a copy interrupted by a restart is finished on the next start
            if await self._column_type(f"{table}_v4", "LocalAddr"):
                renamed.add(table)
            if await self._column_type(table, "LocalAddr") != "IPv4" or table in renamed:
                continue
            # the address is in the sorting key: ensure() creates the table anew and copies the old rows into it
            await self._ch.command(f"DROP VIEW IF EXISTS {db}.{table}_mv")
            await self._ch.command(f"RENAME TABLE {db}.{table} TO {db}.{table}_v4")
            renamed.add(table)
        for table in ("hosts", "exporters", "ptr", "if_names"):
            if await self._column_type(f"{table}_data", "addr") not in ("", "String"):
                await self._ch.command(f"DROP DICTIONARY IF EXISTS {db}.{table}")
                await self._ch.command(f"DROP TABLE IF EXISTS {db}.{table}_data")
        return renamed


class FlowService:
    def __init__(self, ch: ClickHouseClient, db: Database, audit: AuditService):
        self._ch = ch
        self._db = db
        self._audit = audit
        self.schema = FlowSchema(ch)
        self.sites_seeded = False

    async def load_sites(self, rows: list[tuple[str, str, str]]) -> None:
        await self._ch.command(f"TRUNCATE TABLE IF EXISTS {self._ch.database}.site_prefixes")
        await self.add_sites(rows)

    async def add_sites(self, rows: list[tuple[str, str, str]]) -> None:
        for i in range(0, len(rows), 20000):
            await self._ch.insert("site_prefixes", [{"prefix": p, "site": s, "grp": g} for p, s, g in rows[i:i + 20000]])
        await self._ch.command(f"SYSTEM RELOAD DICTIONARY {self._ch.database}.sites")

    async def remove_site(self, prefix: str) -> None:
        await self._ch.command(f"ALTER TABLE {self._ch.database}.site_prefixes DELETE WHERE prefix = '{prefix}'")
        await self._ch.command(f"SYSTEM RELOAD DICTIONARY {self._ch.database}.sites")

    @staticmethod
    def fields() -> list[dict]:
        return [{"name": f.name, "label": f.label, "kind": f.kind} for f in FIELDS.values()]

    async def status(self) -> dict:
        r = (await self._ch.query(
            "SELECT countIf(TimeReceived >= now() - 60) AS last_minute, max(TimeReceived) AS last_flow, "
            "count() AS total FROM flows"))[0]
        disk = (await self._ch.query(
            "SELECT sum(bytes_on_disk) AS disk FROM system.parts WHERE database = currentDatabase() AND active"))[0]
        last = r["last_flow"] if int(r["total"]) else None
        return {"flows_per_second": round(int(r["last_minute"]) / 60, 2), "last_flow": last,
                "total_flows": int(r["total"]), "disk_bytes": int(disk["disk"] or 0), "ok": await self._ch.ping()}

    async def overview(self) -> dict:
        widgets = {}
        for key, dim in (("as", "RemoteAS"), ("country", "RemoteCountry"), ("site", "Site"), ("proto", "Proto"),
                         ("port", "RemotePort"), ("device", "LocalHost"), ("app", "Application"),
                         ("exporter", "ExporterName"), ("threat", "Threat")):
            expr = dimension(dim).dimension
            rows = await self._ch.query(
                f"SELECT {expr} AS k, sum(Bytes) AS v FROM flows WHERE TimeReceived >= now() - 3600 "
                + ("AND " + FIELDS['threat'].column + " != '' " if key == "threat" else "")
                + "GROUP BY k ORDER BY v DESC LIMIT 8")
            total = sum(int(r["v"]) for r in rows) or 1
            widgets[key] = [{"name": r["k"], "bytes": int(r["v"]), "percent": round(int(r["v"]) * 100 / total, 1)} for r in rows]
        route = await self._ch.query(
            "SELECT toString(Route) AS route, toString(Direction) AS dir, sum(Bytes) AS v FROM flows "
            "WHERE TimeReceived >= now() - 86400 AND Direction != 'internal' GROUP BY route, dir")
        series = await self._ch.query(
            "SELECT toStartOfInterval(TimeReceived, INTERVAL 600 SECOND) AS t, toString(Direction) AS dir, "
            "sum(Bytes) * 8 / 600 AS v FROM flows WHERE TimeReceived >= now() - 86400 AND Direction != 'internal' "
            "GROUP BY t, dir ORDER BY t")
        last = await self._ch.query(self._records_sql("1", 1))
        return {"widgets": widgets, "routes": [{**r, "v": int(r["v"])} for r in route],
                "series": [{"t": r["t"], "dir": r["dir"], "v": float(r["v"])} for r in series],
                "last_flow": last[0] if last else None, "status": await self.status()}

    async def recent(self, filter_text: str, limit: int = 100) -> list[dict]:
        parser = FilterParser(filter_text)
        where = parser.compile()
        return await self._ch.query(
            self._records_sql(f"TimeReceived >= now() - 3600 AND ({where})", min(int(limit), 500)), parser.params)

    @staticmethod
    def _records_sql(where: str, limit: int) -> str:
        return (
            "SELECT TimeReceived, toString(Direction) AS Direction, toString(Route) AS Route, "
            f"{ip_text('LocalAddr')} AS LocalAddr, {ip_text('RemoteAddr')} AS RemoteAddr, Protocol, "
            "LocalPort, RemotePort, RemoteAS, RemoteASName, RemoteCountry, Site, Bytes, Packets, LocalHost, Application, "
            "Exporter, InIfName, OutIfName, Flags, Threat, RemoteHost FROM ("
            f"SELECT *, {FIELDS['proto'].dimension} AS Protocol, {FIELDS['site'].dimension} AS Site, "
            f"{FIELDS['localhost'].dimension} AS LocalHost, {FIELDS['application'].dimension} AS Application, "
            f"{FIELDS['exportername'].dimension} AS Exporter, {FIELDS['inifname'].dimension} AS InIfName, "
            f"{FIELDS['outifname'].dimension} AS OutIfName, {FIELDS['tcpflags'].dimension} AS Flags, "
            f"{FIELDS['threat'].column} AS Threat, {FIELDS['remotehost'].column} AS RemoteHost FROM flows "
            f"WHERE {where} ORDER BY TimeReceived DESC LIMIT {int(limit)}) ORDER BY TimeReceived DESC")

    async def visualize(self, q: dict) -> dict:
        end = dt.datetime.fromisoformat(q["end"]) if q.get("end") else utcnow()
        start = dt.datetime.fromisoformat(q["start"]) if q.get("start") else end - dt.timedelta(hours=6)
        if end.tzinfo is None:
            end = end.replace(tzinfo=dt.timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.timezone.utc)
        if start >= end:
            raise DomainError("The start of the period must be before its end")
        duration = (end - start).total_seconds()
        points = max(20, min(int(q.get("points") or 200), 600))
        step = max(60, int(math.ceil(duration / points / 60) * 60))
        unit = q.get("unit") if q.get("unit") in UNITS else "bps"
        graph = q.get("graph") if q.get("graph") in GRAPHS else "stacked"
        limit = max(1, min(int(q.get("limit") or 10), 50))
        limit_type = q.get("limitType") if q.get("limitType") in LIMIT_TYPES else "avg"
        dims = [dimension(d) for d in (q.get("dimensions") or [])][:6]
        bidirectional = bool(q.get("bidirectional")) and graph != "sankey"
        if graph == "sankey" and len(dims) < 2:
            raise DomainError("Select at least two dimensions for Sankey")

        parser = FilterParser(q.get("filter") or "")
        where = parser.compile()
        params = {**parser.params, "start": _ts(start), "end": _ts(end)}
        table = self._table(duration, step, [d.name for d in dims] + list(parser.fields))
        if table != "flows":
            step = max(step, AGG_TABLES[table][0])
        base = f"TimeReceived >= {{start:DateTime}} AND TimeReceived < {{end:DateTime}} AND ({where})"
        metric = UNITS[unit][0]
        dim_sql = [d.dimension for d in dims] or ["'Total'"]
        dim_cols = ", ".join(f"{s} AS d{i}" for i, s in enumerate(dim_sql))
        dim_names = ", ".join(f"d{i}" for i in range(len(dim_sql)))

        if limit_type == "max":
            top_sql = (f"SELECT {dim_names} FROM (SELECT {dim_cols}, toStartOfInterval(TimeReceived, INTERVAL {step} SECOND) AS t, "
                       f"sum({metric}) AS v FROM {table} WHERE {base} GROUP BY {dim_names}, t) "
                       f"GROUP BY {dim_names} ORDER BY max(v) DESC LIMIT {limit}")
        elif limit_type == "last":
            params["last_start"] = _ts(end - dt.timedelta(seconds=step * 3))
            top_sql = (f"SELECT {dim_cols} FROM {table} WHERE {base} AND TimeReceived >= {{last_start:DateTime}} "
                       f"GROUP BY {dim_names} ORDER BY sum({metric}) DESC LIMIT {limit}")
        else:
            top_sql = f"SELECT {dim_cols} FROM {table} WHERE {base} GROUP BY {dim_names} ORDER BY sum({metric}) DESC LIMIT {limit}"
        in_top = f"({dim_names}) IN ({top_sql})" if dims else "1"

        if graph == "sankey":
            rows = await self._ch.query(
                f"SELECT {dim_cols}, sum({metric}) / {duration} AS v FROM {table} WHERE {base} AND {in_top} "
                f"GROUP BY {dim_names} ORDER BY v DESC", params)
            return {**self._sankey(rows, dims, unit), "table": table}

        key_expr = (f"if({in_top}, [{dim_names}], ['Other'])" if dims else "['Total']")
        dir_expr = "toString(Direction)" if bidirectional else "'out'"
        rows = await self._ch.query(
            f"SELECT toStartOfInterval(TimeReceived, INTERVAL {step} SECOND) AS t, {dir_expr} AS dir, key, "
            f"sum({metric}) / {step} AS v FROM (SELECT *, {dim_cols}, {key_expr} AS key FROM {table} WHERE {base}) "
            f"GROUP BY t, dir, key ORDER BY t", params)

        axis = []
        t = start.replace(second=0, microsecond=0)
        t = dt.datetime.fromtimestamp(int(t.timestamp()) // step * step, dt.timezone.utc)
        while t < end:
            axis.append(t)
            t += dt.timedelta(seconds=step)
        index = {a.strftime("%Y-%m-%d %H:%M:%S"): i for i, a in enumerate(axis)}
        series: dict[tuple, list[float]] = {}
        for r in rows:
            i = index.get(r["t"])
            if i is None:
                continue
            key = (r["dir"], tuple(r["key"]))
            series.setdefault(key, [0.0] * len(axis))[i] += float(r["v"])

        out = []
        for (direction, key), values in series.items():
            sign = -1 if bidirectional and direction == "in" else 1
            avg = sum(values) / len(values) if values else 0
            total = sum(values) * step / (8 if unit == "bps" else 1)
            out.append({"name": " · ".join(key), "dimensions": list(key), "direction": direction,
                        "values": [round(v * sign, 3) for v in values],
                        "stats": {"min": min(values), "max": max(values), "last": values[-1] if values else 0,
                                  "avg": avg, "p95": _percentile(values, 0.95), "total": total}})
        out.sort(key=lambda s: (s["direction"] != "out", s["name"] == "Other", -s["stats"]["avg"]))

        previous = None
        if q.get("previousPeriod") and graph in ("stacked", "lines"):
            shift = self._previous_shift(duration)
            pparams = {**params, "start": _ts(start - shift), "end": _ts(end - shift)}
            prow = await self._ch.query(
                f"SELECT toStartOfInterval(TimeReceived + {int(shift.total_seconds())}, INTERVAL {step} SECOND) AS t, "
                f"sum({metric}) / {step} AS v FROM {table} WHERE {base} "
                + ("AND Direction = 'out' " if bidirectional else "") + "GROUP BY t ORDER BY t", pparams)
            pvals = [0.0] * len(axis)
            for r in prow:
                i = index.get(r["t"])
                if i is not None:
                    pvals[i] = float(r["v"])
            previous = {"label": self._previous_label(duration), "values": pvals}

        return {"axis": [a.isoformat() for a in axis], "step": step, "unit": unit, "unit_label": UNITS[unit][1],
                "graph": graph, "series": out, "previous": previous, "table": table,
                "dimensions": [d.name for d in dims], "bidirectional": bidirectional,
                "start": start.isoformat(), "end": end.isoformat()}

    @staticmethod
    def _previous_shift(duration: float) -> dt.timedelta:
        for limit, shift in ((3600, dt.timedelta(hours=1)), (86400, dt.timedelta(days=1)),
                             (7 * 86400, dt.timedelta(days=7)), (31 * 86400, dt.timedelta(days=30))):
            if duration <= limit:
                return shift
        return dt.timedelta(days=365)

    @staticmethod
    def _previous_label(duration: float) -> str:
        for limit, label in ((3600, "an hour ago"), (86400, "a day ago"), (7 * 86400, "a week ago"),
                             (31 * 86400, "a month ago")):
            if duration <= limit:
                return label
        return "a year ago"

    @staticmethod
    def _sankey(rows: list[dict], dims, unit: str) -> dict:
        nodes: dict[str, dict] = {}
        links: dict[tuple[str, str], float] = {}
        n = len(dims)
        for r in rows:
            v = float(r["v"])
            names = [f"{r[f'd{i}']}​" * (i + 1) for i in range(n)]
            for i in range(n):
                nodes.setdefault(names[i], {"name": names[i], "label": r[f"d{i}"], "depth": i})
            for i in range(n - 1):
                links[(names[i], names[i + 1])] = links.get((names[i], names[i + 1]), 0) + v
        return {"graph": "sankey", "unit": unit, "unit_label": UNITS[unit][1], "dimensions": [d.name for d in dims],
                "nodes": list(nodes.values()),
                "links": [{"source": s, "target": t, "value": v} for (s, t), v in links.items()]}

    async def saved_filters(self, actor: Actor) -> list[dict]:
        async with self._db.session() as s:
            rows = await SavedFilterRepo(s).visible(actor.username)
        return [{"id": f.id, "name": f.name, "filter": f.filter, "shared": f.shared, "owner": f.created_by,
                 "mine": f.created_by == actor.username} for f in rows]

    async def save_filter(self, actor: Actor, name: str, text: str, shared: bool) -> dict:
        FilterParser(text).compile()
        async with self._db.session() as s:
            f = SavedFilterRepo(s).add(SavedFilter(name=name.strip()[:128], filter=text, shared=shared,
                                                   created_by=actor.username))
            await s.flush()
            self._audit.record(s, actor, "flows.filter_saved", "filter", f.id, f.name)
            return {"id": f.id}

    async def delete_filter(self, actor: Actor, filter_id: int) -> None:
        async with self._db.session() as s:
            repo = SavedFilterRepo(s)
            f = await repo.require(filter_id)
            if f.created_by != actor.username and actor.role != "admin":
                raise DomainError("You can delete only your own filters")
            await repo.remove(f)

    # ---------- aggregates ----------
    @staticmethod
    def _table(duration: float, step: int, fields: list[str]) -> str:
        """Picks the coarsest table that still answers the query: aggregates for long periods when every
        dimension and filter field exists there."""
        if any(not FIELDS[f.lower()].agg for f in fields):
            return "flows"
        if duration > 7 * 86400 and step >= 3600:
            return "flows_1h"
        if duration > 86400 and step >= 300:
            return "flows_5m"
        return "flows"

    # ---------- live traffic for the map ----------
    async def local_traffic(self, seconds: int = 300) -> list[dict]:
        rows = await self._ch.query(
            f"SELECT {ip_text('LocalAddr')} AS ip, sum(Bytes) * 8 / {int(seconds)} AS bps, "
            f"sumIf(Bytes, Direction = 'in') * 8 / {int(seconds)} AS in_bps, "
            f"sumIf(Bytes, Direction = 'out') * 8 / {int(seconds)} AS out_bps FROM flows "
            f"WHERE TimeReceived >= now() - {int(seconds)} GROUP BY ip ORDER BY bps DESC LIMIT 5000")
        return [{"ip": r["ip"], "bps": float(r["bps"]), "in_bps": float(r["in_bps"]), "out_bps": float(r["out_bps"])}
                for r in rows]

    # ---------- exporters ----------
    async def exporters(self) -> list[dict]:
        rows = await self._ch.query(
            f"SELECT {ip_text('ExporterAddress')} AS addr, {EXPORTER_NAME_SQL} AS name, "
            "countIf(TimeReceived >= now() - 300) / 300 AS fps, sumIf(Bytes, TimeReceived >= now() - 300) * 8 / 300 AS bps, "
            "max(TimeReceived) AS last_seen, count() AS flows, round(avg(SamplingRate)) AS sampling "
            "FROM flows WHERE TimeReceived >= now() - 86400 GROUP BY addr, name ORDER BY bps DESC")
        ifs = await self._ch.query(
            f"SELECT {ip_text('ea')} AS addr, idx, "
            f"dictGetOrDefault('if_names', 'name', ({ip_text('ea')}, idx), toString(idx)) AS name, "
            f"dictGetOrDefault('if_names', 'speed', ({ip_text('ea')}, idx), toUInt64(0)) AS speed, "
            "sum(i) * 8 / 300 AS in_bps, sum(o) * 8 / 300 AS out_bps FROM ("
            "SELECT ExporterAddress AS ea, InIf AS idx, Bytes AS i, toUInt64(0) AS o FROM flows "
            "WHERE TimeReceived >= now() - 300 AND InIf != 0 UNION ALL "
            "SELECT ExporterAddress AS ea, OutIf AS idx, toUInt64(0) AS i, Bytes AS o FROM flows "
            "WHERE TimeReceived >= now() - 300 AND OutIf != 0) "
            "GROUP BY ea, idx ORDER BY addr, in_bps + out_bps DESC LIMIT 2000")
        by: dict[str, list] = {}
        for r in ifs:
            speed = int(r["speed"] or 0)
            util = round(max(float(r["in_bps"]), float(r["out_bps"])) / (speed * 1e4), 1) if speed else None
            by.setdefault(r["addr"], []).append({"index": int(r["idx"]), "name": r["name"], "in_bps": float(r["in_bps"]),
                                                 "out_bps": float(r["out_bps"]), "speed_mbps": speed or None,
                                                 "utilization": util})
        return [{"address": r["addr"], "name": r["name"], "flows_per_second": round(float(r["fps"]), 2),
                 "bps": float(r["bps"]), "last_seen": r["last_seen"], "flows_24h": int(r["flows"]),
                 "sampling_rate": int(float(r["sampling"] or 1)), "interfaces": by.get(r["addr"], [])} for r in rows]

    # ---------- enrichment dictionaries ----------
    async def _replace(self, table: str, rows: list[dict]) -> None:
        """Replaces the data of a dictionary (`name` → table `name_data`) or of a plain table and reloads it."""
        target = table if table == "local_networks" else f"{table}_data"
        await self._ch.command(f"TRUNCATE TABLE IF EXISTS {self._ch.database}.{target}")
        for i in range(0, len(rows), 20000):
            await self._ch.insert(target, rows[i:i + 20000])
        if table != "local_networks":
            await self._ch.command(f"SYSTEM RELOAD DICTIONARY {self._ch.database}.{table}")

    async def sync_enrichment(self, exporters: dict[str, dict], hosts: list[dict], local_nets: list[str]) -> None:
        """Exporter names, interface names / speeds by ifIndex, LAN host names and local subnets for the collector."""
        await self._replace("exporters", [{"addr": _ip_text(ip), "name": e["name"], "device_id": e["id"]}
                                          for ip, e in exporters.items()])
        await self._replace("if_names", [{"addr": _ip_text(ip), "idx": int(idx), "name": str(name), "speed": int(speed or 0)}
                                         for ip, e in exporters.items() for idx, name, speed in e["interfaces"]])
        await self._replace("hosts", [{"addr": _ip_text(h["ip"]), "name": h["name"], "mac": h["mac"]} for h in hosts
                                      if h.get("ip")])
        await self._replace("local_networks", [{"prefix": n} for n in local_nets])

    # ---------- reverse DNS ----------
    async def refresh_ptr(self, limit: int = 300) -> int:
        rows = await self._ch.query(
            f"SELECT {ip_text('RemoteAddr')} AS ip FROM flows WHERE TimeReceived >= now() - 3600 "
            f"AND dictGetOrDefault('ptr', 'name', {ip_key('RemoteAddr')}, '') = '' "
            f"GROUP BY ip ORDER BY sum(Bytes) DESC LIMIT {int(limit)}")
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(20)

        async def one(ip: str) -> dict:
            async with sem:
                try:
                    host, _ = await asyncio.wait_for(loop.getnameinfo((ip, 0), socket.NI_NAMEREQD), 3)
                except (OSError, asyncio.TimeoutError):
                    host = "—"
                return {"addr": _ip_text(ip), "name": host[:253]}

        found = await asyncio.gather(*(one(r["ip"]) for r in rows))
        if found:
            await self._ch.insert("ptr_data", found)
            await self._ch.command(f"SYSTEM RELOAD DICTIONARY {self._ch.database}.ptr")
        return len(found)

    # ---------- threat intelligence ----------
    async def refresh_threats(self, lists: list[dict], exclude: list[str]) -> dict:
        """Downloads public blocklists (FireHOL level1, Spamhaus DROP, abuse.ch Feodo…) into an ip_trie dictionary."""
        excluded = [ipaddress.ip_network(e, strict=False) for e in exclude if e]
        rows: list[dict] = []
        status: dict[str, object] = {}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     headers={"User-Agent": "NetworkControl/1.0"}) as http:
            for lst in lists:
                if not lst.get("enabled", True):
                    continue
                try:
                    r = await http.get(lst["url"])
                    r.raise_for_status()
                except httpx.HTTPError as e:
                    status[lst["name"]] = f"Error: {e}"
                    continue
                count = 0
                for net in parse_blocklist(r.text):
                    if net.is_private or net.is_reserved or net.is_loopback or net.is_multicast or net.is_link_local \
                            or net.prefixlen < (8 if net.version == 4 else 19) \
                            or any(net.overlaps(x) for x in excluded):
                        continue
                    rows.append({"prefix": str(net), "list": lst["name"]})
                    count += 1
                status[lst["name"]] = count
        if rows:
            await self._ch.command(f"TRUNCATE TABLE IF EXISTS {self._ch.database}.threat_prefixes")
            for i in range(0, len(rows), 50000):
                await self._ch.insert("threat_prefixes", rows[i:i + 50000])
            await self._ch.command(f"SYSTEM RELOAD DICTIONARY {self._ch.database}.threats")
        return {"lists": status, "prefixes": len(rows), "updated_at": utcnow().isoformat()}

    async def threat_contacts(self, hours: int = 24) -> list[dict]:
        rows = await self._ch.query(
            f"SELECT {FIELDS['threat'].column} AS list, {ip_text('LocalAddr')} AS local, "
            f"{FIELDS['localhost'].column} AS host, {ip_text('RemoteAddr')} AS remote, any(RemoteCountry) AS country, "
            "any(RemoteASName) AS asname, toString(any(Direction)) AS direction, sum(Bytes) AS bytes, sum(Flows) AS flows, "
            f"max(TimeReceived) AS last_seen FROM flows WHERE TimeReceived >= now() - {int(hours) * 3600} "
            f"AND {FIELDS['threat'].column} != '' GROUP BY list, local, host, remote ORDER BY last_seen DESC LIMIT 500")
        return [{**r, "bytes": int(r["bytes"]), "flows": int(r["flows"])} for r in rows]

    # ---------- saved views ----------
    async def views(self, actor: Actor) -> list[dict]:
        async with self._db.session() as s:
            rows = (await s.execute(select(SavedView).where(or_(SavedView.shared, SavedView.created_by == actor.username))
                                    .order_by(SavedView.name))).scalars().all()
        return [{"id": v.id, "name": v.name, "state": v.state, "shared": v.shared, "owner": v.created_by,
                 "mine": v.created_by == actor.username} for v in rows]

    async def save_view(self, actor: Actor, name: str, state: dict, shared: bool) -> dict:
        FilterParser(str(state.get("filter") or "")).compile()
        async with self._db.session() as s:
            v = SavedView(name=name.strip()[:128], state=state, shared=shared, created_by=actor.username)
            s.add(v)
            await s.flush()
            self._audit.record(s, actor, "flows.view_saved", "view", v.id, v.name)
            return {"id": v.id}

    async def delete_view(self, actor: Actor, view_id: int) -> None:
        async with self._db.session() as s:
            v = await s.get(SavedView, view_id)
            if not v:
                raise DomainError("View not found")
            if v.created_by != actor.username and actor.role != "admin":
                raise DomainError("You can delete only your own views")
            await s.delete(v)


def _ip_text(ip: str) -> str:
    """The dictionary key of an address: as ip_text() renders the column (IPv4 dotted, IPv6 compressed)."""
    a = ipaddress.ip_address(str(ip).split("%")[0])
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(a)


def parse_blocklist(text: str):
    """Plain lists (one address / prefix per line, '#' or ';' comments) and Spamhaus JSON lines."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("{"):
            try:
                cidr = json.loads(line).get("cidr")
            except ValueError:
                continue
            if not cidr:
                continue
        else:
            cidr = line.split(";")[0].split("#")[0].split()[0]
        try:
            net = ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError:
            continue
        yield net


DEFAULT_THREAT_LISTS = [
    {"name": "firehol_level1", "url": "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset",
     "enabled": True, "terms": "https://iplists.firehol.org/?ipset=firehol_level1"},
    {"name": "spamhaus_drop", "url": "https://www.spamhaus.org/drop/drop_v4.json", "enabled": True,
     "terms": "https://www.spamhaus.org/blocklists/do-not-route-or-peer/"},
    {"name": "spamhaus_drop_v6", "url": "https://www.spamhaus.org/drop/drop_v6.json", "enabled": True,
     "terms": "https://www.spamhaus.org/blocklists/do-not-route-or-peer/"},
    {"name": "feodo", "url": "https://feodotracker.abuse.ch/downloads/ipblocklist.txt", "enabled": True,
     "terms": "https://feodotracker.abuse.ch/ (CC0)"},
    {"name": "cins_army", "url": "https://cinsscore.com/list/ci-badguys.txt", "enabled": False,
     "terms": "https://cinsscore.com/"},
]
