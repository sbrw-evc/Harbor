import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..container import Container
from ..domain.errors import DomainError
from ..domain.models import Role
from ..services.identity.spa import MODE_LOCAL
from ..services.settings import INFRA
from .deps import Principal, admin, container, current, viewer
from .schemas import ServerConfigIn

router = APIRouter(prefix="/api")


@router.get("/analytics/summary")
async def summary(_: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.analytics.summary()


@router.get("/analytics/live")
async def live(_: Principal = Depends(viewer), c: Container = Depends(container)):
    return c.analytics.live()


@router.get("/analytics/traffic")
async def traffic(range: str = "24h", client_id: uuid.UUID | None = None, _: Principal = Depends(viewer),
                  c: Container = Depends(container)):
    return await c.analytics.series(range, client_id)


@router.get("/analytics/top")
async def top(range: str = "24h", _: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.analytics.top(range)


@router.get("/analytics/heatmap")
async def heatmap(range: str = "30d", _: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.analytics.heatmap(range)


@router.get("/analytics/geo")
async def geo(range: str = "30d", _: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.analytics.geo(range)


@router.get("/events")
async def events(request: Request, _: Principal = Depends(viewer), c: Container = Depends(container)):
    q = c.bus.subscribe()

    async def stream():
        try:
            yield "retry: 5000\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            c.bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/alerts")
async def alerts(all: bool = False, _: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.alerts.list(include_resolved=all)


@router.post("/alerts/{alert_id}/ack")
async def ack(alert_id: int, p: Principal = Depends(current), c: Container = Depends(container)):
    await c.alerts.acknowledge(alert_id, p.actor)
    return {"ok": True}


@router.get("/audit")
async def audit(limit: int = 50, offset: int = 0, action: str | None = None, actor: str | None = None,
                q: str | None = None, _: Principal = Depends(admin), c: Container = Depends(container)):
    return await c.audit.page(min(limit, 200), offset, action, actor, q)


@router.get("/server/info")
async def server_info(_: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.server.info()


@router.get("/server/config")
async def server_config(_: Principal = Depends(admin), c: Container = Depends(container)):
    return await c.server.config()


@router.put("/server/config")
async def server_config_save(body: ServerConfigIn, p: Principal = Depends(admin), c: Container = Depends(container)):
    await c.server.save(p.actor, body.config)
    await c.clients.sync(p.actor)
    return {"ok": True}


@router.get("/server/backups/{name}")
async def server_backup(name: str, _: Principal = Depends(admin), c: Container = Depends(container)):
    return {"config": await c.server.backup(name)}


@router.get("/mikrotik-vpn")
async def mikrotik_vpn(_: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.mikrotik.info()


@router.post("/mikrotik-vpn/setup")
async def mikrotik_vpn_setup(p: Principal = Depends(admin), c: Container = Depends(container)):
    await c.mikrotik.wg.setup()
    async with c.db.session() as s:
        c.audit.record(s, p.actor, "mikrotik_vpn.setup", "router", c.mikrotik.wg.iface, "WireGuard",
                       network=str(c.mikrotik.wg.network), port=c.mikrotik.wg.port)
    return await c.mikrotik.info()


@router.get("/routing/status")
async def routing_status(_: Principal = Depends(viewer), c: Container = Depends(container)):
    return {"router": await c.routing.status(), "site_list": await c.routing.site_list()}


@router.post("/routing/site-list")
async def routing_upload(file: UploadFile = File(...), p: Principal = Depends(admin), c: Container = Depends(container)):
    raw = await file.read(64 * 1024 * 1024)
    return await c.routing.apply_site_list(p.actor, raw)


@router.get("/settings")
async def settings_get(_: Principal = Depends(viewer), c: Container = Depends(container)):
    return await c.settings.all()


@router.put("/settings")
async def settings_put(body: dict, p: Principal = Depends(admin), c: Container = Depends(container)):
    res = await c.settings.update(body)
    async with c.db.session() as s:
        c.audit.record(s, p.actor, "settings.updated", "settings", "", "", **{k: str(v) for k, v in body.items()})
    if set(body) & INFRA and await c.rc.refresh_now() and body.get("router_host"):
        await c.devices.seed_router()
    return res


# The infrastructure: the SSH key of the panel, checks, the storage it runs on ------------------------------------

@router.get("/infra")
async def infra(_: Principal = Depends(admin), c: Container = Depends(container)):
    st = c.storage
    return {"ssh_public_key": await c.ssh_keys.ensure(),
            "storage": {"postgres": c.db.engine.url.render_as_string(hide_password=True), "timescaledb": c.db.timescale,
                        "clickhouse": st.clickhouse_url, "openbao": st.bao_addr},
            "gateway_client": c.files.has_awg_conf()}


class CheckIn(BaseModel):
    target: str = Field(pattern="^(router|vps)$")


@router.post("/infra/check")
async def infra_check(body: CheckIn, _: Principal = Depends(admin), c: Container = Depends(container)):
    """Connects over SSH with the key of the panel and runs a harmless command."""
    await c.ssh_keys.ensure()
    try:
        if body.target == "router":
            out = await c.router.status()
            return {"ok": True, "detail": f"RouterOS {out.get('version', '')}".strip()}
        info = await c.vpn_gateway.versions()
        return {"ok": True, "detail": ", ".join(f"{k} {v}" for k, v in info.items() if v)}
    except DomainError as e:
        return {"ok": False, "detail": e.message}


@router.post("/gateway/client")
async def gateway_client(p: Principal = Depends(admin), c: Container = Depends(container)):
    """Creates the AmneziaWG client of the gateway itself and writes its config for the awg-client container."""
    created = await c.clients.create(p.actor, name="home-gateway", note="The gateway of the panel (awg-client)",
                                     tags=["gateway"], server="amnezia")
    bundle = await c.clients.bundle(p.actor, uuid.UUID(created["id"]))
    try:
        c.files.write_awg_conf(bundle.config)
    except OSError as e:
        raise DomainError(f"The client is created, but its config could not be written: {e}") from e
    async with c.db.session() as s:
        c.audit.record(s, p.actor, "gateway.client_written", "client", created["id"], "home-gateway")
    return {"ok": True, "client_id": created["id"]}


# People -----------------------------------------------------------------------------------------------------------

class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=256)
    role: Role = Role.VIEWER
    password: str = Field(max_length=256)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=256)
    role: Role | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    password: str = Field(max_length=256)


async def _local_mode(c: Container) -> None:
    if await c.settings.get("auth_mode") != MODE_LOCAL:
        raise DomainError("Users are managed in Keystone")


@router.get("/users")
async def users(_: Principal = Depends(admin), c: Container = Depends(container)):
    return await c.users.list()


@router.post("/users")
async def users_create(body: UserIn, p: Principal = Depends(admin), c: Container = Depends(container)):
    await _local_mode(c)
    return await c.users.create(p.actor, body.username, body.display_name, body.role, body.password, email=body.email)


@router.patch("/users/{user_id}")
async def users_update(user_id: int, body: UserPatch, p: Principal = Depends(admin), c: Container = Depends(container)):
    await _local_mode(c)
    return await c.users.update(p.actor, user_id, **body.model_dump(exclude_unset=True))


@router.post("/users/{user_id}/password")
async def users_password(user_id: int, body: PasswordResetIn, p: Principal = Depends(admin),
                         c: Container = Depends(container)):
    await _local_mode(c)
    await c.users.reset_password(p.actor, user_id, body.password)
    return {"ok": True}


@router.post("/users/{user_id}/totp/disable")
async def users_totp_disable(user_id: int, p: Principal = Depends(admin), c: Container = Depends(container)):
    await _local_mode(c)
    await c.auth.totp_disable(user_id, p.actor)
    return {"ok": True}


@router.delete("/users/{user_id}")
async def users_delete(user_id: int, p: Principal = Depends(admin), c: Container = Depends(container)):
    await _local_mode(c)
    await c.users.delete(p.actor, user_id)
    return {"ok": True}


# Keystone (SPA): the connection, moving the local users, the sign-in mode -----------------------------------------

class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=4096)


class SwitchIn(BaseModel):
    purge_local: bool = False


@router.get("/spa")
async def spa_status(_: Principal = Depends(admin), c: Container = Depends(container)):
    return await c.spa.status()


@router.put("/spa/key")
async def spa_key(body: KeyIn, p: Principal = Depends(admin), c: Container = Depends(container)):
    return await c.spa.set_key(body.key, p.actor)


@router.delete("/spa/key")
async def spa_key_remove(p: Principal = Depends(admin), c: Container = Depends(container)):
    await c.spa.remove_key(p.actor)
    return {"ok": True}


@router.post("/spa/import")
async def spa_import(p: Principal = Depends(admin), c: Container = Depends(container)):
    await _local_mode(c)
    return await c.spa.import_local_users(c.auth, p.actor)


@router.post("/spa/switch")
async def spa_switch(body: SwitchIn, p: Principal = Depends(admin), c: Container = Depends(container)):
    await _local_mode(c)
    return await c.spa.switch_to_keystone(c.auth, p.actor, body.purge_local)


@router.post("/auth/local")
async def auth_local(p: Principal = Depends(admin), c: Container = Depends(container)):
    await c.spa.switch_to_local(p.actor)
    return {"ok": True}


@router.post("/spa/sync")
async def spa_sync(p: Principal = Depends(admin), c: Container = Depends(container)):
    return await c.spa.sync(force=True, actor=p.actor)
