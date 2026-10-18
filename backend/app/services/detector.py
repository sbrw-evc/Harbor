"""Traffic detection rules over the ClickHouse flows: thresholds, DDoS (volumetric, SYN), scans, baseline anomalies
and drops, silent exporters, threat contacts and link utilisation. Firing groups become panel alerts (and Telegram
notifications) with a link to the matching flow view."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import statistics

from sqlalchemy import select

from ..adapters.clickhouse import ClickHouseClient
from ..db.base import Database
from ..db.models import FlowRule
from ..domain.errors import DomainError, NotFound
from ..domain.models import Severity
from .alerts import AlertService
from .audit import AuditService
from .common import Actor, utcnow
from .flowfilter import FIELDS, NO_EXPORTER, FilterParser, ch_ip, dimension, ip_text

log = logging.getLogger(__name__)

KINDS: dict[str, dict] = {
    "threshold": {"title": "Traffic threshold", "defaults": {"dimensions": ["LocalHost"], "metric": "bps", "operator": ">",
                                                         "threshold": 100_000_000, "window": 300}},
    "ddos_volumetric": {"title": "DDoS: volumetric attack", "defaults": {"bps": 500_000_000, "pps": 100_000, "window": 60,
                                                                     "k": 0, "baseline_window": 3600}},
    "ddos_syn": {"title": "DDoS: SYN flood", "defaults": {"pps": 2000, "ratio": 0.6, "window": 60}},
    "scan": {"title": "Port / host scan", "defaults": {"mode": "inbound", "ports": 100, "hosts": 50,
                                                                   "window": 300}},
    "anomaly": {"title": "Anomaly against the weekly baseline", "defaults": {
        "dimensions": [], "weeks": 3, "k": 3, "ratio": 0.5, "min_bps": 1_000_000, "direction": "up"}},
    "traffic_drop": {"title": "Traffic drop", "defaults": {"dimensions": [], "weeks": 3, "ratio": 0.2,
                                                              "min_bps": 1_000_000}},
    "exporter_silent": {"title": "Exporter is silent", "defaults": {"minutes": 10}},
    "threat_contact": {"title": "Contact with a threat-listed address", "defaults": {"window": 300, "min_flows": 1}},
    "link_utilization": {"title": "Link utilization", "defaults": {"percent": 85, "window": 300}},
}
MAX_GROUPS = 50


def fmt_rate(v: float, unit: str = "bps") -> str:
    if unit == "pps":
        return f"{v:,.0f} pps".replace(",", " ")
    for div, name in ((1e9, "Gbit/s"), (1e6, "Mbit/s"), (1e3, "kbit/s")):
        if v >= div:
            return f"{v / div:.1f} {name}"
    return f"{v:.0f} bit/s"


def flows_link(filter_text: str, dimensions: list[str], minutes: int = 60, graph: str = "stacked") -> str:
    end = utcnow()
    state = {"preset": "custom", "refresh": 0, "start": (end - dt.timedelta(minutes=minutes)).isoformat(),
             "end": end.isoformat(), "dimensions": dimensions, "filter": filter_text, "limit": 10, "limitType": "avg",
             "unit": "bps", "graph": graph, "bidirectional": True, "previousPeriod": False}
    raw = base64.b64encode(json.dumps(state, ensure_ascii=False).encode()).decode()
    return f"/flows?tab=visualize&q={raw}"


class Detector:
    def __init__(self, ch: ClickHouseClient, db: Database, alerts: AlertService, audit: AuditService):
        self._ch = ch
        self._db = db
        self._alerts = alerts
        self._audit = audit

    # ---------- CRUD ----------
    @staticmethod
    def kinds() -> list[dict]:
        return [{"kind": k, "title": v["title"], "defaults": v["defaults"]} for k, v in KINDS.items()]

    @staticmethod
    def _validate(data: dict) -> dict:
        kind = data.get("kind")
        if kind not in KINDS:
            raise DomainError("Unknown rule type")
        FilterParser(data.get("filter") or "").compile()
        params = {**KINDS[kind]["defaults"], **(data.get("params") or {})}
        for d in params.get("dimensions") or []:
            dimension(d)
        if len(params.get("dimensions") or []) > 3:
            raise DomainError("No more than three dimensions")
        for key in ("window", "baseline_window"):
            if key in params:
                params[key] = max(60, min(int(params[key]), 86400))
        params["for_count"] = max(1, min(int(params.get("for_count") or 1), 30))
        params["clear_count"] = max(1, min(int(params.get("clear_count") or 2), 60))
        try:
            severity = Severity(data.get("severity") or "warning")
        except ValueError:
            raise DomainError("Invalid severity")
        return {"kind": kind, "params": params, "severity": severity}

    @staticmethod
    def _view(r: FlowRule) -> dict:
        return {"id": r.id, "name": r.name, "kind": r.kind, "kind_title": KINDS.get(r.kind, {}).get("title", r.kind),
                "enabled": r.enabled, "filter": r.filter, "severity": r.severity.value, "params": r.params,
                "firing": len((r.state or {}).get("firing", {})), "last_eval_at": r.last_eval_at,
                "last_error": r.last_error, "created_by": r.created_by}

    async def list(self) -> list[dict]:
        async with self._db.session() as s:
            rows = (await s.execute(select(FlowRule).order_by(FlowRule.id))).scalars().all()
        return [self._view(r) for r in rows]

    async def create(self, actor: Actor, data: dict) -> dict:
        v = self._validate(data)
        async with self._db.session() as s:
            r = FlowRule(name=str(data.get("name") or KINDS[v["kind"]]["title"])[:128], filter=data.get("filter") or "",
                         enabled=data.get("enabled", True) is not False, created_by=actor.username, state={}, **v)
            s.add(r)
            await s.flush()
            self._audit.record(s, actor, "flows.rule_created", "rule", r.id, r.name, kind=r.kind)
            return self._view(r)

    async def update(self, actor: Actor, rule_id: int, data: dict) -> dict:
        async with self._db.session() as s:
            r = await s.get(FlowRule, rule_id)
            if not r:
                raise NotFound("Rule not found")
            v = self._validate({"kind": r.kind, "params": {**r.params, **(data.get("params") or {})},
                                "filter": data.get("filter", r.filter), "severity": data.get("severity", r.severity.value)})
            r.params, r.severity = v["params"], v["severity"]
            for k in ("name", "filter"):
                if k in data:
                    setattr(r, k, str(data[k] or "")[:128] if k == "name" else str(data[k] or ""))
            if "enabled" in data:
                r.enabled = bool(data["enabled"])
            self._audit.record(s, actor, "flows.rule_updated", "rule", r.id, r.name)
            out = self._view(r)
        if not out["enabled"]:
            await self._alerts.resolve_prefix_except(f"flow.rule.{rule_id}.", set())
        return out

    async def delete(self, actor: Actor, rule_id: int) -> None:
        async with self._db.session() as s:
            r = await s.get(FlowRule, rule_id)
            if not r:
                raise NotFound("Rule not found")
            await s.delete(r)
            self._audit.record(s, actor, "flows.rule_deleted", "rule", rule_id, r.name)
        await self._alerts.resolve_prefix_except(f"flow.rule.{rule_id}.", set())

    async def seed_defaults(self) -> None:
        async with self._db.session() as s:
            if (await s.execute(select(FlowRule.id).limit(1))).first():
                return
            for kind, name, sev in (("ddos_volumetric", "Incoming DDoS attack", Severity.CRITICAL),
                                    ("ddos_syn", "SYN flood", Severity.CRITICAL),
                                    ("threat_contact", "Contact with a malicious address", Severity.WARNING),
                                    ("scan", "Scan from the internet", Severity.INFO),
                                    ("exporter_silent", "NetFlow exporter is silent", Severity.WARNING)):
                s.add(FlowRule(name=name, kind=kind, severity=sev, params=self._validate({"kind": kind})["params"],
                               enabled=kind != "scan", created_by="system", state={}))

    # ---------- evaluation ----------
    async def run(self) -> None:
        async with self._db.session() as s:
            rules = (await s.execute(select(FlowRule).where(FlowRule.enabled))).scalars().all()
        for r in rules:
            try:
                groups = await self.evaluate(r)
                error = ""
            except Exception as e:
                log.warning("rule %s failed: %s", r.name, e)
                groups, error = None, getattr(e, "message", str(e))[:1000]
            async with self._db.session() as s:
                cur = await s.get(FlowRule, r.id)
                if not cur:
                    continue
                cur.last_eval_at, cur.last_error = utcnow(), error
                if groups is not None:
                    cur.state = await self._transition(cur, groups)

    async def test(self, rule_id: int) -> list[dict]:
        async with self._db.session() as s:
            r = await s.get(FlowRule, rule_id)
            if not r:
                raise NotFound("Rule not found")
        groups = await self.evaluate(r)
        return [{"group": g, **v} for g, v in groups.items()]

    async def _transition(self, r: FlowRule, groups: dict[str, dict]) -> dict:
        state = dict(r.state or {})
        pending, firing, clear = dict(state.get("pending", {})), dict(state.get("firing", {})), dict(state.get("clear", {}))
        for_count, clear_count = r.params.get("for_count", 1), r.params.get("clear_count", 2)
        now = utcnow().isoformat()
        for g, v in groups.items():
            pending[g] = pending.get(g, 0) + 1
            clear.pop(g, None)
            if pending[g] >= for_count or g in firing:
                firing.setdefault(g, now)
                await self._alerts.raise_(self._key(r, g), r.severity, f"{r.name}: {v['label']}", v["text"],
                                          f"rule:{r.id}", link=v.get("link", ""))
        for g in list(firing):
            if g not in groups:
                clear[g] = clear.get(g, 0) + 1
                if clear[g] >= clear_count:
                    await self._alerts.resolve(self._key(r, g))
                    firing.pop(g)
                    clear.pop(g)
        pending = {g: c for g, c in pending.items() if g in groups}
        return {"pending": pending, "firing": firing, "clear": clear}

    @staticmethod
    def _key(r: FlowRule, group: str) -> str:
        return f"flow.rule.{r.id}.{hashlib.sha1(group.encode()).hexdigest()[:12]}"

    async def evaluate(self, r: FlowRule) -> dict[str, dict]:
        parser = FilterParser(r.filter or "")
        where = parser.compile()
        fn = getattr(self, f"_k_{r.kind}")
        return await fn(r.params, where, parser.params, r.filter or "")

    async def _q(self, sql: str, params: dict) -> list[dict]:
        return await self._ch.query(sql, params)

    @staticmethod
    def _and(user_filter: str, extra: str) -> str:
        return f"({user_filter}) AND {extra}" if user_filter else extra

    async def _k_threshold(self, p, where, params, ftext):
        w = int(p["window"])
        metric = "sum(Bytes) * 8" if p.get("metric", "bps") == "bps" else "sum(Packets)"
        op = "<" if p.get("operator") == "<" else ">"
        dims = [dimension(d) for d in p.get("dimensions") or []]
        cols = ", ".join(f"{d.dimension} AS d{i}" for i, d in enumerate(dims))
        group = f"GROUP BY {', '.join(f'd{i}' for i in range(len(dims)))}" if dims else ""
        rows = await self._q(
            f"SELECT {cols + ', ' if cols else ''}{metric} / {w} AS v FROM flows "
            f"WHERE TimeReceived >= now() - {w} AND ({where}) {group} HAVING v {op} {float(p['threshold'])} "
            f"ORDER BY v DESC LIMIT {MAX_GROUPS}", params)
        out = {}
        for row in rows:
            label = " · ".join(str(row[f"d{i}"]) for i in range(len(dims))) or "all traffic"
            filt = " AND ".join(f"{d.name} = '{row[f'd{i}']}'" for i, d in enumerate(dims) if d.kind in ("string", "enum"))
            out[label] = {"label": label, "value": float(row["v"]),
                          "text": f"{fmt_rate(float(row['v']), p.get('metric', 'bps'))} {op} "
                                  f"{fmt_rate(float(p['threshold']), p.get('metric', 'bps'))} over {w // 60} min",
                          "link": flows_link(self._and(ftext, filt) if filt else ftext, p.get("dimensions") or [])}
        return out

    async def _k_ddos_volumetric(self, p, where, params, ftext):
        w = int(p["window"])
        rows = await self._q(
            f"SELECT {ip_text('LocalAddr')} AS ip, {FIELDS['localhost'].column} AS host, sum(Bytes) * 8 / {w} AS bps, "
            f"sum(Packets) / {w} AS pps, uniqExact(RemoteAddr) AS sources FROM flows "
            f"WHERE TimeReceived >= now() - {w} AND Direction = 'in' AND ({where}) GROUP BY ip, host "
            f"HAVING bps >= {float(p['bps'])} OR pps >= {float(p['pps'])} ORDER BY bps DESC LIMIT {MAX_GROUPS}", params)
        base = {}
        if rows and float(p.get("k") or 0) > 0:
            bw = int(p["baseline_window"])
            for b in await self._q(
                    f"SELECT {ip_text('LocalAddr')} AS ip, sum(Bytes) * 8 / {bw} AS bps FROM flows "
                    f"WHERE TimeReceived >= now() - {w + bw} AND TimeReceived < now() - {w} AND Direction = 'in' "
                    f"AND LocalAddr IN ({', '.join('toIPv6(' + repr(ch_ip(r['ip'])) + ')' for r in rows)}) GROUP BY ip", {}):
                base[b["ip"]] = float(b["bps"])
        out = {}
        for r in rows:
            bps = float(r["bps"])
            if base and bps < float(p["k"]) * base.get(r["ip"], 0):
                continue
            out[r["ip"]] = {"label": f"{r['host']} ({r['ip']})" if r["host"] != r["ip"] else r["ip"], "value": bps,
                            "text": f"{fmt_rate(bps)}, {fmt_rate(float(r['pps']), 'pps')} from {r['sources']} sources",
                            "link": flows_link(self._and(ftext, f"LocalAddr = {r['ip']} AND Direction = in"),
                                               ["RemoteASName", "RemoteAddr"], 30)}
        return out

    async def _k_ddos_syn(self, p, where, params, ftext):
        w = int(p["window"])
        syn = "bitAnd(TCPFlags, 2) = 2 AND bitAnd(TCPFlags, 16) = 0"
        rows = await self._q(
            f"SELECT {ip_text('LocalAddr')} AS ip, sumIf(Packets, {syn}) / {w} AS syn_pps, "
            f"sumIf(Packets, {syn}) / greatest(sum(Packets), 1) AS ratio, uniqExactIf(RemoteAddr, {syn}) AS sources "
            f"FROM flows WHERE TimeReceived >= now() - {w} AND Direction = 'in' AND Proto = 6 AND ({where}) "
            f"GROUP BY ip HAVING syn_pps >= {float(p['pps'])} AND ratio >= {float(p['ratio'])} "
            f"ORDER BY syn_pps DESC LIMIT {MAX_GROUPS}", params)
        return {r["ip"]: {"label": r["ip"], "value": float(r["syn_pps"]),
                          "text": f"{fmt_rate(float(r['syn_pps']), 'pps')} SYN without ACK ({float(r['ratio']) * 100:.0f}% of TCP) "
                                  f"from {r['sources']} sources",
                          "link": flows_link(self._and(ftext, f"LocalAddr = {r['ip']} AND Proto = tcp"),
                                             ["RemoteAddr", "LocalPort"], 30)} for r in rows}

    async def _k_scan(self, p, where, params, ftext):
        w = int(p["window"])
        if p.get("mode") == "outbound":
            rows = await self._q(
                f"SELECT {ip_text('LocalAddr')} AS ip, {FIELDS['localhost'].column} AS host, "
                f"uniqExact(RemoteAddr) AS hosts, uniqExact(RemotePort) AS ports FROM flows "
                f"WHERE TimeReceived >= now() - {w} AND Direction = 'out' AND ({where}) GROUP BY ip, host "
                f"HAVING hosts >= {int(p['hosts'])} ORDER BY hosts DESC LIMIT {MAX_GROUPS}", params)
            return {r["ip"]: {"label": f"{r['host']} is scanning the network", "value": int(r["hosts"]),
                              "text": f"{r['hosts']} addresses and {r['ports']} ports in {w // 60} min, possibly an infected device",
                              "link": flows_link(self._and(ftext, f"LocalAddr = {r['ip']}"), ["RemotePort", "RemoteCountry"])}
                    for r in rows}
        rows = await self._q(
            f"SELECT {ip_text('RemoteAddr')} AS ip, any(RemoteCountry) AS country, any(RemoteASName) AS asname, "
            f"uniqExact(LocalPort) AS ports, uniqExact(LocalAddr) AS hosts FROM flows "
            f"WHERE TimeReceived >= now() - {w} AND Direction = 'in' AND ({where}) GROUP BY ip "
            f"HAVING ports >= {int(p['ports'])} OR hosts >= {int(p['hosts'])} ORDER BY ports DESC LIMIT {MAX_GROUPS}", params)
        return {r["ip"]: {"label": f"{r['ip']} ({r['country'] or '—'}, {r['asname'] or 'AS?'})", "value": int(r["ports"]),
                          "text": f"{r['ports']} ports on {r['hosts']} addresses in {w // 60} min",
                          "link": flows_link(self._and(ftext, f"RemoteAddr = {r['ip']}"), ["LocalAddr", "LocalPort"])}
                for r in rows}

    async def _baseline(self, p, where, params) -> tuple[list[str], dict[str, float], dict[str, list[float]]]:
        dims = [dimension(d) for d in p.get("dimensions") or []]
        table = "flows_5m" if all(d.agg for d in dims) and where == "1" else "flows"
        t0 = (await self._q("SELECT toStartOfInterval(now() - 300, INTERVAL 300 SECOND) AS t", {}))[0]["t"]
        weeks = max(1, min(int(p.get("weeks", 3)), 8 if table == "flows_5m" else 4))
        stamps = [f"toDateTime('{t0}') - INTERVAL {7 * i} DAY" for i in range(0, weeks + 1)]
        cols = ", ".join(f"{d.dimension} AS d{i}" for i, d in enumerate(dims)) or "'total' AS d0"
        names = ", ".join(f"d{i}" for i in range(max(1, len(dims))))
        rows = await self._q(
            f"SELECT {names}, toString(t) AS t, v FROM (SELECT {cols}, toStartOfInterval(TimeReceived, INTERVAL 300 SECOND) AS t, "
            f"sum(Bytes) * 8 / 300 AS v FROM {table} WHERE TimeReceived >= {stamps[-1]} AND ({where}) "
            f"AND toStartOfInterval(TimeReceived, INTERVAL 300 SECOND) IN ({', '.join(stamps)}) GROUP BY {names}, t)", params)
        week_of = {}
        for i in range(weeks + 1):
            ts = dt.datetime.strptime(t0, "%Y-%m-%d %H:%M:%S") - dt.timedelta(days=7 * i)
            week_of[ts.strftime("%Y-%m-%d %H:%M:%S")] = i
        actual: dict[str, float] = {}
        history: dict[str, list[float]] = {}
        weeks_with_data = set()
        for r in rows:
            g = " · ".join(str(r[f"d{i}"]) for i in range(max(1, len(dims))))
            i = week_of.get(r["t"])
            if i is None:
                continue
            if i == 0:
                actual[g] = float(r["v"])
            else:
                weeks_with_data.add(i)
                history.setdefault(g, [0.0] * weeks)[i - 1] = float(r["v"])
        if len(weeks_with_data) < 2:
            return [d.name for d in dims], {}, {}
        return [d.name for d in dims], actual, history

    async def _k_anomaly(self, p, where, params, ftext, drop: bool = False):
        dim_names, actual, history = await self._baseline(p, where, params)
        out = {}
        for g in set(actual) | set(history):
            a = actual.get(g, 0.0)
            hist = history.get(g, [])
            if not hist:
                continue
            med = statistics.median(hist)
            mad = statistics.median([abs(x - med) for x in hist])
            if drop:
                if med < float(p["min_bps"]) or a >= float(p["ratio"]) * med:
                    continue
                text = f"Now {fmt_rate(a)}, usually {fmt_rate(med)} (below {float(p['ratio']) * 100:.0f}%)"
            else:
                if max(a, med) < float(p["min_bps"]):
                    continue
                dev = a - med
                if abs(dev) <= max(float(p["k"]) * 1.4826 * mad, float(p["ratio"]) * med):
                    continue
                direction = p.get("direction", "up")
                if (direction == "up" and dev <= 0) or (direction == "down" and dev >= 0):
                    continue
                text = f"Now {fmt_rate(a)}, usually {fmt_rate(med)} at this time ({'+' if dev > 0 else ''}{dev / med * 100 if med else 100:.0f}%)"
            out[g] = {"label": g, "value": a, "text": text, "link": flows_link(ftext, dim_names, 180, "lines")}
        return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]["value"]))[:MAX_GROUPS])

    async def _k_traffic_drop(self, p, where, params, ftext):
        return await self._k_anomaly(p, where, params, ftext, drop=True)

    async def _k_exporter_silent(self, p, where, params, ftext):
        m = int(p.get("minutes", 10))
        rows = await self._q(
            f"SELECT {ip_text('ExporterAddress')} AS ip, {FIELDS['exportername'].column} AS name, "
            f"toString(max(TimeReceived)) AS last FROM flows WHERE TimeReceived >= now() - 86400 "
            f"AND NOT ({NO_EXPORTER}) GROUP BY ip, name HAVING max(TimeReceived) < now() - {m * 60}", {})
        return {r["ip"]: {"label": r["name"], "value": 0,
                          "text": f"No flows since {r['last']} UTC (over {m} min)", "link": "/flows?tab=exporters"}
                for r in rows}

    async def _k_threat_contact(self, p, where, params, ftext):
        w = int(p["window"])
        col = FIELDS["threat"].column
        rows = await self._q(
            f"SELECT {col} AS list, {ip_text('LocalAddr')} AS ip, {FIELDS['localhost'].column} AS host, "
            f"groupUniqArray(5)({ip_text('RemoteAddr')}) AS remotes, sum(Flows) AS flows, sum(Bytes) AS bytes "
            f"FROM flows WHERE TimeReceived >= now() - {w} AND {col} != '' AND ({where}) GROUP BY list, ip, host "
            f"HAVING flows >= {int(p.get('min_flows', 1))} ORDER BY flows DESC LIMIT {MAX_GROUPS}", params)
        return {f"{r['list']}|{r['ip']}": {
            "label": f"{r['host']} ↔ {r['list']}", "value": int(r["flows"]),
            "text": f"{r['flows']} flows with {', '.join(r['remotes'])} ({r['list']})",
            "link": flows_link(self._and(ftext, f"LocalAddr = {r['ip']} AND Threat != ''"), ["RemoteAddr", "Application"])}
            for r in rows}

    async def _k_link_utilization(self, p, where, params, ftext):
        w = int(p["window"])
        rows = await self._q(
            f"SELECT {ip_text('ea')} AS ip, idx, dir, "
            f"dictGetOrDefault('if_names', 'name', ({ip_text('ea')}, idx), toString(idx)) AS name, "
            f"dictGetOrDefault('if_names', 'speed', ({ip_text('ea')}, idx), toUInt64(0)) AS speed, "
            f"{FIELDS['exportername'].column.replace('ExporterAddress', 'ea')} AS exporter, sum(b) * 8 / {w} AS bps FROM ("
            f"SELECT ExporterAddress AS ea, InIf AS idx, 'in' AS dir, Bytes AS b FROM flows WHERE TimeReceived >= now() - {w} "
            f"AND InIf != 0 AND ({where}) UNION ALL SELECT ExporterAddress AS ea, OutIf AS idx, 'out' AS dir, Bytes AS b "
            f"FROM flows WHERE TimeReceived >= now() - {w} AND OutIf != 0 AND ({where})) GROUP BY ea, idx, dir "
            f"HAVING speed > 0 AND bps * 100 / (speed * 1000000) >= {float(p['percent'])} ORDER BY bps DESC LIMIT {MAX_GROUPS}",
            {**params})
        return {f"{r['ip']}|{r['idx']}|{r['dir']}": {
            "label": f"{r['exporter']} {r['name']} ({'in' if r['dir'] == 'in' else 'out'})", "value": float(r["bps"]),
            "text": f"{fmt_rate(float(r['bps']))} of {int(r['speed'])} Mbit/s "
                    f"({float(r['bps']) * 100 / (int(r['speed']) * 1e6):.0f}%)",
            "link": flows_link(f"Exporter = {r['ip']}", ["LocalHost", "Application"], 30)} for r in rows}
