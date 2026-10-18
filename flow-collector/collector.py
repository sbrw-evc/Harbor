import asyncio
import datetime as dt
import gzip
import ipaddress
import json
import logging
import os
import time

import httpx
import maxminddb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collector")
logging.getLogger("httpx").setLevel(logging.WARNING)

CH_URL = os.environ.get("CLICKHOUSE_URL", "http://clickhouse:8123")
CH_USER = os.environ.get("CLICKHOUSE_USER", "amnezia")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CH_DB = os.environ.get("CLICKHOUSE_DB", "flows")
# The defaults below are starting values of private networks; collector.json of the panel replaces them.
LAN = [ipaddress.ip_network(n.strip(), strict=False)
       for n in os.environ.get("LAN_CIDR", "192.168.0.0/24").split(",") if n.strip()]  # NOSONAR
# IPv6 unique local and link-local addresses never leave the site: always local.
ALWAYS_LOCAL = [ipaddress.ip_network("fc00::/7"), ipaddress.ip_network("fe80::/10")]
EXTRA_LAN: list = []
GATEWAY = os.environ.get("GATEWAY_IP", "192.168.0.210")  # NOSONAR
VPN_HOST = os.environ.get("AWG_HOST", "")
ROUTER = os.environ.get("ROUTER_HOST", "")
CONFIG_FILE = os.environ.get("COLLECTOR_CONFIG", "/shared/collector.json")
GEO_DIR = os.environ.get("GEOIP_DIR", "/data")
LISTEN = os.environ.get("FLOW_LISTEN", "netflow://:2055,sflow://:6343")
KEEP_INTERNAL = os.environ.get("KEEP_INTERNAL", "1") == "1"
BATCH = int(os.environ.get("BATCH_SIZE", "5000"))
FLUSH_SECONDS = float(os.environ.get("FLUSH_SECONDS", "5"))
PROTOCOLS = {"ICMP": 1, "TCP": 6, "UDP": 17, "GRE": 47, "ESP": 50, "AH": 51, "SCTP": 132}
DATABASES = {"city": "dbip-city-lite", "asn": "dbip-asn-lite"}


def is_local(addr) -> bool:
    return any(addr in n for n in LAN) or any(addr in n for n in EXTRA_LAN) or any(addr in n for n in ALWAYS_LOCAL)


def service_port(lport: int, rport: int) -> int:
    return lport if lport and (not rport or lport < rport) else rport


def address(value):
    """An address as goflow2 renders it (IPv4, IPv6 or IPv4-mapped IPv6) → ipaddress object; None when absent."""
    v = str(value or "").strip().split("%")[0]
    if not v:
        return None
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return None if a.is_unspecified else a


def column(a) -> str:
    """The text of an IPv6 column: IPv4 as ::ffff:a.b.c.d, which every ClickHouse version parses."""
    if a is None:
        # The unspecified address: the empty value of the column.
        return "::ffff:0.0.0.0"  # NOSONAR
    return f"::ffff:{a}" if a.version == 4 else str(a)


def exporter_address(flow: dict) -> str:
    a = address(pick(flow, "sampler_address", "SamplerAddress", default=""))
    return str(a) if a else "0.0.0.0"


BROADCAST = ipaddress.ip_address("255.255.255.255")


class GeoEnricher:
    def __init__(self, directory: str):
        self._dir = directory
        self._readers: dict[str, maxminddb.Reader] = {}
        self._cache: dict[str, tuple] = {}

    async def refresh(self) -> None:
        os.makedirs(self._dir, exist_ok=True)
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as http:
            for kind, base in DATABASES.items():
                path = os.path.join(self._dir, f"{base}.mmdb")
                if os.path.exists(path) and time.time() - os.path.getmtime(path) < 35 * 86400:
                    self._open(kind, path)
                    continue
                today = dt.date.today()
                for d in (today, today.replace(day=1) - dt.timedelta(days=1)):
                    try:
                        r = await http.get(f"https://download.db-ip.com/free/{base}-{d.strftime('%Y-%m')}.mmdb.gz")
                    except httpx.HTTPError as e:
                        log.warning("geo download %s: %s", base, e)
                        continue
                    if r.status_code == 200:
                        await asyncio.to_thread(self._install, path, r.content)
                        log.info("downloaded %s", base)
                        break
                if os.path.exists(path):
                    self._open(kind, path)
        self._cache.clear()

    @staticmethod
    def _install(path: str, compressed: bytes) -> None:
        with open(path + ".tmp", "wb") as f:
            f.write(gzip.decompress(compressed))
        os.replace(path + ".tmp", path)

    def _open(self, kind: str, path: str) -> None:
        self._readers[kind] = maxminddb.open_database(path)

    def lookup(self, ip: str) -> tuple[str, int, str]:
        hit = self._cache.get(ip)
        if hit:
            return hit
        country, asn, org = "", 0, ""
        city = self._readers.get("city")
        if city:
            rec = city.get(ip) or {}
            country = (rec.get("country") or {}).get("iso_code") or ""
        asdb = self._readers.get("asn")
        if asdb:
            rec = asdb.get(ip) or {}
            asn = int(rec.get("autonomous_system_number") or 0)
            org = rec.get("autonomous_system_organization") or ""
        if len(self._cache) > 200_000:
            self._cache.clear()
        self._cache[ip] = (country, asn, org)
        return self._cache[ip]


