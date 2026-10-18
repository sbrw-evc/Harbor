"""The setup wizard (before the first administrator exists). Every step but the state needs the one-time setup code
in the X-Setup-Code header: it is printed to the log of the backend and written to <data>/setup-code.

1. storage — PostgreSQL (TimescaleDB), ClickHouse and OpenBao (the one of the compose file, or an external one with
   AppRole credentials): checked and kept as a draft on the server; nothing is applied yet, so it may still be changed;
2. account — the sign-in (a local administrator, or a Keystone connection key) and the address of the panel. All of it
   is checked first; then the storage is applied (OpenBao initialised, bootstrap.json written, Harbor started on it)
   and the administrator created or Keystone connected. The root token and the recovery key of an OpenBao initialised
   here come back in the answer, once.
After that the "First steps" checklist takes over (services/onboarding.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field

from ..bootstrap import (CH_SECRET, KV_MOUNT, PG_SECRET, BaoInitializer, approle_login, check_clickhouse, check_openbao,
                         check_postgres, write_approle)
from ..adapters.openbao import OpenBaoSecretStore
from ..domain.errors import DomainError
from ..domain.models import Role
from ..services.auth import PasswordHasher
from ..services.common import Actor
from ..services.identity.spa import MODE_LOCAL, check_key
from ..services.settings import validate
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
# The secrets of the draft: never sent back to the browser; one left empty in the form keeps the one of the draft.
DRAFT_SECRETS = (("postgres", "password"), ("clickhouse", "password"), ("openbao", "root_token"),
                 ("openbao", "secret_id"))


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
    editable = stage != STAGE_DONE and st.storage_editable
    return {"stage": stage, "defaults": DEFAULTS if editable else None, "storage_editable": editable,
            "error": st.error if editable else None}


@router.post("/verify")
async def setup_verify(st: SetupState = Depends(code_checked)):
    return {"ok": True, "stage": await st.stage()}


@router.post("/storage/check")
async def storage_check(body: StorageIn, st: SetupState = Depends(code_checked)):
    pg, ch, bao = body.postgres, body.clickhouse, body.openbao
    return {"postgres": await check_postgres(pg.host, pg.port, pg.database, pg.user, pg.password),
            "clickhouse": await check_clickhouse(ch.url, ch.user, ch.password),
            "openbao": await check_openbao(bao.addr)}


async def _check_storage(d: dict, st: SetupState) -> None:
    """Everything the storage needs, checked without changing anything."""
    pg, ch, bao = d["postgres"], d["clickhouse"], d["openbao"]
    for res in (await check_postgres(pg["host"], pg["port"], pg["database"], pg["user"], pg["password"]),
                await check_clickhouse(ch["url"], ch["user"], ch["password"]), await check_openbao(bao["addr"])):
        if not res["ok"]:
            raise DomainError(res["error"])
    if bao["mode"] == "bundled":
        await BaoInitializer(bao["addr"], st.boot.approle_dir).precheck(bao["root_token"] or None)
    else:
        if not bao["role_id"] or not bao["secret_id"]:
            raise DomainError("Enter the AppRole role_id and secret_id of the external OpenBao")
        await approle_login(bao["addr"], bao["role_id"], bao["secret_id"], bao["mount"])


@router.get("/storage")
async def storage_draft(st: SetupState = Depends(code_checked)):
    """The storage the wizard keeps, without its secrets: which of them are kept is told instead."""
    d = st.draft.read() if st.storage_editable else None
    if not d:
        return {"draft": None, "kept": {}}
    kept = {f"{part}.{key}": bool(d[part].get(key)) for part, key in DRAFT_SECRETS}
    for part, key in DRAFT_SECRETS:
        d[part][key] = ""
    return {"draft": d, "kept": kept}


@router.post("/storage")
async def storage(body: StorageIn, st: SetupState = Depends(code_checked)):
    """Checks the storage and keeps it as the draft of the wizard: the last step applies it."""
    if not st.storage_editable:
        raise DomainError("The storage is set up already")
    d = body.model_dump()
    kept = st.draft.read() or {}
    for part, key in DRAFT_SECRETS:
        if not d[part][key]:
            d[part][key] = (kept.get(part) or {}).get(key, "")
    d["openbao"]["addr"] = d["openbao"]["addr"].rstrip("/")
    d["clickhouse"]["url"] = d["clickhouse"]["url"].rstrip("/")
    d["clickhouse"]["database"] = d["clickhouse"]["database"] or "flows"
    await _check_storage(d, st)
    st.draft.write(d)
    return {"stage": await st.stage()}


async def _apply_storage(st: SetupState) -> None:
    """Applies the draft of the storage: OpenBao, the passwords in it, bootstrap.json, Harbor started on it."""
    d = st.draft.read()
    if not d:
        raise DomainError("The storage is not set up yet")
    await _check_storage(d, st)  # the draft may be old
    pg, ch, bao = d["postgres"], d["clickhouse"], d["openbao"]
    if bao["mode"] == "bundled":
        await BaoInitializer(bao["addr"], st.boot.approle_dir).run(bao["root_token"] or None)
        mount = KV_MOUNT
    else:
        write_approle(st.boot.approle_dir, bao["role_id"], bao["secret_id"])
        mount = bao["mount"]
    store = OpenBaoSecretStore(bao["addr"], str(st.boot.approle_dir), mount)
    await store.put(PG_SECRET, {"password": pg["password"]})
    await store.put(CH_SECRET, {"password": ch["password"]})
    st.boot.write({
        "postgres": {"host": pg["host"], "port": pg["port"], "database": pg["database"], "user": pg["user"]},
        "clickhouse": {"url": ch["url"], "user": ch["user"], "database": ch["database"]},
        "openbao": {"addr": bao["addr"], "mount": mount, "approle_dir": str(st.boot.approle_dir)},
    })
    if not await st.activate():
        # Not left half applied: the storage can be corrected and the step repeated.
        st.boot.path.unlink(missing_ok=True)
        raise DomainError(f"Harbor could not start on the storage: {st.error}")


@router.post("/account")
async def account(body: AccountIn, request: Request, response: Response, st: SetupState = Depends(code_checked)):
    """The last step: applies everything the wizard was given."""
    stage = await st.stage()
    if stage != STAGE_ACCOUNT:
        raise DomainError("The storage is not set up yet" if stage == STAGE_STORAGE else "Harbor is set up already")
    # Everything is checked before anything is applied.
    try:
        base = validate({"public_base_url": body.public_base_url})["public_base_url"] if body.public_base_url else ""
    except ValueError as e:
        raise DomainError(str(e)) from e
    if body.mode == MODE_LOCAL:
        username = body.username.strip()
        if not username or any(c.isspace() for c in username):
            raise DomainError("Enter a user name without spaces")
        PasswordHasher.validate_strength(body.password)
    else:
        if not body.key:
            raise DomainError("Paste the connection key from Keystone (spa1.…)")
        await check_key(body.key)
    if st.container is None:
        await _apply_storage(st)
    c = st.container
    actor = Actor(SETUP_ACTOR, Role.ADMIN.value, client_ip(request))
    if base:
        await c.settings.update({"public_base_url": base}, internal=True)
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
        await c.spa.set_key(body.key, actor)
        await c.spa.switch_to_keystone(c.auth, actor, purge_local=False)
        result = {"stage": STAGE_DONE, "redirect": "/api/auth/spa/start"}
    await c.rc.refresh_now()
    # The root token and the recovery key of an OpenBao initialised here are shown once: with the end of the wizard
    # they are kept nowhere.
    keys = st.draft.bao_keys()
    st.gate.finish()
    return {**result, "openbao": keys}


@router.get("/keystone")
async def keystone_hint(request: Request, st: SetupState = Depends(code_checked)):
    """What to enter in Keystone when connecting the panel: its redirect and sign-out addresses."""
    base = (request.query_params.get("base") or str(request.base_url)).rstrip("/")
    return {"redirect_uri": f"{base}/api/auth/spa/callback", "post_logout_uri": f"{base}/",
            "roles": [r.value for r in (Role.ADMIN, Role.OPERATOR, Role.VIEWER)]}
