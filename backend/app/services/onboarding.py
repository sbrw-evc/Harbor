"""Business processes of a new installation, after Umbrella Monitoring: the "First steps" checklist of the overview page
(GET /api/onboarding) and the step-by-step setup guides of the key pages (GET /api/guides/{page}).

Every step is computed from real data and settings — the SPA state, the devices that answered, the flows in
ClickHouse, the VPN clients, the Telegram bot in OpenBao, the backups on disk — never from a "done" flag a person
ticks. A step has the page that fixes it (``path``) or, in a page guide, the button of that page (``action``), and
``can_fix`` tells whether the signed-in role may do it. The checklist and a guide are done when every required step is.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from sqlalchemy import func, select

from ..db.base import Database
from ..db.models import Alert, FlowRule, NetworkDevice, NetworkEndpoint, PanelUser, SavedView, VpnClient
from ..db.repositories import SettingRepo
from ..domain.models import Role
from .routing import CUSTOM_KEY, SITE_LIST_KEY
from .settings import SettingsService

log = logging.getLogger(__name__)

CHECK_SECONDS = 4.0
LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# The steps of the "First steps" checklist, in the order to do them.
STEP_SPA = "spa"
STEP_ROUTER = "router"
STEP_VPS = "vps"
STEP_GATEWAY = "gateway"
STEP_ADDRESS = "address"
STEP_DEVICE = "device"
STEP_FLOWS = "flows"
STEP_VPN_CLIENT = "vpn_client"
STEP_TELEGRAM = "telegram"
STEP_BACKUPS = "backups"

# Guide actions: a step done with a button of the page itself (the web app maps each to that button).
ACTION_CREATE = "create"
ACTION_DISCOVER = "discover"
ACTION_UPLOAD = "upload"
ACTION_SYNC = "sync"


def is_public_address(url: str) -> bool:
    """An address people and SPA can reach: an http(s) URL whose host is not a loopback name."""
    try:
        u = urlsplit(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and bool(host) and host not in LOOPBACK and not host.endswith(".localhost")


@dataclass
class Facts:
    """What the checklist and the guides are computed from, read in one pass."""
    local_mode: bool = False
    local_admins: int = 0
    router_set: bool = False
    vps_set: bool = False
    vps_ok: bool = False
    gateway_set: bool = False
    gateway_client: bool = False
    spa_connected: bool = False
    spa_synced: bool = False
    spa_error: bool = False
    spa_admins: int = 0
    address: bool = False
    devices: int = 0
    devices_up: int = 0
    router_up: bool = False
    devices_on_map: bool = False
    endpoints: int = 0
    flows: bool = False
    flow_rules: int = 0
    threats_loaded: bool = False
    views: int = 0
    vpn_clients: int = 0
    configs_issued: bool = False
    clients_connected: bool = False
    default_policy: bool = False
    telegram: bool = False
    backups: bool = False
    site_list: bool = False
    custom_prefixes: bool = False


@dataclass
class Step:
    id: str
    done: bool
    optional: bool = False
    path: str = ""
    action: str = ""
    role: Role = Role.ADMIN
    can_fix: bool = field(default=False)

    def view(self) -> dict:
        d = asdict(self)
        d.pop("role")
        return d


def _checklist(f: Facts) -> list[Step]:
    return [
        # Sign-in: a local administrator (standalone) or a working Keystone connection with an administrator.
        Step(STEP_SPA, f.local_admins > 0 if f.local_mode
             else f.spa_connected and f.spa_synced and not f.spa_error and f.spa_admins > 0, path="/settings#spa"),
        Step(STEP_ROUTER, f.router_set and f.router_up, path="/settings#infrastructure"),
        Step(STEP_VPS, f.vps_set and f.vps_ok, optional=True, path="/settings#infrastructure"),
        Step(STEP_GATEWAY, f.gateway_set and f.gateway_client, optional=True, path="/settings#infrastructure"),
        Step(STEP_ADDRESS, f.address, path="/settings#spa"),
        Step(STEP_DEVICE, f.devices_up > 0, path="/devices"),
        Step(STEP_FLOWS, f.flows, path="/flows?tab=exporters" if f.devices_up else "/devices", role=Role.OPERATOR),
        Step(STEP_VPN_CLIENT, f.vpn_clients > 0, path="/clients", role=Role.OPERATOR),
        Step(STEP_TELEGRAM, f.telegram, path="/settings#notifications"),
        Step(STEP_BACKUPS, f.backups, path="/backups"),
    ]


# The setup guides of the key pages: page → its steps, in the order to do them. A step with an action is done with a
# button of that page, a step with a path on that page, a step with neither is an instruction for the page itself.
GUIDES: dict[str, Callable[[Facts], list[Step]]] = {
    "devices": lambda f: [
        Step("add", f.devices > 0, action=ACTION_CREATE),
        Step("answers", f.devices_up > 0, role=Role.OPERATOR),
        Step("endpoints", f.endpoints > 0, path="/endpoints", role=Role.VIEWER),
        Step("discover", f.devices > 1, optional=True, action=ACTION_DISCOVER),
        Step("map", f.devices_on_map, optional=True, path="/network-map", role=Role.OPERATOR),
    ],
    "flows": lambda f: [
        Step("device", f.devices_up > 0, path="/devices"),
        Step("export", f.flows, path="/devices", role=Role.OPERATOR),
        Step("rules", f.flow_rules > 0, optional=True, path="/flows?tab=rules"),
        Step("threats", f.threats_loaded, optional=True, path="/flows?tab=threats"),
        Step("view", f.views > 0, optional=True, path="/flows?tab=visualize", role=Role.VIEWER),
    ],
    "clients": lambda f: [
        Step("client", f.vpn_clients > 0, action=ACTION_CREATE, role=Role.OPERATOR),
        Step("config", f.configs_issued, role=Role.OPERATOR),
        Step("connected", f.clients_connected, role=Role.OPERATOR),
        Step("policy", f.default_policy, optional=True, path="/settings"),
        Step("telegram", f.telegram, optional=True, path="/settings#notifications"),
    ],
    "routing": lambda f: [
        Step("router", f.router_up, path="/devices"),
        Step("site_list", f.site_list, action=ACTION_UPLOAD),
        Step("custom", f.custom_prefixes, optional=True),
        Step("flows", f.flows, optional=True, path="/flows", role=Role.VIEWER),
    ],
    "settings": lambda f: [
        Step("spa", f.spa_connected and f.spa_synced and not f.spa_error and f.spa_admins > 0, action=ACTION_SYNC),
        Step("address", f.address),
        Step("telegram", f.telegram, path="/settings#notifications"),
        Step("backups", f.backups, path="/backups"),
        Step("policy", f.default_policy, optional=True),
    ],
}


def _result(steps: list[Step], role: Role) -> dict:
    for s in steps:
        s.can_fix = role.allows(s.role)
    return {"done": all(s.done for s in steps if not s.optional), "steps": [s.view() for s in steps]}


class OnboardingService:
    def __init__(self, db: Database, settings: SettingsService, spa, notifier, backups, flows, public_urls: list[str],
                 gateway_client=None):
        self._gateway_client = gateway_client or (lambda: False)
        self._db = db
        self._settings = settings
        self._spa = spa
        self._notifier = notifier
        self._backups = backups
        self._flows = flows
        self._public_urls = public_urls

    async def _check(self, name: str, probe) -> bool:
        """A check against another system (SPA's key source, OpenBao, ClickHouse, the disk): a failure or a timeout
        means "not done" and never breaks the page."""
        try:
            return bool(await asyncio.wait_for(probe(), CHECK_SECONDS))
        except Exception as e:  # noqa: BLE001 — any failure of a probe is "not done"
            log.debug("onboarding: %s not checked: %s", name, e)
            return False

    async def facts(self) -> Facts:
        """Read on every request (a few counts and three probes, each bounded by CHECK_SECONDS), so a step is ticked
        off as soon as it is done."""
        f = Facts()
        async with self._db.session() as s:
            async def n(model, *where) -> int:
                return int((await s.execute(select(func.count()).select_from(model).where(*where))).scalar() or 0)

            ok = NetworkDevice.last_ok_at.is_not(None)
            people = VpnClient.system.is_(False)
            f.devices = await n(NetworkDevice)
            f.devices_up = await n(NetworkDevice, ok)
            f.router_up = await n(NetworkDevice, ok, NetworkDevice.role == "router") > 0
            f.devices_on_map = await n(NetworkDevice, NetworkDevice.map_x.is_not(None)) > 0
            f.endpoints = await n(NetworkEndpoint)
            f.flow_rules = await n(FlowRule, FlowRule.enabled.is_(True))
            f.views = await n(SavedView)
            f.vpn_clients = await n(VpnClient, people)
            f.configs_issued = await n(VpnClient, people, VpnClient.config_downloaded_at.is_not(None)) > 0
            f.clients_connected = await n(VpnClient, people, VpnClient.last_handshake_at.is_not(None)) > 0
            f.local_admins = await n(PanelUser, PanelUser.source == "local", PanelUser.is_active.is_(True),
                                     PanelUser.role == Role.ADMIN)
            f.vps_ok = await n(Alert, Alert.key == "server.unreachable", Alert.resolved_at.is_(None)) == 0
            f.spa_admins = await n(PanelUser, PanelUser.source == "spa", PanelUser.external_id.is_not(None),
                                   PanelUser.is_active.is_(True), PanelUser.role == Role.ADMIN)
            stored = await SettingRepo(s).get_all()
        f.site_list = bool(stored.get(SITE_LIST_KEY))
        f.custom_prefixes = bool(stored.get(CUSTOM_KEY))

        settings = await self._settings.all()
        f.default_policy = settings.get("default_quota_gb") not in (None, "") or settings.get("default_valid_days") not in (None, "")
        threat = settings.get("threat_status")
        f.threats_loaded = bool(settings.get("threat_enabled")) and isinstance(threat, dict) and int(threat.get("prefixes") or 0) > 0

        spa = await self._spa.state()
        f.spa_connected = await self._check("spa", self._spa.connection)
        f.spa_synced = bool(spa.get("last_sync_at"))
        f.spa_error = bool(spa.get("error"))
        f.address = any(is_public_address(u) for u in self._public_urls)
        f.local_mode = await self._settings.get("auth_mode") != "keystone"
        f.router_set = bool(settings.get("router_host"))
        f.vps_set = bool(settings.get("awg_host"))
        f.gateway_set = bool(settings.get("gateway_ip"))
        f.gateway_client = bool(self._gateway_client())

        async def telegram() -> bool:
            return bool(settings.get("notify_enabled")) and await self._notifier.configured()

        async def backups() -> bool:
            return bool(settings.get("backup_enabled")) and bool(await asyncio.to_thread(self._backups.list))

        async def flows() -> bool:
            return int((await self._flows.status()).get("total_flows") or 0) > 0

        f.telegram, f.backups, f.flows = await asyncio.gather(
            self._check("telegram", telegram), self._check("backups", backups), self._check("flows", flows))
        return f

    async def checklist(self, role: Role) -> dict:
        return _result(_checklist(await self.facts()), role)

    async def guide(self, page: str, role: Role) -> dict | None:
        build = GUIDES.get(page)
        if build is None:
            return None
        return _result(build(await self.facts()), role)
