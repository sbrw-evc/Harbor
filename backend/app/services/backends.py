from __future__ import annotations

import asyncio
import datetime as dt

from ..adapters.amnezia import AwgConfigCodec
from ..adapters.mikrotik_wg import COMMENT, MikrotikWireguard
from ..domain.errors import DomainError, NotFound
from ..domain.models import ClientBundle, NewPeer, PeerEntry, PeerRuntime
from ..ports import VpnBackend, VpnServerGateway
from .common import current
from .configs import ClientConfigBuilder, qr_svg, wg_conf
from .server import ServerConfigEditor, free_address, generate_psk
from .settings import SettingsService


class AmneziaBackend(VpnBackend):
    kind = "amnezia"
    title = "Amnezia (AWG)"
    alert_key = "server.unreachable"

    def __init__(self, editor: ServerConfigEditor, gateway: VpnServerGateway, builder: ClientConfigBuilder,
                 settings: SettingsService, default_host):
        self._editor = editor
        self._gw = gateway
        self._builder = builder
        self._settings = settings
        self._default_host = default_host

    async def configured(self) -> bool:
        return bool(current(self._default_host))

    async def ready(self) -> bool:
        return await self.configured()

    async def runtime(self) -> dict[str, PeerRuntime]:
        return await self._gw.runtime()

    async def peers(self) -> list[PeerEntry]:
        return (await self._editor.load()).peers

    async def names(self) -> dict[str, str]:
        return {t.get("clientId"): (t.get("userData") or {}).get("clientName", "")
                for t in await self._gw.read_clients_table()}

    async def add_peer(self, public_key: str, name: str) -> NewPeer:
        psk = await self._gw.preshared_key()
        async with self._editor.edit() as cfg:
            address = cfg.free_address()
            cfg.peers.append(PeerEntry(public_key, address, psk, False,
                                       AwgConfigCodec.peer_lines(public_key, psk, address)))
        await self.rename_peer(public_key, name, address)
        return NewPeer(address, psk)

    async def rename_peer(self, public_key: str, name: str, address: str) -> None:
        table = await self._gw.read_clients_table()
        for t in table:
            if t.get("clientId") == public_key:
                t.setdefault("userData", {})["clientName"] = name
                break
        else:
            table.append({"clientId": public_key, "userData": {
                "clientName": name, "allowedIps": address, "allowed_ips": address,
                "creationDate": dt.datetime.now().strftime("%a %b %d %H:%M:%S %Y")}})
        await self._gw.write_clients_table(table)

    async def set_disabled(self, public_key: str, disabled: bool) -> None:
        async with self._editor.edit() as cfg:
            peer = cfg.peer(public_key)
            if not peer:
                raise NotFound("Peer not found in the server config; run a sync")
            peer.disabled = disabled

    async def rekey_peer(self, old_key: str, new_key: str) -> None:
        async with self._editor.edit() as cfg:
            peer = cfg.peer(old_key)
            if not peer:
                raise NotFound("Peer not found in the server config")
            peer.lines = [f"PublicKey = {new_key}" if l.strip().startswith("PublicKey") else l for l in peer.lines]
            peer.public_key = new_key
        table = await self._gw.read_clients_table()
        for t in table:
            if t.get("clientId") == old_key:
                t["clientId"] = new_key
        await self._gw.write_clients_table(table)

    async def remove_peer(self, public_key: str) -> None:
        async with self._editor.edit() as cfg:
            cfg.peers = [p for p in cfg.peers if p.public_key != public_key]
        await self._gw.write_clients_table(
            [t for t in await self._gw.read_clients_table() if t.get("clientId") != public_key])

    async def bundle(self, *, name: str, private_key: str, public_key: str, address: str,
                     preshared_key: str | None) -> ClientBundle:
        st = await self._settings.all()
        server = await self._editor.load()
        return await asyncio.to_thread(
            self._builder.build, name=name, server=server, server_public_key=await self._gw.server_public_key(),
            psk=await self._gw.preshared_key(), private_key=private_key, public_key=public_key, address=address,
            endpoint_host=st["endpoint_host"] or current(self._default_host), dns=st["client_dns"])