def pick(flow: dict, *names, default=None):
    for n in names:
        if n in flow and flow[n] not in (None, ""):
            return flow[n]
    return default


class Classifier:
    def __init__(self, geo: GeoEnricher):
        self._geo = geo

    def __call__(self, flow: dict) -> dict | None:
        s, d = address(pick(flow, "src_addr", "SrcAddr")), address(pick(flow, "dst_addr", "DstAddr"))
        if s is None or d is None:
            return None
        src, dst = str(s), str(d)
        internal = is_local(s) and is_local(d)
        if internal and not KEEP_INTERNAL:
            return None
        if VPN_HOST and VPN_HOST in (src, dst):
            return None
        if s.is_multicast or d.is_multicast or d == BROADCAST:
            return None
        proto = pick(flow, "proto", "Proto", default=0)
        proto = PROTOCOLS.get(str(proto).upper(), proto) if not str(proto).isdigit() else int(proto)
        sport, dport = int(pick(flow, "src_port", "SrcPort", default=0)), int(pick(flow, "dst_port", "DstPort", default=0))
        if internal:
            direction, local, remote, lport, rport, route = "internal", src, dst, sport, dport, "direct"
            country, asn, org = "", 0, ""
        elif is_local(s):
            direction, local, remote, lport, rport = "out", src, dst, sport, dport
            route = "vpn" if str(address(pick(flow, "next_hop", "NextHop", default="")) or "") == GATEWAY else "direct"
        else:
            direction, local, remote, lport, rport = "in", dst, src, dport, sport
            # replies of tunnelled connections come back from the gateway on the router's LAN side
            exporter = exporter_address(flow)
            route = "vpn" if is_local(d) and (not ROUTER or exporter in (ROUTER, "0.0.0.0")) else "direct"
        if not internal:
            country, asn, org = self._geo.lookup(remote)
        rate = int(pick(flow, "sampling_rate", "SamplingRate", default=1) or 1)
        received = int(pick(flow, "time_received_ns", "TimeReceivedNs", default=0)) // 1_000_000_000 \
            or int(pick(flow, "time_received", "TimeReceived", default=0)) or int(time.time())
        in_if, out_if = int(pick(flow, "in_if", "InIf", default=0)), int(pick(flow, "out_if", "OutIf", default=0))
        return {"TimeReceived": received, "Direction": direction, "Route": route,
                "LocalAddr": column(ipaddress.ip_address(local)), "RemoteAddr": column(ipaddress.ip_address(remote)),
                "Proto": int(proto or 0), "LocalPort": lport, "RemotePort": rport, "RemoteAS": asn, "RemoteASName": org,
                "RemoteCountry": country, "Bytes": int(pick(flow, "bytes", "Bytes", default=0)) * rate,
                "Packets": int(pick(flow, "packets", "Packets", default=0)) * rate, "Flows": 1,
                "ExporterAddress": column(address(pick(flow, "sampler_address", "SamplerAddress", default=""))),
                "InIf": in_if, "OutIf": out_if, "TCPFlags": int(pick(flow, "tcp_flags", "TCPFlags", default=0)) & 0xFF,
                "SamplingRate": rate, "ServicePort": service_port(lport, rport)}


def load_config(path: str = CONFIG_FILE) -> bool:
    """Reads collector.json, written by the panel from its settings (the networks that count as local, the gateway,
    the VPS, the router, ClickHouse). Returns whether the ClickHouse connection changed. Without the file the
    environment of older installations stays in force."""
    global LAN, GATEWAY, VPN_HOST, ROUTER, CH_URL, CH_USER, CH_PASSWORD, CH_DB
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return False
    nets = []
    for n in str(cfg.get("lan_cidr") or "").split(","):
        try:
            nets.append(ipaddress.ip_network(n.strip(), strict=False))
        except ValueError:
            continue
    LAN = nets or LAN
    GATEWAY = cfg.get("gateway_ip") or GATEWAY
    VPN_HOST = cfg.get("awg_host") or ""
    ROUTER = cfg.get("router_host") or ""
    ch = cfg.get("clickhouse") or {}
    new = (ch.get("url") or CH_URL, ch.get("user") or CH_USER, ch.get("password", CH_PASSWORD), ch.get("database") or CH_DB)
    changed = new != (CH_URL, CH_USER, CH_PASSWORD, CH_DB)
    CH_URL, CH_USER, CH_PASSWORD, CH_DB = new
    return changed


