"""Harbor with demo data, for screenshots and trying the UI without a network to manage.

The real application (app.main) runs against a real PostgreSQL; only the systems around it are stand-ins:

* SPA — the in-process OpenID Connect issuer of the tests (tests/spafake.py), mounted at /api/demo-spa and connected
  with an ordinary spa1. connection key; sign-in goes through the real authorization code flow and signs in as the
  SPA administrator "anna";
* ClickHouse — the embedded engine of the tests (chdb, tests/chfake.py) with the real flow schema and a day of flows;
* OpenBao — a dictionary in memory;
* the AmneziaWG server, the MikroTik router and the device drivers — fakes that answer with counters that grow, so
  the collector, polling, the network map and live charts work as in production;
* GeoIP — a fixed table of the demo addresses.

Run (from backend/, with the dev requirements installed):

    DEMO_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/nc_demo \\
    DEMO_PUBLIC_URL=http://127.0.0.1:4173 python -m demo.run --port 8080

DEMO_PUBLIC_URL is the address the browser opens (the web app, which proxies /api to this backend). The database is
emptied and filled again on every start. Never point it at a real installation's database.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import hashlib
import logging
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "tests")]

PUBLIC = os.environ.get("DEMO_PUBLIC_URL", "http://127.0.0.1:4173").rstrip("/")
ISSUER = f"{PUBLIC}/api/demo-spa"
DATA = Path(os.environ.get("DEMO_DATA_DIR") or tempfile.mkdtemp(prefix="nc-demo-"))
for _old in ("chdb", "backups"):  # every start begins from scratch, as the database does
    shutil.rmtree(DATA / _old, ignore_errors=True)

import spafake  # noqa: E402  (tests/spafake.py: its issuer is read at request time)

spafake.ISSUER = ISSUER
os.environ.update({
    "DATABASE_URL": os.environ.get("DEMO_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/nc_demo"),
    "AWG_HOST": "203.0.113.10", "ROUTER_HOST": "192.168.88.1", "GATEWAY_IP": "192.168.88.210",
    "LAN_CIDR": "192.168.88.0/24", "SPA_CONNECTION_KEY": spafake.connection_key(), "SPA_PUBLIC_URL": PUBLIC,
    "PUBLIC_BASE_URL": PUBLIC, "DATA_DIR": str(DATA), "BACKUP_DIR": str(DATA / "backups"),
    "GEOIP_DIR": str(DATA / "geoip"),
})

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from chfake import ChdbClient  # noqa: E402

from app import container as container_mod, main  # noqa: E402
from app.adapters.devices import PLATFORMS  # noqa: E402
from app.adapters.devices.base import Child, DeviceDriver, Endpoint, Facts, Interface, Neighbor, Snapshot  # noqa: E402
from app.adapters.mikrotik_wg import MikrotikWireguard  # noqa: E402
from app.adapters.oui import OuiDatabase  # noqa: E402
from app.db.models import Base, ConnectionEvent, NetworkDevice, TrafficBucket, VpnClient  # noqa: E402
from app.db.repositories import SettingRepo  # noqa: E402
from app.domain.models import BlockReason, ClientStatus, GeoPoint, PeerRuntime, Severity  # noqa: E402
from app.ports import GeoLocator, RouterGateway, SecretStore, VpnServerGateway  # noqa: E402
from app.services.common import Actor, utcnow  # noqa: E402
from app.services.routing import SITE_LIST_KEY  # noqa: E402
from sqlalchemy import select, update  # noqa: E402

log = logging.getLogger("demo")
ADMIN = Actor("anna", "admin")
rnd = random.Random(7)


def key(seed: str) -> str:
    """A WireGuard-looking public key, stable for a seed."""
    import base64
    return base64.b64encode(hashlib.sha256(seed.encode()).digest()).decode()


# --- OpenBao, GeoIP, OUI -----------------------------------------------------------------------------------------------

class MemorySecrets(SecretStore):
    def __init__(self, *_):
        self.d: dict[str, dict] = {}

    async def get(self, path):
        return self.d.get(path)

    async def put(self, path, data):
        self.d[path] = dict(data)

    async def delete(self, path):
        self.d.pop(path, None)

    async def keys(self, prefix):
        return sorted({p[len(prefix):].split("/")[0] + "/" for p in self.d if p.startswith(prefix)})

    async def snapshot(self) -> bytes:
        return b"demo openbao snapshot"

    async def healthy(self):
        return True


GEO = {
    "203.0.113.10": GeoPoint("NL", "Netherlands", "Amsterdam", 52.37, 4.90),
    "198.51.100.21": GeoPoint("RU", "Russia", "Moscow", 55.75, 37.62),
    "198.51.100.34": GeoPoint("RU", "Russia", "Saint Petersburg", 59.94, 30.31),
    "198.51.100.47": GeoPoint("RU", "Russia", "Kazan", 55.79, 49.12),
    "198.51.100.58": GeoPoint("GE", "Georgia", "Tbilisi", 41.72, 44.79),
    "198.51.100.66": GeoPoint("TR", "Turkey", "Istanbul", 41.01, 28.98),
    "198.51.100.73": GeoPoint("DE", "Germany", "Berlin", 52.52, 13.40),
    "198.51.100.85": GeoPoint("AM", "Armenia", "Yerevan", 40.18, 44.51),
    "198.51.100.92": GeoPoint("RU", "Russia", "Novosibirsk", 55.03, 82.92),
    "198.51.100.99": GeoPoint("RS", "Serbia", "Belgrade", 44.79, 20.45),
    "198.51.100.120": GeoPoint("AE", "United Arab Emirates", "Dubai", 25.20, 55.27),
    "192.0.2.15": GeoPoint("RU", "Russia", "Moscow", 55.75, 37.62),
}


class DemoGeo(GeoLocator):
    def __init__(self, *_):
        pass

    async def refresh(self, *_):
        return None

    def lookup(self, ip):
        return GEO.get(ip)


class DemoOui(OuiDatabase):
    async def refresh(self):
        return None


# --- the AmneziaWG server ----------------------------------------------------------------------------------------------

# name, owner, tags, endpoint (None: offline), average rate in bytes/s, has a monthly quota, days left, blocked
PEOPLE = [
    ("home-gateway", "", ["infra"], "192.0.2.15", 2_400_000, None, None, False),
    ("anna-iphone", "Anna Petrova", ["family", "mobile"], "198.51.100.21", 420_000, 200, None, False),
    ("anna-macbook", "Anna Petrova", ["family"], "198.51.100.21", 1_150_000, None, None, False),
    ("ivan-pixel", "Ivan Sidorov", ["mobile"], "198.51.100.34", 260_000, 100, 45, False),
    ("ivan-thinkpad", "Ivan Sidorov", ["work"], "198.51.100.73", 820_000, None, None, False),
    ("olga-ipad", "Olga Smirnova", ["family"], "198.51.100.58", 180_000, 50, None, False),
    ("office-laptop-3", "Office", ["work"], "198.51.100.47", 540_000, None, 120, False),
    ("dmitry-android", "Dmitry K.", ["guest"], "198.51.100.66", 90_000, 20, 6, False),
    ("maria-macbook", "Maria V.", ["work"], "198.51.100.85", 610_000, None, None, False),
    ("travel-router", "", ["infra"], "198.51.100.120", 330_000, None, None, False),
    ("sergey-desktop", "Sergey L.", ["work"], None, 0, None, None, False),
    ("guest-tablet", "Guest", ["guest"], None, 0, 10, None, True),
    ("old-phone", "Olga Smirnova", ["mobile"], None, 0, None, None, False),
]


class DemoVpnGateway(VpnServerGateway):
    def __init__(self, *_):
        self.peers = [(key(p[0]), f"10.8.1.{i + 2}/32", p) for i, p in enumerate(PEOPLE)]
        lines = ["[Interface]", "PrivateKey = " + key("server-private"), "Address = 10.8.1.1/24", "ListenPort = 51820",
                 "Jc = 4", "Jmin = 40", "Jmax = 70", "S1 = 52", "S2 = 31", "H1 = 1176323211", "H2 = 1853226617",
                 "H3 = 1490582302", "H4 = 1003914543"]
        for pub, addr, p in self.peers:
            lines += ["", ("#DISABLED# " if p[7] else "") + "[Peer]", f"PublicKey = {pub}", f"AllowedIPs = {addr}"]
        self.config = "\n".join(lines) + "\n"
        self.table = [{"clientId": pub, "userData": {"clientName": p[0]}} for pub, _, p in self.peers]
        self.counters = {pub: [rnd.randint(10**9, 5 * 10**9), rnd.randint(10**8, 10**9)] for pub, _, _ in self.peers}
        self.last = time.time()

    async def read_config(self):
        return self.config

    async def apply_config(self, text):
        self.config = text

    async def runtime(self):
        now = time.time()
        elapsed, self.last = now - self.last, now
        out = {}
        for pub, addr, p in self.peers:
            c = self.counters[pub]
            online = p[3] is not None and f"#DISABLED# [Peer]\nPublicKey = {pub}" not in self.config
            if online:
                wave = 0.55 + 0.45 * abs(((now / 37 + hash(pub) % 11) % 2) - 1) * 2
                c[0] += int(p[4] * elapsed * wave * rnd.uniform(0.6, 1.4))
                c[1] += int(p[4] * elapsed * wave * rnd.uniform(0.08, 0.2))
            out[pub] = PeerRuntime(pub, f"{p[3]}:{40000 + int(hashlib.md5(pub.encode()).hexdigest()[:3], 16)}" if p[3] else None,
                                   addr, int(now - rnd.randint(5, 90)) if online else int(now - 86400 * 3), c[1], c[0])
        return out

    async def server_public_key(self):
        return key("server-public")

    async def preshared_key(self):
        return None

    async def read_clients_table(self):
        return [dict(t) for t in self.table]

    async def write_clients_table(self, table):
        self.table = table

    async def list_backups(self):
        return ["awg0.conf.20261014-0915", "awg0.conf.20261010-1830"]

    async def read_backup(self, name):
        return self.config

    async def versions(self):
        return {"tools": "amneziawg-tools v1.0.20241018", "go": "amneziawg-go v0.2.12"}


class DemoRouter(RouterGateway):
    def __init__(self, *_):
        self.addresses: set[str] = set()

    async def status(self):
        return {"count": 3842 + len(self.addresses), "cpu": rnd.randint(3, 9), "uptime": "23d04h17m", "version": "7.16.1",
                "board": "RB5009UG+S+", "routes": [{"gateway": "10.8.1.2", "active": True, "distance": 1},
                                                    {"gateway": "203.0.113.1", "active": False, "distance": 10}],
                "mangle": [{"comment": "amnezia: mark vpn-sites", "bytes": 48_211_908_112, "packets": 41_022_118},
                           {"comment": "amnezia: route marked", "bytes": 48_102_331_904, "packets": 40_998_201}],
                "tunnel_active": True}

    async def replace_address_list(self, cidrs):
        self.addresses = set(cidrs)
        return len(cidrs)

    async def list_addresses(self):
        return sorted(self.addresses)

    async def add_address(self, cidr):
        self.addresses.add(cidr)

    async def remove_address(self, cidr):
        self.addresses.discard(cidr)


class DemoMikrotikWg(MikrotikWireguard):
    """WireGuard on the router is not set up in the demo: the page offers to set it up."""

    async def interface(self):
        return None

    async def public_address(self):
        return "203.0.113.2"

    async def peers(self):
        return []

    async def runtime(self):
        return {}

    async def peer_entries(self):
        return []


# --- network devices -----------------------------------------------------------------------------------------------

def mac(prefix: str, n: int) -> str:
    return f"{prefix}:{n // 65536 % 256:02x}:{n // 256 % 256:02x}:{n % 256:02x}"


R_MAC, SW_MAC, AP1, AP2, NAS_MAC = "48:8f:5a:3c:10:01", "ac:84:c6:5d:20:01", "60:32:b1:7a:00:11", "60:32:b1:7a:00:12", "00:11:32:aa:bb:01"
# hostname, MAC, IP, where (wired switch port | AP and SSID), signal
HOSTS = [
    ("anna-iphone", mac("3c:22:fb", 1), "192.168.88.21", (AP1, "Home"), -48),
    ("anna-macbook", mac("f0:18:98", 2), "192.168.88.22", (AP1, "Home"), -55),
    ("ivan-pixel", mac("f4:f5:d8", 3), "192.168.88.23", (AP2, "Home"), -61),
    ("ivan-thinkpad", mac("00:1b:21", 4), "192.168.88.24", ("Gi1/0/3", None), None),
    ("lg-oled-tv", mac("98:fa:9b", 5), "192.168.88.30", ("Gi1/0/5", None), None),
    ("ps5", mac("8c:79:f5", 6), "192.168.88.31", ("Gi1/0/6", None), None),
    ("yandex-station", mac("28:6c:07", 7), "192.168.88.32", (AP1, "Home"), -58),
    ("hp-printer", mac("3c:a8:2a", 8), "192.168.88.40", ("Gi1/0/7", None), None),
    ("door-camera", mac("40:a6:b7", 9), "192.168.88.41", (AP2, "IoT"), -67),
    ("yard-camera", mac("c0:51:7e", 10), "192.168.88.42", ("Gi1/0/8", None), None),
    ("roborock", mac("64:09:80", 11), "192.168.88.43", (AP2, "IoT"), -70),
    ("hue-bridge", mac("00:17:88", 12), "192.168.88.44", ("Gi1/0/11", None), None),
    ("olga-ipad", mac("a4:83:e7", 13), "192.168.88.25", (AP2, "Home"), -52),
    ("raspberry-pi", mac("dc:a6:32", 14), "192.168.88.50", ("Gi1/0/12", None), None),
    ("thermostat", mac("ec:fa:bc", 15), "192.168.88.45", (AP1, "IoT"), -63),
    ("work-laptop", mac("14:18:77", 16), "192.168.88.26", (AP1, "Home"), -45),
    ("galaxy-tab", mac("5c:49:7d", 17), "192.168.88.27", (AP2, "Guest"), -72),
    ("apple-tv", mac("bc:d0:74", 18), "192.168.88.33", ("Gi1/0/4", None), None),
]
COUNTERS: dict[str, tuple[float, float]] = {}
BOOT = time.time()


def counter(name: str, rate: float) -> int:
    """An interface byte counter that grows at about `rate` bytes/s, a little faster or slower from poll to poll."""
    now = time.time()
    last, value = COUNTERS.get(name, (now, rate * 3_600_000))
    value += (now - last) * rate * rnd.uniform(0.7, 1.3)
    COUNTERS[name] = (now, value)
    return int(value)


def snapshot(host: str) -> Snapshot:
    if host == "192.168.88.1":
        ifs = [Interface("ether1-wan", 1, "Uplink to the provider", "ether", R_MAC, True, True, 1000, 1500, ["203.0.113.2/30"],
                         counter("r1rx", 4.1e6), counter("r1tx", 0.9e6)),
               Interface("ether2", 2, "nas", "ether", mac("48:8f:5a", 0x3c1002), True, True, 2500, 1500, [], counter("r2rx", 1.2e6), counter("r2tx", 2.3e6)),
               Interface("ether3", 3, "", "ether", mac("48:8f:5a", 0x3c1003), True, False, None, 1500, [], 0, 0),
               Interface("ether4", 4, "", "ether", mac("48:8f:5a", 0x3c1004), True, False, None, 1500, [], 0, 0),
               Interface("sfp-sfpplus1", 9, "core switch", "ether", mac("48:8f:5a", 0x3c1009), True, True, 10000, 1500, [],
                         counter("r9rx", 1.1e6), counter("r9tx", 4.3e6)),
               Interface("bridge", 10, "LAN", "bridge", R_MAC, True, True, None, 1500, ["192.168.88.1/24"], counter("rbrx", 1.3e6), counter("rbtx", 4.6e6)),
               Interface("awg-gw", 11, "tunnel to the AmneziaWG gateway", "wg", "", True, True, None, 1420, ["10.8.2.1/30"],
                         counter("rwrx", 0.7e6), counter("rwtx", 0.3e6))]
        nbs = [Neighbor("sfp-sfpplus1", "sw-core", "192.168.88.2", SW_MAC, "Ten1/0/25", "TP-Link JetStream", "lldp"),
               Neighbor("ether2", "nas", "192.168.88.10", NAS_MAC, "eth0", "Linux", "lldp")]
        eps = []
        for name, m, ip, _, _ in HOSTS:
            eps += [Endpoint(m, ip, "", "bridge", "arp"), Endpoint(m, ip, name, "bridge", "dhcp")]
        eps.append(Endpoint(NAS_MAC, "192.168.88.10", "nas", "ether2", "arp"))
        return Snapshot(Facts("gw-core", "MikroTik", "RB5009UG+S+", "RouterOS", "7.16.1", "HE308E7KJ2C", R_MAC,
                              int(time.time() - BOOT) + 2_000_000, round(rnd.uniform(3, 9)), 31.0), ifs, nbs, eps)
    if host == "192.168.88.2":
        ifs = [Interface(f"Gi1/0/{i}", i, "", "ethernetCsmacd", mac("ac:84:c6", 0x5d2000 + i), True,
                         i in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12), 1000 if i in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12) else None, 1500, [],
                         counter(f"s{i}rx", 0.05e6 * i), counter(f"s{i}tx", 0.11e6 * i)) for i in range(1, 25)]
        ifs.append(Interface("Ten1/0/25", 25, "uplink gw-core", "ethernetCsmacd", SW_MAC, True, True, 10000, 1500, [],
                             counter("s25rx", 4.3e6), counter("s25tx", 1.1e6)))
        nbs = [Neighbor("Ten1/0/25", "gw-core", "192.168.88.1", R_MAC, "sfp-sfpplus1", "MikroTik", "lldp"),
               Neighbor("Gi1/0/9", "ap-hall", "192.168.88.3", AP1, "eth0", "EAP670", "lldp"),
               Neighbor("Gi1/0/10", "ap-office", "192.168.88.4", AP2, "eth0", "EAP653", "lldp")]
        eps = [Endpoint(m, "", "", where[0], "bridge", vlan=1) for _, m, _, where, _ in HOSTS if where[1] is None]
        return Snapshot(Facts("sw-core", "TP-Link", "TL-SG3428X", "JetStream", "1.0.12 Build 20240305", "2235XK1000123", SW_MAC,
                              int(time.time() - BOOT) + 5_100_000, round(rnd.uniform(4, 12)), 42.0), ifs, nbs, eps)
    if host == "192.168.88.5":
        eps = [Endpoint(m, ip, name, "", "controller", where[1], sig, None, where[0], "", rnd.uniform(1e5, 3e6), rnd.uniform(1e4, 4e5))
               for name, m, ip, where, sig in HOSTS if where[1]]
        kids = [Child(AP1, "ap-hall", "EAP670", "ap", "192.168.88.3", True, "1.1.3", SW_MAC, "Gi1/0/9",
                      sum(1 for h in HOSTS if h[3][0] == AP1)),
                Child(AP2, "ap-office", "EAP653", "ap", "192.168.88.4", True, "1.0.11", SW_MAC, "Gi1/0/10",
                      sum(1 for h in HOSTS if h[3][0] == AP2))]
        return Snapshot(Facts("Omada", "TP-Link", "OC200", "Omada Controller", "5.14.32"), [], [], eps, kids)
    # the NAS
    ifs = [Interface("eth0", 2, "", "ether", NAS_MAC, True, True, 2500, 1500, ["192.168.88.10/24"], counter("n0rx", 2.3e6), counter("n0tx", 1.2e6)),
           Interface("docker0", 3, "", "bridge", "02:42:ac:11:00:01", True, True, None, 1500, ["172.17.0.1/16"], 0, 0)]
    return Snapshot(Facts("nas", "Proxmox", "Proxmox VE", "Debian 12", "8.2.7", "", NAS_MAC, int(time.time() - BOOT) + 900_000,
                          round(rnd.uniform(8, 22)), 58.0), ifs, [Neighbor("eth0", "gw-core", "192.168.88.1", R_MAC, "ether2", "MikroTik", "lldp")], [])


class DemoDriver(DeviceDriver):
    capabilities = frozenset({"hostname", "interface", "dns", "ntp", "flow_export", "reboot", "endpoint_block", "command", "backup"})

    async def collect(self):
        await asyncio.sleep(0.2)
        return snapshot(self.target.host)

    async def apply(self, action, params):
        return "ok"

    async def backup(self):
        return "# demo configuration\n/system identity set name=gw-core\n"


for _key in ("mikrotik", "tplink_jetstream", "omada", "linux"):
    PLATFORMS[_key] = dataclasses.replace(PLATFORMS[_key], factory=DemoDriver)

# --- flows -------------------------------------------------------------------------------------------------------------

# remote prefix, AS, AS name, country, site (routed through the VPN) or "", weight, remote ports
REMOTES = [
    ("142.250.74.", 15169, "GOOGLE", "US", "YouTube", 22, (443,)),
    ("104.16.132.", 13335, "CLOUDFLARENET", "US", "", 12, (443, 80)),
    ("77.88.55.", 13238, "YANDEX", "RU", "", 10, (443,)),
    ("45.57.90.", 2906, "AS-SSI", "US", "Netflix", 14, (443,)),
    ("149.154.167.", 62041, "TELEGRAM", "GB", "Telegram", 6, (443,)),
    ("155.133.248.", 32590, "VALVE-CORPORATION", "US", "Steam", 9, (27015, 443)),
    ("17.253.144.", 714, "APPLE-ENGINEERING", "US", "", 7, (443,)),
    ("52.95.120.", 16509, "AMAZON-02", "IE", "", 6, (443,)),
    ("95.216.10.", 24940, "HETZNER-AS", "DE", "", 4, (22, 443)),
    ("87.240.190.", 47541, "VKONTAKTE-SPB-AS", "RU", "", 5, (443,)),
    ("31.13.72.", 32934, "FACEBOOK", "IE", "Instagram", 6, (443,)),
    ("162.159.130.", 13335, "CLOUDFLARENET", "US", "Discord", 3, (443, 50005)),
]
SITES = [("142.250.0.0/15", "YouTube", "video"), ("45.57.0.0/17", "Netflix", "video"), ("149.154.160.0/20", "Telegram", "messengers"),
         ("155.133.224.0/19", "Steam", "games"), ("31.13.64.0/18", "Instagram", "social"), ("162.159.128.0/19", "Discord", "messengers")]
THREATS = [("185.220.101.0/24", "firehol_level1"), ("45.155.205.0/24", "spamhaus_drop")]
LOCALS = [h[2] for h in HOSTS] + ["192.168.88.10"]
WEIGHTS = {ip: w for ip, w in zip(LOCALS, [9, 7, 5, 6, 14, 10, 3, 1, 2, 2, 1, 1, 4, 2, 1, 6, 2, 8, 5])}


def flow_rows(t0: int, t1: int, per_minute: int = 90) -> list[dict]:
    rows = []
    total_w = sum(r[5] for r in REMOTES)
    for t in range(t0, t1, 60):
        hour = dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=3))).hour
        load = 0.25 + 0.75 * max(0.0, 1 - abs(hour - 21) / 9)  # quiet nights, busy evenings
        for _ in range(max(3, int(per_minute * load))):
            pick = rnd.uniform(0, total_w)
            for r in REMOTES:
                pick -= r[5]
                if pick <= 0:
                    break
            local = rnd.choices(LOCALS, weights=[WEIGHTS.get(ip, 1) for ip in LOCALS])[0]
            down = rnd.random() < 0.72
            size = int(rnd.lognormvariate(14.2 if r[4] in ("YouTube", "Netflix", "Steam") else 12.6, 1.1) * load)
            rows.append({"TimeReceived": t + rnd.randint(0, 59), "Direction": "in" if down else "out",
                         "Route": "vpn" if r[4] else "direct", "LocalAddr": local, "RemoteAddr": r[0] + str(rnd.randint(2, 250)),
                         "Proto": 17 if r[6][0] in (27015, 50005) else 6, "LocalPort": rnd.randint(49152, 65000),
                         "RemotePort": rnd.choice(r[6]), "RemoteAS": r[1], "RemoteASName": r[2], "RemoteCountry": r[3],
                         "Bytes": size if down else size // 9, "Packets": max(1, size // 1200), "Flows": 1,
                         "ExporterAddress": "192.168.88.1", "InIf": 1 if down else 10, "OutIf": 10 if down else 1,
                         "TCPFlags": 24, "SamplingRate": 1})
        if rnd.random() < 0.03:  # a probe from a listed address
            rows.append({"TimeReceived": t, "Direction": "in", "Route": "direct", "LocalAddr": "192.168.88.10",
                         "RemoteAddr": "185.220.101." + str(rnd.randint(2, 250)), "Proto": 6, "LocalPort": 22,
                         "RemotePort": rnd.randint(30000, 60000), "RemoteAS": 208294, "RemoteASName": "CIA-TRIAD-SECURITY",
                         "RemoteCountry": "DE", "Bytes": 120, "Packets": 2, "Flows": 1, "ExporterAddress": "192.168.88.1",
                         "InIf": 1, "OutIf": 2, "TCPFlags": 2, "SamplingRate": 1})
    return rows


# --- the container -----------------------------------------------------------------------------------------------------

container_mod.OpenBaoSecretStore = MemorySecrets
container_mod.AmneziaDockerGateway = DemoVpnGateway
container_mod.MikrotikSshRouter = DemoRouter
container_mod.MikrotikWireguard = DemoMikrotikWg
container_mod.DbIpGeoLocator = DemoGeo
container_mod.OuiDatabase = DemoOui
container_mod.ClickHouseClient = lambda *a: ChdbClient(str(DATA / "chdb"))


class PrefixTransport(httpx.AsyncBaseTransport):
    """The backend reaches the demo SPA in-process, through the same mount the browser uses."""

    def __init__(self, app):
        self._inner = httpx.ASGITransport(app=app)

    async def handle_async_request(self, request):
        return await self._inner.handle_async_request(request)


class DemoContainer(container_mod.Container):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.spa._transport = PrefixTransport(main.app)

    async def start(self) -> None:
        await self.bus.start()
        await self.live.start()
        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.db.create_schema()
        try:
            await seed(self)
        except Exception:
            log.exception("demo: seeding failed")
            raise
        await self.election.start()

    async def refresh_threats(self) -> dict:  # no downloads in the demo: the lists are seeded
        return {}

    async def refresh_ptr(self) -> None:
        return None

    async def _become_leader(self) -> None:
        await super()._become_leader()
        assert self.scheduler is not None
        from app.workers import PeriodicJob

        async def more_flows() -> None:
            now = int(time.time())
            await self.clickhouse.insert("flows", flow_rows(now - 60, now))
        job = PeriodicJob("demo-flows", 60, more_flows, initial_delay=30)
        self.scheduler.add(job)
        self.scheduler._tasks.append(asyncio.create_task(self.scheduler._loop(job), name=job.name))


async def seed(c: DemoContainer) -> None:
    now = utcnow()
    fake: spafake.FakeSpa = SPA
    fake.add_user("usr_anna", "anna", ["admin"], name="Anna Petrova", groups=["network-admins"])
    fake.add_user("usr_ivan", "ivan", ["operator"], name="Ivan Sidorov", groups=["noc"])
    fake.add_user("usr_olga", "olga", ["viewer"], name="Olga Smirnova", groups=["family"])
    fake.signed_in = "usr_anna"
    await c.spa.sync()

    await c.settings.update({"poll_seconds": 10, "default_valid_days": 365, "backup_enabled": True,
                             "threat_status": {"lists": {"firehol_level1": 4512, "spamhaus_drop": 1380}, "prefixes": 5892,
                                               "updated_at": now.isoformat()}})
    # VPN clients come from the server config, as in production; then their owners, quotas and history.
    await c.clients.sync()
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    async with c.db.session() as s:
        rows = {r.name: r for r in (await s.execute(select(VpnClient))).scalars()}
        for name, owner, tags, ep, rate, quota, days, blocked in PEOPLE:
            r = rows[name]
            r.owner, r.tags, r.managed = owner, tags, True
            r.system = name == "home-gateway"
            r.expires_at = now + dt.timedelta(days=days) if days else None
            r.created_by = "anna"
            r.config_downloaded_at = now - dt.timedelta(days=rnd.randint(3, 80))
            # The collector counts from the server's current counters on (as after a sync in production).
            r.raw_tx, r.raw_rx = c.vpn_gateway.counters[r.public_key]
            if blocked:
                r.status, r.block_reason = ClientStatus.BLOCKED, BlockReason.MANUAL
            # 30 days of hourly traffic, busy in the evening.
            used = 0
            for h in range(30 * 24, 0, -1):
                t = (now - dt.timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
                if not rate and h < 24 * 4:
                    continue
                local_hour = (t.hour + 3) % 24
                load = 0.2 + 0.8 * max(0.0, 1 - abs(local_hour - 21) / 8)
                down = int((rate or 150_000) * 1200 * load * rnd.uniform(0.3, 1.3))
                s.add(TrafficBucket(client_id=r.id, bucket=t, rx=down // 8, tx=down))
                r.total_tx += down
                r.total_rx += down // 8
                if t >= month:
                    used += down + down // 8
            # A monthly quota above this month's use (dmitry-android close to it, for the warning).
            if quota:
                r.quota_bytes = -(-int(used * (1.2 if name == "dmitry-android" else 2.2)) // 1024**3) * 1024**3
            for d in range(0, 30, rnd.randint(2, 5)):
                ip = ep or rnd.choice(list(GEO)[1:])
                g = GEO.get(ip)
                s.add(ConnectionEvent(client_id=r.id, ts=now - dt.timedelta(days=d, hours=rnd.randint(0, 12)), ip=ip,
                                      port=40000 + d, country_code=g.country_code, country=g.country, city=g.city,
                                      lat=g.lat, lon=g.lon))

    # Devices, polled through the demo drivers as in production.
    for d in ({"name": "gw-core", "platform": "mikrotik", "host": "192.168.88.1", "role": "router", "location": "Hall cabinet",
               "transport": "api", "use_system_key": False, "flow_exporter_ip": "192.168.88.1", "tags": ["core"]},
              {"name": "sw-core", "platform": "tplink_jetstream", "host": "192.168.88.2", "role": "switch", "location": "Hall cabinet",
               "tags": ["core"]},
              {"name": "Omada", "platform": "omada", "host": "192.168.88.5", "role": "controller", "location": "Hall cabinet",
               "site": "Home"},
              {"name": "nas", "platform": "linux", "host": "192.168.88.10", "role": "server", "location": "Hall cabinet"}):
        await c.devices.create(ADMIN, {**d, "credentials": {"username": "monitor", "password": "demo"}, "poll_seconds": 60})
    await asyncio.sleep(2.5)  # creation polls in the background

    # Routing: the compiled site list and a custom address.
    async with c.db.session() as s:
        await SettingRepo(s).put(SITE_LIST_KEY, {"sites": 214, "prefixes": 3842, "coverage_percent": 1.37,
                                                 "groups": {"video": 38, "social": 41, "messengers": 22, "games": 29, "news": 47, "other": 37},
                                                 "site_names": sorted(["youtube", "netflix", "telegram", "steam", "instagram",
                                                                       "discord", "twitch", "spotify", "linkedin", "medium"])})
    # Flows: the real schema in the embedded ClickHouse, a day of flows, sites, threat lists and enrichment.
    await c.flows.schema.ensure(30)
    await c.flows.load_sites(SITES)
    c.flows.sites_seeded = True
    await c.routing.add_custom(ADMIN, "203.0.113.128/25", "Office VPN range")
    nowt = int(time.time())
    rows = flow_rows(nowt - 86400, nowt)
    for i in range(0, len(rows), 20000):
        await c.clickhouse.insert("flows", rows[i:i + 20000])
    await c.clickhouse.command("INSERT INTO flows.threat_prefixes VALUES " + ", ".join(f"('{p}', '{n}')" for p, n in THREATS))
    await c.clickhouse.command("SYSTEM RELOAD DICTIONARY flows.threats")
    await c.detector.seed_defaults()
    await c.sync_enrichment()
    await c.flows.save_view(ADMIN, "Evening video through the VPN", {"dims": ["Site"], "range": "24h"}, True)

    # The map: the core devices placed, the rest laid out automatically.
    async with c.db.session() as s:
        for name, x, y in (("gw-core", 420.0, 180.0), ("sw-core", 420.0, 340.0)):
            await s.execute(update(NetworkDevice).where(NetworkDevice.name == name).values(map_x=x, map_y=y))

    await c.backups.create(ADMIN)
    await c.alerts.raise_("demo.exporter", Severity.INFO, "New flow exporter: gw-core", "192.168.88.1 sends NetFlow v9")

    # The live chart starts with the last twenty minutes.
    for i in range(120, 0, -1):
        t = now - dt.timedelta(seconds=10 * i)
        wave = 1 + 0.35 * __import__("math").sin(i / 9)
        c.live.history.append({"t": t.isoformat(), "rx": int(5.8e6 * wave * rnd.uniform(0.8, 1.2)),
                               "tx": int(0.9e6 * wave * rnd.uniform(0.8, 1.2)), "online": 10})
    log.info("demo: seeded; sign in at %s (SPA user anna, Administrator)", PUBLIC)


SPA = spafake.FakeSpa()
main.Container = DemoContainer
main.app.mount("/api/demo-spa", SPA.app)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    uvicorn.run(main.app, host=a.host, port=a.port, log_level="info", proxy_headers=True, forwarded_allow_ips="*")
