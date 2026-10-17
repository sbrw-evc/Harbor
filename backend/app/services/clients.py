from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import secrets
import uuid

import logging

from ..db.base import Database
from ..db.models import ShareLink, VpnClient
from ..db.repositories import ClientRepo, ConnectionRepo, ShareRepo, TrafficRepo
from ..domain.errors import DomainError, ExternalServiceError, NotFound
from ..domain.models import BlockReason, ClientBundle, ClientStatus
from ..ports import SecretStore, VpnBackend
from .audit import AuditService
from .common import Actor, EventBus, current, utcnow
from .server import generate_keypair
from .settings import SettingsService

log = logging.getLogger(__name__)

ONLINE_SECONDS = 180
GB = 1024 ** 3


def month_start(now: dt.datetime | None = None) -> dt.datetime:
    now = now or utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


class ClientView:
    @staticmethod
    def dto(c: VpnClient, month: tuple[int, int] = (0, 0), live: dict | None = None) -> dict:
        used = month[0] + month[1]
        now = utcnow()
        online = bool(c.last_handshake_at and (now - c.last_handshake_at).total_seconds() < ONLINE_SECONDS)
        live = live or {}
        return {
            "id": str(c.id), "server": c.server, "public_key": c.public_key, "name": c.name, "owner": c.owner, "contact": c.contact,
            "tags": c.tags or [], "note": c.note, "address": c.address, "status": c.status.value,
            "block_reason": c.block_reason.value if c.block_reason else None, "managed": c.managed, "system": c.system,
            "expires_at": iso(c.expires_at), "quota_bytes": c.quota_bytes,
            "quota_used_bytes": used, "quota_percent": round(used * 100 / c.quota_bytes, 1) if c.quota_bytes else None,
            "month_rx": month[1], "month_tx": month[0],
            "total_rx": c.total_tx, "total_tx": c.total_rx,
            "online": online and c.status == ClientStatus.ACTIVE, "last_handshake_at": iso(c.last_handshake_at),
            "endpoint": c.last_endpoint, "country_code": c.country_code, "country": c.country, "city": c.city,
            "lat": c.lat, "lon": c.lon, "rate_rx": live.get("rx", 0), "rate_tx": live.get("tx", 0),
            "created_at": iso(c.created_at), "created_by": c.created_by, "config_downloaded_at": iso(c.config_downloaded_at),
        }


