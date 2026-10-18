import ipaddress
import re
import time
from urllib.parse import urlsplit

from ..db.base import Database
from ..db.repositories import SettingRepo

DEFAULTS = {
    "client_dns": "1.1.1.1, 1.0.0.1",
    "endpoint_host": "",
    "poll_seconds": 10,
    "retention_days": 90,
    "quota_warn_percent": 80,
    "expiry_warn_days": 3,
    "default_quota_gb": None,
    "default_valid_days": None,
    "share_link_hours": 24,
    "timezone": "Europe/Moscow",
    "gateway_stale_minutes": 5,
    "notify_enabled": False,
    "notify_min_severity": "warning",
    "notify_telegram_chat_id": "",
    "backup_enabled": True,
    "backup_keep": 14,
    "mt_endpoint_host": "",
    "mt_client_dns": "",
    "mt_allowed_ips": "0.0.0.0/0",
    "endpoint_offline_minutes": 15,
    "endpoint_retention_days": 90,
    "flow_collector_host": "",
    "flow_collector_port": 2055,
    "threat_enabled": True,
    "threat_refresh_hours": 6,
    "threat_lists": None,
    "threat_exclude": "",
    "rdns_enabled": True,
    "threat_status": None,
    # The infrastructure the panel manages, set in the setup wizard and on the settings page (was .env before).
    # The Amnezia server (VPS) reached over SSH, with its AmneziaWG container.
    "awg_host": "",
    "awg_ssh_user": "root",
    "awg_container": "amnezia-awg2",
    "awg_iface": "awg0",
    "awg_bin": "awg",
    "awg_dir": "/opt/amnezia/awg",
    # The home router (MikroTik, RouterOS 7) reached over SSH.
    "router_host": "",
    "router_ssh_user": "admin",
    # The gateway: the host of the panel with the awg-client container, its LAN and the tunnel behind it.
    "gateway_ip": "",
    # Starting values of private networks, changed in the panel (NOSONAR: defaults, not addresses to reach).
    "lan_cidr": "192.168.0.0/24",  # NOSONAR
    "lan_if": "eth0",
    "tunnel_net": "10.8.1.0/24",  # NOSONAR
    "port_forwards": "",
    # WireGuard on MikroTik.
    "mt_wg_iface": "wg-nc",
    "mt_wg_network": "10.9.0.0/24",  # NOSONAR: a starting value, changed in the panel
    "mt_wg_port": 51820,
    # WireGuard on OPNsense (REST API; the API key and secret are in OpenBao, opnsense/api).
    "opn_host": "",
    "opn_wg_instance": "wg0",
    "opn_verify_tls": False,
    "opn_endpoint_host": "",
    "opn_client_dns": "",
    "opn_allowed_ips": "0.0.0.0/0",
    # The panel itself.
    "public_base_url": "",
    "spa_public_url": "",
    "session_ttl_hours": 12,
    "cookie_secure": False,
    "flows_ttl_days": 30,
    # How people sign in: "local" (users of the panel, passwords and TOTP in OpenBao) or "keystone" (Keystone / SPA).
    # Empty until the setup wizard chooses.
    "auth_mode": "",
    # The state of the SPA connection (services/identity/spa.py): last synchronisation, its ETag, counts, errors and
    # the configuration SPA gave (application name, account and sign-out addresses).
    "spa": None,
}


# Kept by their services; not listed or changed through the general settings API.
PRIVATE = frozenset({"spa", "auth_mode"})

# The settings of the infrastructure: a change rebuilds the adapters (app/runtime.py).
INFRA = frozenset({"awg_host", "awg_ssh_user", "awg_container", "awg_iface", "awg_bin", "awg_dir", "router_host",
                   "router_ssh_user", "gateway_ip", "lan_cidr", "lan_if", "tunnel_net", "port_forwards", "mt_wg_iface",
                   "mt_wg_network", "mt_wg_port", "opn_host", "opn_wg_instance", "opn_verify_tls", "public_base_url",
                   "spa_public_url", "session_ttl_hours", "cookie_secure", "flows_ttl_days"})

_HOST = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PATH = re.compile(r"^/[A-Za-z0-9_./-]{0,255}$")
_FORWARD = re.compile(r"^(\d{1,5})=([0-9.]+):(\d{1,5})$")


def _host(v, empty: bool = True) -> str:
    v = str(v or "").strip()
    if not v and empty:
        return ""
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        if _HOST.match(v):
            return v.lower()
    raise ValueError("an IP address or a host name")


def _ip(v, empty: bool = True) -> str:
    v = str(v or "").strip()
    if not v and empty:
        return ""
    try:
        return str(ipaddress.ip_address(v))
    except ValueError as e:
        raise ValueError("an IP address") from e


