"""WireGuard on OPNsense through its REST API (the os-wireguard core plugin, 24.1+).

The panel manages the peers ("clients" in OPNsense terms) of one instance ("server"), found by its name or by its
device (wg0). A peer belongs to an instance through its ``servers`` field: every write sends it, since ``setClient``
without it unbinds the peer. Changes take effect after ``service/reconfigure``.

The API key and secret live only in OpenBao (``opnsense/api``)."""
from __future__ import annotations

import ipaddress
import re

import httpx

from ..domain.errors import DomainError, ExternalServiceError
from ..domain.models import PeerEntry, PeerRuntime
from ..ports import SecretStore

SECRET_PATH = "opnsense/api"
_KEY = re.compile(r"[A-Za-z0-9+/]{42,43}=")


def _key(value: str) -> str:
    if not _KEY.fullmatch(value or ""):
        raise ValueError("bad wireguard key")
    return value


def _options(value) -> list[str]:
    """A list field as OPNsense returns it: a comma separated string (search) or ``{key: {value, selected}}`` (get)."""
    if isinstance(value, dict):
        return [k for k, v in value.items() if not isinstance(v, dict) or str(v.get("selected", "0")) in ("1", "True")]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _.@-]", "_", name).strip()[:64] or "client"


class OpnsenseWireguard:
    def __init__(self, host: str, instance: str, verify_tls: bool, secrets: SecretStore,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.host = host
        self.instance_name = instance
        self._verify = verify_tls
        self._secrets = secrets
        self._transport = transport

    async def credentials(self) -> tuple[str, str] | None:
        data = await self._secrets.get(SECRET_PATH) or {}
        if data.get("key") and data.get("secret"):
            return data["key"], data["secret"]
        return None

    async def _request(self, method: str, path: str, body: dict | None = None, *, creds=None):
        if not self.host:
            raise DomainError("The OPNsense address is not set")
        creds = creds or await self.credentials()
        if not creds:
            raise DomainError("The OPNsense API key is not set; enter it on the 'VPN on OPNsense' page")
        host = self.host if ":" not in self.host else f"[{self.host}]"
        try:
            async with httpx.AsyncClient(base_url=f"https://{host}", auth=creds, verify=self._verify, timeout=20,
                                         transport=self._transport) as http:
                r = await http.request(method, path, json=body if method == "POST" else None)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in (401, 403):
                raise ExternalServiceError(f"OPNsense {self.host}: API denied access ({code}); check the API key")
            raise ExternalServiceError(f"OPNsense {self.host}: API returned {code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise ExternalServiceError(f"OPNsense {self.host}: API is unreachable ({e or type(e).__name__})") from e
        except ValueError as e:
            raise ExternalServiceError(f"OPNsense {self.host}: the API response is not JSON") from e

    async def _post(self, path: str, body: dict) -> dict:
        res = await self._request("POST", path, body)
        if isinstance(res, dict) and res.get("result") == "failed":
            problems = "; ".join(f"{k}: {v}" for k, v in (res.get("validations") or {}).items())
            raise DomainError(f"OPNsense refused the change{': ' + problems if problems else ''}")
        return res

    async def check(self, key: str, secret: str) -> None:
        """Tries the key on a harmless call (used before saving it)."""
        await self._request("GET", "/api/wireguard/server/searchServer", creds=(key, secret))

    # The instance -------------------------------------------------------------------------------------------------

    async def instance(self) -> dict | None:
        rows = (await self._request("GET", "/api/wireguard/server/searchServer")).get("rows") or []
        want = self.instance_name

        def device(*rs: dict) -> str:
            for r in rs:
                if r.get("interface"):
                    return str(r["interface"])
            n = next((str(r["instance"]) for r in rs if str(r.get("instance", "")) != ""), "")
            return f"wg{n}" if n else ""

        row = next((r for r in rows if r.get("name") == want), None) or \
            next((r for r in rows if device(r) == want), None)
        if not row:
            return None
        full = (await self._request("GET", f"/api/wireguard/server/getServer/{row['uuid']}")).get("server") or {}
        addresses = _options(full.get("tunneladdress")) or _options(row.get("tunneladdress"))
        iface = ipaddress.ip_interface(addresses[0]) if addresses else None
        return {"uuid": row["uuid"], "name": row.get("name") or want,
                "device": device(full, row),
                "enabled": str(full.get("enabled", row.get("enabled", "1"))) == "1",
                "port": _int(full.get("port") or row.get("port")),
                "public_key": full.get("pubkey") or row.get("pubkey") or "",
                "addresses": addresses,
                "network": str(iface.network) if iface else None,
                "server_ip": str(iface.ip) if iface else None}

    async def require_instance(self) -> dict:
        inst = await self.instance()
        if not inst:
            raise DomainError(f"WireGuard instance '{self.instance_name}' was not found on OPNsense")
        if not inst["network"]:
            raise DomainError(f"WireGuard instance '{self.instance_name}' on OPNsense has no tunnel address")
        return inst

    # The peers ----------------------------------------------------------------------------------------------------

    async def all_clients(self) -> list[dict]:
        rows = (await self._request("POST", "/api/wireguard/client/searchClient",
                                    {"current": 1, "rowCount": -1})).get("rows") or []
        return [{"uuid": r["uuid"], "name": r.get("name", ""), "public_key": r.get("pubkey", ""),
                 "address": ",".join(_options(r.get("tunneladdress"))),
                 "enabled": str(r.get("enabled", "1")) == "1", "servers": _options(r.get("servers"))}
                for r in rows if r.get("uuid")]

    async def clients(self, inst: dict | None = None) -> list[dict]:
        inst = inst or await self.instance()
        if not inst:
            return []
        mine = {inst["uuid"], inst["name"]}
        return [c for c in await self.all_clients() if mine & set(c["servers"])]

    async def _find(self, public_key: str) -> dict:
        for c in await self.clients():
            if c["public_key"] == public_key:
                return c
        raise DomainError("The peer was not found on OPNsense; run a sync")

    async def _servers(self, uuid: str) -> str:
        """The instances a peer is bound to, as uuids (sent back on every change)."""
        full = (await self._request("GET", f"/api/wireguard/client/getClient/{uuid}")).get("client") or {}
        return ",".join(_options(full.get("servers")))

    async def show(self) -> list[dict]:
        res = await self._request("GET", "/api/wireguard/service/show")
        if isinstance(res, dict):
            res = res.get("records") or res.get("rows") or []
        return [r for r in res if isinstance(r, dict)]

    async def runtime(self) -> dict[str, PeerRuntime]:
        inst = await self.instance()
        if not inst:
            return {}
        out = {}
        for r in await self.show():
            if r.get("type") != "peer" or (r.get("if") and r["if"] != inst["device"]):
                continue
            pub = r.get("public-key")
            if not pub:
                continue
            endpoint = r.get("endpoint")
            out[pub] = PeerRuntime(public_key=pub, endpoint=endpoint if endpoint and endpoint != "(none)" else None,
                                   allowed_ips=str(r.get("allowed-ips") or ""),
                                   latest_handshake=_int(r.get("latest-handshake")),
                                   rx=_int(r.get("transfer-rx")), tx=_int(r.get("transfer-tx")))
        return out

    async def peer_entries(self) -> list[PeerEntry]:
        return [PeerEntry(c["public_key"], c["address"], None, not c["enabled"]) for c in await self.clients()]

    async def reconfigure(self) -> None:
        await self._request("POST", "/api/wireguard/service/reconfigure", {})

    async def add_peer(self, inst: dict, public_key: str, psk: str, address: str, name: str) -> None:
        ipaddress.ip_interface(address)
        await self._post("/api/wireguard/client/addClient", {"client": {
            "enabled": "1", "name": safe_name(name), "pubkey": _key(public_key), "psk": _key(psk),
            "tunneladdress": address, "keepalive": "", "servers": inst["uuid"]}})
        await self.reconfigure()

    async def set_peer(self, public_key: str, *, disabled: bool | None = None, new_key: str | None = None,
                       name: str | None = None) -> None:
        c = await self._find(public_key)
        body = {"servers": await self._servers(c["uuid"])}
        if disabled is not None:
            body["enabled"] = "0" if disabled else "1"
        if new_key:
            body["pubkey"] = _key(new_key)
        if name is not None:
            body["name"] = safe_name(name)
        await self._post(f"/api/wireguard/client/setClient/{c['uuid']}", {"client": body})
        await self.reconfigure()

    async def remove_peer(self, public_key: str) -> None:
        c = await self._find(public_key)
        await self._post(f"/api/wireguard/client/delClient/{c['uuid']}", {})
        await self.reconfigure()