class VpnClientService:
    def __init__(self, db: Database, backends: list[VpnBackend], secrets_store: SecretStore,
                 settings: SettingsService, audit: AuditService, bus: EventBus, public_base_url, live):
        self._live = live
        self._db = db
        self.backends = {b.kind: b for b in backends}
        self._secrets = secrets_store
        self._settings = settings
        self._audit = audit
        self._bus = bus
        self._public_base = public_base_url

    @staticmethod
    def _secret_path(client_id: uuid.UUID) -> str:
        return f"clients/{client_id}"

    def backend(self, kind: str) -> VpnBackend:
        b = self.backends.get(kind)
        if not b:
            raise DomainError(f"Unknown VPN server: {kind}")
        return b

    async def servers(self) -> list[dict]:
        async def one(b: VpnBackend) -> dict:
            try:
                ready = await b.ready()
            except ExternalServiceError:
                ready = False
            return {"kind": b.kind, "title": b.title, "ready": ready}
        return list(await asyncio.gather(*(one(b) for b in self.backends.values())))

    async def list(self, q: str | None = None, status: str | None = None, tag: str | None = None,
                   server: str | None = None) -> list[dict]:
        async with self._db.session() as s:
            clients = await ClientRepo(s).search(q, status, tag, server)
            month = await TrafficRepo(s).totals_by_client(month_start())
        return [ClientView.dto(c, month.get(c.id, (0, 0)), self._live.rates.get(str(c.id))) for c in clients]

    async def get(self, client_id: uuid.UUID) -> dict:
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            month = await TrafficRepo(s).totals_by_client(month_start())
            sessions = await ConnectionRepo(s).for_client(c.id, 50)
        d = ClientView.dto(c, month.get(c.id, (0, 0)), self._live.rates.get(str(c.id)))
        d["connections"] = [{"ts": iso(e.ts), "ip": e.ip, "port": e.port, "country_code": e.country_code,
                             "country": e.country, "city": e.city} for e in sessions]
        return d

    async def tags(self) -> list[str]:
        async with self._db.session() as s:
            return await ClientRepo(s).all_tags()


    async def create(self, actor: Actor, *, name: str, owner: str = "", contact: str = "", tags: list[str] | None = None,
                     note: str = "", expires_at: dt.datetime | None = None, quota_gb: float | None = None,
                     server: str = "amnezia") -> dict:
        name = name.strip()[:128]
        if not name:
            raise DomainError("Enter a client name")
        backend = self.backend(server)
        keys = generate_keypair()
        peer = await backend.add_peer(keys.public_key, name)
        client_id = uuid.uuid4()
        await self._secrets.put(self._secret_path(client_id), {"private_key": keys.private_key,
                                                               "public_key": keys.public_key,
                                                               "psk": peer.preshared_key})
        async with self._db.session() as s:
            c = ClientRepo(s).add(VpnClient(
                id=client_id, server=server, public_key=keys.public_key, name=name, owner=owner, contact=contact,
                tags=self._tags(tags), note=note, address=peer.address, managed=True, created_by=actor.username,
                expires_at=expires_at, quota_bytes=int(quota_gb * GB) if quota_gb else None))
            self._audit.record(s, actor, "client.created", "client", c.id, name, address=peer.address, server=server)
        self._bus.publish("clients", {"id": str(client_id)})
        return await self.get(client_id)

    async def update(self, actor: Actor, client_id: uuid.UUID, fields: dict) -> dict:
        allowed = {"name", "owner", "contact", "tags", "note", "expires_at", "quota_gb"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            changes = {}
            for k, v in fields.items():
                if k == "quota_gb":
                    k, v = "quota_bytes", int(v * GB) if v else None
                elif k == "tags":
                    v = self._tags(v)
                elif k == "name":
                    v = (v or "").strip()[:128] or c.name
                if getattr(c, k) != v:
                    changes[k] = str(v)
                    setattr(c, k, v)
            self._audit.record(s, actor, "client.updated", "client", c.id, c.name, **changes)
            pub, name, address, server = c.public_key, c.name, c.address, c.server
        if "name" in changes:
            await self.backend(server).rename_peer(pub, name, address)
        self._bus.publish("clients", {"id": str(client_id)})
        return await self.get(client_id)

    async def set_blocked(self, actor: Actor, client_id: uuid.UUID, blocked: bool,
                          reason: BlockReason = BlockReason.MANUAL) -> dict:
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            if c.system and blocked:
                raise DomainError("The system client (home gateway) cannot be blocked")
            pub, server = c.public_key, c.server
        await self.backend(server).set_disabled(pub, blocked)
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            c.status = ClientStatus.BLOCKED if blocked else ClientStatus.ACTIVE
            c.block_reason = reason if blocked else None
            self._audit.record(s, actor, "client.blocked" if blocked else "client.unblocked", "client", c.id, c.name,
                               reason=reason.value if blocked else None)
        self._bus.publish("clients", {"id": str(client_id)})
        return await self.get(client_id)

    async def reissue(self, actor: Actor, client_id: uuid.UUID) -> dict:
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            old_pub, server = c.public_key, c.server
        keys = generate_keypair()
        old_secret = await self._secrets.get(self._secret_path(client_id)) or {}
        await self.backend(server).rekey_peer(old_pub, keys.public_key)
        await self._secrets.put(self._secret_path(client_id), {"private_key": keys.private_key,
                                                               "public_key": keys.public_key,
                                                               "psk": old_secret.get("psk")})
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            c.public_key, c.managed, c.raw_rx, c.raw_tx, c.config_downloaded_at = keys.public_key, True, 0, 0, None
            self._audit.record(s, actor, "client.reissued", "client", c.id, c.name)
        self._bus.publish("clients", {"id": str(client_id)})
        return await self.get(client_id)

    async def delete(self, actor: Actor, client_id: uuid.UUID) -> None:
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            if c.system:
                raise DomainError("The system client (home gateway) cannot be removed from the panel")
            pub, server = c.public_key, c.server
        await self.backend(server).remove_peer(pub)
        await self._secrets.delete(self._secret_path(client_id))
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            self._audit.record(s, actor, "client.deleted", "client", c.id, c.name, address=c.address)
            await s.delete(c)
        self._bus.publish("clients", {"id": str(client_id), "deleted": True})

    async def bulk(self, actor: Actor, ids: list[uuid.UUID], action: str) -> dict:
        ops = {"block": lambda i: self.set_blocked(actor, i, True), "unblock": lambda i: self.set_blocked(actor, i, False),
               "delete": lambda i: self.delete(actor, i)}
        if action not in ops:
            raise DomainError("Unknown action")
        done, failed = 0, []
        for i in ids:
            try:
                await ops[action](i)
                done += 1
            except DomainError as e:
                failed.append({"id": str(i), "error": e.message})
        return {"done": done, "failed": failed}


    async def bundle(self, actor: Actor | None, client_id: uuid.UUID) -> ClientBundle:
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            if not c.managed:
                raise DomainError("The key of this client is unknown to the panel (it was created outside the panel). "
                                  "Reissue the keys to get a config.")
            secret = await self._secrets.get(self._secret_path(c.id))
            if not secret:
                raise DomainError("The client key was not found in OpenBao. Reissue the keys.")
            bundle = await self.backend(c.server).bundle(
                name=c.name, private_key=secret["private_key"], public_key=c.public_key, address=c.address,
                preshared_key=secret.get("psk"))
            c.config_downloaded_at = utcnow()
            if actor:
                self._audit.record(s, actor, "client.config_viewed", "client", c.id, c.name)
        return bundle

    async def create_share_link(self, actor: Actor, client_id: uuid.UUID, hours: int | None = None) -> dict:
        hours = hours or await self._settings.get("share_link_hours")
        token = secrets.token_urlsafe(24)
        async with self._db.session() as s:
            c = await ClientRepo(s).require(client_id)
            if not c.managed:
                raise DomainError("Reissue the client keys first")
            expires = utcnow() + dt.timedelta(hours=hours)
            ShareRepo(s).add(ShareLink(token_hash=hashlib.sha256(token.encode()).hexdigest(), client_id=c.id,
                                       created_by=actor.username, expires_at=expires, uses_left=3))
            self._audit.record(s, actor, "client.share_link_created", "client", c.id, c.name, hours=hours)
        return {"url": f"{(current(self._public_base) or '').rstrip('/')}/s/{token}", "expires_at": expires.isoformat()}

    async def consume_share_link(self, token: str, ip: str) -> ClientBundle:
        async with self._db.session() as s:
            link = await ShareRepo(s).get(hashlib.sha256(token.encode()).hexdigest())
            if not link or link.expires_at < utcnow() or link.uses_left <= 0:
                raise NotFound("The link is invalid or has expired")
            link.uses_left -= 1
            client_id = link.client_id
            c = await ClientRepo(s).require(client_id)
            self._audit.record(s, Actor("share-link", "", ip), "client.share_link_used", "client", c.id, c.name)
        return await self.bundle(None, client_id)


    async def sync(self, actor: Actor | None = None) -> dict:
        total = {"imported": 0, "missing": 0, "peers": 0}
        for backend in self.backends.values():
            try:
                res = await self._sync_backend(backend, actor)
            except ExternalServiceError as e:
                log.warning("sync %s skipped: %s", backend.kind, e.message)
                continue
            for k in total:
                total[k] += res[k]
        if total["imported"] or total["missing"]:
            self._bus.publish("clients", {"sync": True})
        return total

    async def _sync_backend(self, backend: VpnBackend, actor: Actor | None) -> dict:
        if not await backend.configured() or not await backend.ready():
            return {"imported": 0, "missing": 0, "peers": 0}
        peers = {p.public_key: p for p in await backend.peers() if p.public_key}
        names = await backend.names()
        imported, missing = 0, 0
        async with self._db.session() as s:
            repo = ClientRepo(s)
            known = {c.public_key: c for c in await repo.all(VpnClient.server == backend.kind)}
            for pub, p in peers.items():
                c = known.get(pub)
                if c is None:
                    if await repo.by_public_key(pub):
                        continue
                    c = repo.add(VpnClient(server=backend.kind, public_key=pub, name=names.get(pub) or pub[:10],
                                           address=p.address, managed=False, created_by="import"))
                    imported += 1
                c.address = p.address
                if p.disabled and c.status != ClientStatus.BLOCKED:
                    c.status, c.block_reason = ClientStatus.BLOCKED, c.block_reason or BlockReason.MANUAL
                elif not p.disabled and c.status != ClientStatus.ACTIVE:
                    c.status, c.block_reason = ClientStatus.ACTIVE, None
            for pub, c in known.items():
                if pub not in peers and c.status != ClientStatus.MISSING:
                    c.status = ClientStatus.MISSING
                    missing += 1
            if actor and (imported or missing):
                self._audit.record(s, actor, "client.sync", "server", backend.kind, backend.title,
                                   imported=imported, missing=missing)
        return {"imported": imported, "missing": missing, "peers": len(peers)}

    @staticmethod
    def _tags(tags) -> list[str]:
        return sorted({t.strip()[:32] for t in (tags or []) if t and t.strip()})