def _cidr(v) -> str:
    try:
        return str(ipaddress.ip_network(str(v).strip(), strict=False))
    except ValueError as e:
        raise ValueError("a network such as 192.168.0.0/24") from e


def _name(v) -> str:
    v = str(v or "").strip()
    if not _NAME.match(v):
        raise ValueError("letters, digits, '-', '_' or '.'")
    return v


def _path(v) -> str:
    v = str(v or "").strip().rstrip("/") or "/"
    if not _PATH.match(v):
        raise ValueError("an absolute path")
    return v


def _url(v) -> str:
    v = str(v or "").strip().rstrip("/")
    if not v:
        return ""
    u = urlsplit(v)
    if u.scheme not in ("http", "https") or not u.hostname or u.query or u.fragment:
        raise ValueError("an http(s) address such as https://nc.example.com")
    return v


def _int(lo: int, hi: int):
    def check(v) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError) as e:
            raise ValueError(f"a number from {lo} to {hi}") from e
        if not lo <= n <= hi:
            raise ValueError(f"a number from {lo} to {hi}")
        return n
    return check


def _forwards(v) -> str:
    out = []
    for item in str(v or "").replace(",", " ").split():
        m = _FORWARD.match(item)
        if not m or not 0 < int(m[1]) < 65536 or not 0 < int(m[3]) < 65536:
            raise ValueError("pairs like 8443=192.168.0.10:443 separated by spaces")
        _ip(m[2], empty=False)
        out.append(item)
    return " ".join(out)


def _flag(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _ips(v) -> str:
    items = str(v or "").replace(",", " ").split()
    return ", ".join(_ip(x, empty=False) for x in items)


def _cidrs(v) -> str:
    items = str(v or "").replace(",", " ").split()
    if not items:
        raise ValueError("networks such as 0.0.0.0/0, ::/0")
    return ", ".join(_cidr(x) for x in items)


VALIDATORS = {
    "awg_host": _host, "awg_ssh_user": _name, "awg_container": _name, "awg_iface": _name, "awg_bin": _name,
    "awg_dir": _path, "router_host": _host, "router_ssh_user": _name, "gateway_ip": _ip, "lan_cidr": _cidr,
    "lan_if": _name, "tunnel_net": _cidr, "port_forwards": _forwards, "mt_wg_iface": _name, "mt_wg_network": _cidr,
    "mt_wg_port": _int(1, 65535), "opn_host": _host, "opn_wg_instance": _name, "opn_verify_tls": _flag,
    "opn_endpoint_host": _host, "opn_client_dns": _ips, "opn_allowed_ips": _cidrs, "public_base_url": _url,
    "spa_public_url": _url, "session_ttl_hours": _int(1, 720),
    "cookie_secure": bool, "flows_ttl_days": _int(1, 3650),
}


def validate(values: dict) -> dict:
    """Checks and normalises the settings with a validator; the message names the setting."""
    out = dict(values)
    for k, v in values.items():
        check = VALIDATORS.get(k)
        if check is None:
            continue
        try:
            out[k] = check(v)
        except ValueError as e:
            raise ValueError(f"Setting {k}: expected {e}") from e
    return out


class SettingsService:
    TTL = 15

    def __init__(self, db: Database):
        self._db = db
        self._cache: dict | None = None
        self._stored: frozenset[str] = frozenset()
        self._loaded = 0.0

    async def _load(self) -> dict:
        if self._cache is None or time.monotonic() - self._loaded > self.TTL:
            self._loaded = time.monotonic()
            async with self._db.session() as s:
                stored = await SettingRepo(s).get_all()
            self._stored = frozenset(k for k in stored if k in DEFAULTS)
            self._cache = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
        return self._cache

    async def all(self) -> dict:
        return {k: v for k, v in (await self._load()).items() if k not in PRIVATE}

    async def get(self, key: str):
        return (await self._load())[key]

    async def stored(self) -> frozenset[str]:
        """The keys saved in the database (the rest are defaults)."""
        await self._load()
        return self._stored

    def invalidate(self) -> None:
        self._cache = None

    async def put_private(self, key: str, value) -> None:
        """Stores one of the private settings (kept by its service)."""
        if key not in PRIVATE:
            raise ValueError(key)
        async with self._db.session() as s:
            await SettingRepo(s).put(key, value)
        self._cache = None

    async def update(self, values: dict, *, internal: bool = False) -> dict:
        """Saves settings after validation. ``internal`` (the setup wizard, the import of old .env values) may also
        write the private ones."""
        allowed = set(DEFAULTS) if internal else set(DEFAULTS) - PRIVATE
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        values = validate(values)
        async with self._db.session() as s:
            repo = SettingRepo(s)
            for k, v in values.items():
                await repo.put(k, v)
        self._cache = None
        return await self.all()
