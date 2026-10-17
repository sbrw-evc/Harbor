"""The setup wizard (before the first administrator exists). Every step but the state needs the one-time setup code
in the X-Setup-Code header: it is printed to the log of the backend and written to <data>/setup-code.

1. storage — PostgreSQL (TimescaleDB), ClickHouse and OpenBao (the one of the compose file is initialised here, or an
   external one with AppRole credentials);
2. account — the sign-in: a local administrator, or a Keystone connection key; the address of the panel.
After that the "First steps" checklist takes over (services/onboarding.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field

from ..bootstrap import (CH_SECRET, KV_MOUNT, PG_SECRET, BaoInitializer, approle_login, check_clickhouse, check_openbao,
                         check_postgres, write_approle)
from ..adapters.openbao import OpenBaoSecretStore
from ..domain.errors import DomainError
from ..domain.models import Role
from ..services.common import Actor
from ..services.identity.spa import MODE_KEYSTONE, MODE_LOCAL
from ..setup import STAGE_ACCOUNT, STAGE_DONE, STAGE_STORAGE, SetupState
from .deps import COOKIE, client_ip

router = APIRouter(prefix="/api/setup", tags=["setup"])

# What the compose file runs (the wizard offers it).
DEFAULTS = {
    "postgres": {"host": "postgres", "port": 5432, "database": "amnezia", "user": "amnezia"},
    "clickhouse": {"url": "http://clickhouse:8123", "user": "amnezia", "database": "flows"},
    "openbao": {"addr": "http://openbao:8200", "mode": "bundled"},
}
SETUP_ACTOR = "setup"


def state(request: Request) -> SetupState:
    return request.app.state.setup


def code_checked(request: Request, x_setup_code: str | None = Header(default=None)) -> SetupState:
    st = state(request)
    st.gate.check(x_setup_code, client_ip(request))
    return st


class PostgresIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=63)
    user: str = Field(min_length=1, max_length=63)
    password: str = Field(max_length=256)


class ClickHouseIn(BaseModel):
    url: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=63)
    password: str = Field(max_length=256)
    database: str = Field(default="flows", max_length=63)


class OpenBaoIn(BaseModel):
    addr: str = Field(min_length=1, max_length=255)
    mode: str = Field(default="bundled", pattern="^(bundled|external)$")
    root_token: str = Field(default="", max_length=256)
    role_id: str = Field(default="", max_length=256)
    secret_id: str = Field(default="", max_length=256)
    mount: str = Field(default=KV_MOUNT, pattern="^[A-Za-z0-9_-]{1,64}$")


class StorageIn(BaseModel):
    postgres: PostgresIn
    clickhouse: ClickHouseIn
    openbao: OpenBaoIn


class AccountIn(BaseModel):
    mode: str = Field(pattern="^(local|keystone)$")
    public_base_url: str = Field(default="", max_length=255)
    username: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=128)
    email: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=256)
    key: str = Field(default="", max_length=4096)


@router.get("")
async def setup_state(request: Request):
    st = state(request)
    stage = await st.stage()
    return {"stage": stage, "defaults": DEFAULTS if stage == STAGE_STORAGE else None,
            "error": st.error if stage == STAGE_STORAGE else None}


@router.post("/verify")
async def setup_verify(st: SetupState = Depends(code_checked)):
    return {"ok": True, "stage": await st.stage()}


@router.post("/storage/check")
async def storage_check(body: StorageIn, st: SetupState = Depends(code_checked)):
    pg, ch, bao = body.postgres, body.clickhouse, body.openbao
    return {"postgres": await check_postgres(pg.host, pg.port, pg.database, pg.user, pg.password),
            "clickhouse": await check_clickhouse(ch.url, ch.user, ch.password),
            "openbao": await check_openbao(bao.addr)}


@router.post("/storage")
async def storage(body: StorageIn, st: SetupState = Depends(code_checked)):
    if await st.stage() != STAGE_STORAGE:
        raise DomainError("The storage is set up already")
    pg, ch, bao = body.postgres, body.clickhouse, body.openbao
    for res in (await check_postgres(pg.host, pg.port, pg.database, pg.user, pg.password),
                await check_clickhouse(ch.url, ch.user, ch.password), await check_openbao(bao.addr)):
        if not res["ok"]:
            raise DomainError(res["error"])
    shown: dict = {}
    if bao.mode == "bundled":
        shown = await BaoInitializer(bao.addr, st.boot.approle_dir).run(bao.root_token or None)
        mount = KV_MOUNT
    else:
        if not bao.role_id or not bao.secret_id:
            raise DomainError("Enter the AppRole role_id and secret_id of the external OpenBao")
        await approle_login(bao.addr, bao.role_id, bao.secret_id, bao.mount)
        write_approle(st.boot.approle_dir, bao.role_id, bao.secret_id)
        mount = bao.mount
    store = OpenBaoSecretStore(bao.addr, str(st.boot.approle_dir), mount)
    await store.put(PG_SECRET, {"password": pg.password})
    await store.put(CH_SECRET, {"password": ch.password})
    st.boot.write({
        "postgres": {"host": pg.host, "port": pg.port, "database": pg.database, "user": pg.user},
        "clickhouse": {"url": ch.url.rstrip("/"), "user": ch.user, "database": ch.database or "flows"},
        "openbao": {"addr": bao.addr.rstrip("/"), "mount": mount, "approle_dir": str(st.boot.approle_dir)},
    })
    if not await st.activate():
        raise DomainError(f"The storage is saved, but Network Control could not start on it: {st.error}")
    # The root token and the recovery key are shown once: they are kept nowhere.
    return {"stage": await st.stage(), "openbao": shown or None}


@router.post("/account")
async def account(body: AccountIn, request: Request, response: Response, st: SetupState = Depends(code_checked)):
    if await st.stage() != STAGE_ACCOUNT:
        raise DomainError("The storage is not set up yet" if await st.stage() == STAGE_STORAGE
                          else "Network Control is set up already")
    c = st.container
    actor = Actor(SETUP_ACTOR, Role.ADMIN.value, client_ip(request))
    if body.public_base_url:
        await c.settings.update({"public_base_url": body.public_base_url}, internal=True)
    if body.mode == MODE_LOCAL:
        user = await c.users.create(actor, body.username, body.display_name or "Administrator", Role.ADMIN,
                                    body.password, email=body.email, must_change=False)
        await c.settings.update({"auth_mode": MODE_LOCAL}, internal=True)
        token = await c.auth.start_session(user["id"], client_ip(request), request.headers.get("user-agent", ""),
                                           method="setup")
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=c.rc.session_ttl_hours * 3600, path="/")
        result = {"stage": STAGE_DONE, "user": user, "redirect": "/"}
    else:
        if not body.key:
            raise DomainError("Paste the connection key from Keystone (spa1.…)")
        await c.spa.set_key(body.key, actor)
        await c.spa.switch_to_keystone(c.auth, actor, purge_local=False)
        result = {"stage": STAGE_DONE, "redirect": "/api/auth/spa/start"}
    await c.rc.refresh_now()
    st.gate.finish()
    return result


@router.get("/keystone")
async def keystone_hint(request: Request, st: SetupState = Depends(code_checked)):
    """What to enter in Keystone when connecting the panel: its redirect and sign-out addresses."""
    base = (request.query_params.get("base") or str(request.base_url)).rstrip("/")
    return {"redirect_uri": f"{base}/api/auth/spa/callback", "post_logout_uri": f"{base}/",
            "roles": [r.value for r in (Role.ADMIN, Role.OPERATOR, Role.VIEWER)]}