class ClickHouseWriter:
    def __init__(self):
        self.connect()
        self.written = 0

    def connect(self) -> None:
        self._http = httpx.AsyncClient(base_url=CH_URL, timeout=60,
                                       headers={"X-ClickHouse-User": CH_USER, "X-ClickHouse-Key": CH_PASSWORD})

    async def wait_ready(self) -> None:
        while True:
            try:
                r = await self._http.post("/", params={"query": f"EXISTS TABLE {CH_DB}.flows"})
                if r.status_code == 200 and r.text.strip() == "1":
                    return
            except httpx.HTTPError:
                pass
            log.info("waiting for %s.flows", CH_DB)
            await asyncio.sleep(10)

    async def local_networks(self) -> list:
        """Subnets learned from the managed devices (written by the backend), refreshed every minute."""
        try:
            r = await self._http.post("/", params={"query": f"SELECT prefix FROM {CH_DB}.local_networks FORMAT TSV"})
            if r.status_code != 200:
                return []
        except httpx.HTTPError:
            return []
        out = []
        for line in r.text.splitlines():
            try:
                out.append(ipaddress.ip_network(line.strip(), strict=False))
            except ValueError:
                pass
        return out

    async def write(self, rows: list[dict]) -> None:
        body = "\n".join(json.dumps(r) for r in rows).encode()
        for attempt in range(5):
            try:
                r = await self._http.post("/", params={"query": f"INSERT INTO {CH_DB}.flows FORMAT JSONEachRow"},
                                          content=body)
                if r.status_code == 200:
                    self.written += len(rows)
                    return
                log.warning("insert failed: %s", r.text[:300])
            except httpx.HTTPError as e:
                log.warning("insert error: %s", e)
            await asyncio.sleep(2 * (attempt + 1))
        log.error("dropping %d flows after retries", len(rows))


async def wait_for_config(every: float = 10) -> None:
    """collector.json is written by the panel at the end of its setup wizard, in another container: nothing can tell
    this process about it, so it looks again every few seconds."""
    while True:
        load_config()
        if CH_PASSWORD or os.path.exists(CONFIG_FILE):
            return
        log.info("waiting for %s: finish the setup wizard of the panel", CONFIG_FILE)
        await asyncio.sleep(every)


async def run() -> None:
    await wait_for_config()
    geo = GeoEnricher(GEO_DIR)
    await geo.refresh()
    classify = Classifier(geo)
    writer = ClickHouseWriter()
    await writer.wait_ready()
    buffer: list[dict] = []
    last_flush = time.monotonic()
    stats = {"received": 0, "skipped": 0}

    async def flush() -> None:
        nonlocal buffer, last_flush
        if buffer:
            rows, buffer = buffer, []
            await writer.write(rows)
        last_flush = time.monotonic()

    async def periodic() -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            if time.monotonic() - last_flush >= FLUSH_SECONDS:
                await flush()

    async def report() -> None:
        while True:
            await asyncio.sleep(300)
            log.info("flows received=%d skipped=%d written=%d", stats["received"], stats["skipped"], writer.written)

    async def georefresh() -> None:
        while True:
            await asyncio.sleep(86400)
            await geo.refresh()

    async def networks() -> None:
        while True:
            if load_config():
                log.info("ClickHouse settings changed: reconnecting")
                writer.connect()
            nets = await writer.local_networks()
            EXTRA_LAN[:] = nets
            await asyncio.sleep(60)

    tasks = [asyncio.create_task(periodic()), asyncio.create_task(report()), asyncio.create_task(georefresh()),
             asyncio.create_task(networks())]
    while True:
        proc = await asyncio.create_subprocess_exec(
            "goflow2", f"-listen={LISTEN}", "-format=json", "-transport=file",
            stdout=asyncio.subprocess.PIPE, limit=1 << 20)
        log.info("goflow2 listening on %s", LISTEN)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            stats["received"] += 1
            try:
                row = classify(json.loads(line))
            except (ValueError, KeyError):
                row = None
            if row is None:
                stats["skipped"] += 1
                continue
            buffer.append(row)
            if len(buffer) >= BATCH:
                await flush()
        log.warning("goflow2 exited with %s, restarting", await proc.wait())
        await flush()
        await asyncio.sleep(3)
    for t in tasks:
        t.cancel()


if __name__ == "__main__":
    asyncio.run(run())