class MikrotikBackend(VpnBackend):
    kind = "mikrotik"
    title = "MikroTik (WireGuard)"
    alert_key = "server.unreachable.mikrotik"

    def __init__(self, wg: MikrotikWireguard, settings: SettingsService, lock, lan_cidr, router_host=None):
        self.wg = wg
        self._lan = lan_cidr
        self._router_host = router_host
        self._settings = settings
        self._lock = lock

    async def configured(self) -> bool:
        return self._router_host is None or bool(current(self._router_host))

    async def ready(self) -> bool:
        return await self.wg.interface() is not None

    async def runtime(self) -> dict[str, PeerRuntime]:
        return await self.wg.runtime()

    async def peers(self) -> list[PeerEntry]:
        return await self.wg.peer_entries()

    async def names(self) -> dict[str, str]:
        return {p["public_key"]: p["comment"].removeprefix(f"{COMMENT}: ") for p in await self.wg.peers()}

    async def add_peer(self, public_key: str, name: str) -> NewPeer:
        if not await self.ready():
            raise DomainError("WireGuard on MikroTik is not set up; set it up on the 'VPN on MikroTik' page")
        psk = generate_psk()
        async with self._lock.hold():
            taken = [f"{self.wg.server_ip}/32"] + [p["address"] for p in await self.wg.peers()]
            address = free_address(self.wg.network, taken)
            await self.wg.add_peer(public_key, psk, address, name)
        return NewPeer(address, psk)

    async def rename_peer(self, public_key: str, name: str, address: str) -> None:
        await self.wg.set_peer(public_key, name=name)

    async def set_disabled(self, public_key: str, disabled: bool) -> None:
        await self.wg.set_peer(public_key, disabled=disabled)

    async def rekey_peer(self, old_key: str, new_key: str) -> None:
        await self.wg.set_peer(old_key, new_key=new_key)

    async def remove_peer(self, public_key: str) -> None:
        await self.wg.remove_peer(public_key)

    async def endpoint(self) -> tuple[str, int]:
        iface = await self.wg.interface()
        if not iface:
            raise DomainError("WireGuard on MikroTik is not set up")
        host = await self._settings.get("mt_endpoint_host") or await self.wg.public_address()
        return host, iface["listen_port"]

    async def bundle(self, *, name: str, private_key: str, public_key: str, address: str,
                     preshared_key: str | None) -> ClientBundle:
        st = await self._settings.all()
        host, port = await self.endpoint()
        conf = wg_conf(private_key=private_key, address=address, dns=st["mt_client_dns"] or str(self.wg.server_ip),
                       server_public_key=(await self.wg.interface())["public_key"], psk=preshared_key,
                       allowed_ips=st["mt_allowed_ips"], endpoint=f"{host}:{port}")
        return ClientBundle(name=name, config=conf, amnezia_key="", qr_svg=await asyncio.to_thread(qr_svg, conf))

    async def info(self) -> dict:
        iface = await self.wg.interface()
        st = await self._settings.all()
        peers = await self.wg.peers() if iface else []
        return {"configured": iface is not None, "interface": self.wg.iface, "network": str(self.wg.network),
                "server_ip": str(self.wg.server_ip), "lan_cidr": current(self._lan),
                "port": iface["listen_port"] if iface else self.wg.port,
                "public_key": iface["public_key"] if iface else None, "running": bool(iface and iface["running"]),
                "public_address": await self.wg.public_address(), "endpoint_host": st["mt_endpoint_host"],
                "client_dns": st["mt_client_dns"], "allowed_ips": st["mt_allowed_ips"],
                "peers_total": len(peers), "peers_disabled": sum(p["disabled"] for p in peers)}
