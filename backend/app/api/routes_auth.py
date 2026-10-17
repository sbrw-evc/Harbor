"""Sign-in through SPA (Single Point of Authorization) and the sessions of Harbor."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..container import Container
from ..domain.errors import DomainError, Unauthorized
from ..services.identity import spa
from ..services.identity.spa import MODE_LOCAL
from ..services.users import user_dto
from .deps import COOKIE, Principal, client_ip, container, session_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _secure(request: Request, c: Container) -> bool:
    return bool(c.rc.cookie_secure) or request.url.scheme == "https"


def _to_login(code: str, description: str = "") -> RedirectResponse:
    q = {"sso_error": code, **({"sso_error_description": description} if description else {})}
    resp = RedirectResponse(f"/login?{urlencode(q)}", status_code=302)
    resp.delete_cookie(spa.COOKIE, path=spa.COOKIE_PATH)
    return resp


@router.get("/config")
async def config(c: Container = Depends(container)):
    """What the sign-in page needs: whether SPA is connected, and the address of the SPA account page."""
    return await c.spa.public_config()


@router.get("/spa/start")
async def spa_start(request: Request, c: Container = Depends(container), login_hint: str = "", idp_hint: str = "",
                    prompt: str = ""):
    return_path = request.query_params.get("return")
    try:
        url, verifier = await c.spa.start(_base(request), return_path, login_hint=login_hint[:128],
                                          idp_hint=idp_hint[:32], prompt=prompt)
    except spa.NotConnected:
        return _to_login("not_connected")
    except DomainError as e:
        return _to_login("unavailable", e.message)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(spa.COOKIE, verifier, httponly=True, samesite="lax", secure=_secure(request, c),
                    max_age=spa.FLOW_SECONDS, path=spa.COOKIE_PATH)
    return resp


@router.get("/spa/callback")
async def spa_callback(request: Request, c: Container = Depends(container), code: str = "", state: str = "",
                       error: str = "", error_description: str = ""):
    if error:
        return _to_login(error[:64], error_description[:300])
    if not code or not state:
        return _to_login("invalid_request", "The answer of SPA has no code or state")
    try:
        token, back = await c.spa.finish(_base(request), code, state, request.cookies.get(spa.COOKIE),
                                         client_ip(request), request.headers.get("user-agent", ""))
    except spa.SignInError as e:
        return _to_login(e.code, e.message)
    except spa.NotConnected:
        return _to_login("not_connected")
    except DomainError as e:
        return _to_login("server_error", e.message)
    resp = RedirectResponse(back, status_code=302)
    resp.delete_cookie(spa.COOKIE, path=spa.COOKIE_PATH)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=_secure(request, c),
                    max_age=int(c.rc.session_ttl_hours) * 3600, path="/")
    return resp


@router.post("/logout")
async def logout(request: Request, response: Response, c: Container = Depends(container)):
    """Ends the session of Harbor; the browser then goes to SPA's sign-out (`redirect`)."""
    token = request.cookies.get(COOKIE)
    if token:
        await c.auth.logout(token, client_ip(request))
    response.delete_cookie(COOKIE, path="/")
    redirect = await c.spa.sign_out_url(_base(request)) if await c.spa.active() else None
    return {"ok": True, "redirect": redirect}


@router.get("/me")
async def me(p: Principal = Depends(session_user)):
    return user_dto(p.user)


# The local sign-in (auth_mode "local") ------------------------------------------------------------------------------

class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


async def _local_only(c: Container) -> None:
    if await c.settings.get("auth_mode") != MODE_LOCAL:
        raise DomainError("Sign-in is through Keystone")


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, c: Container = Depends(container)):
    await _local_only(c)
    res = await c.auth.login(body.username, body.password, client_ip(request), request.headers.get("user-agent", ""))
    response.set_cookie(COOKIE, res.token, httponly=True, samesite="strict", secure=_secure(request, c),
                        max_age=int(c.rc.session_ttl_hours) * 3600, path="/")
    return {"requires_2fa": res.requires_2fa, "user": None if res.requires_2fa else user_dto(res.user)}


@router.post("/2fa")
async def second_factor(body: CodeIn, request: Request, c: Container = Depends(container)):
    await _local_only(c)
    token = request.cookies.get(COOKIE)
    if not token:
        raise Unauthorized("The sign-in took too long, sign in again")
    return {"user": user_dto(await c.auth.verify_2fa(token, body.code, client_ip(request)))}


@router.post("/password")
async def change_password(body: PasswordChangeIn, p: Principal = Depends(session_user),
                          c: Container = Depends(container)):
    await _local_only(c)
    await c.auth.change_password(p.user.id, body.current, body.new, p.token)
    return {"ok": True}


@router.post("/totp/setup")
async def totp_setup(p: Principal = Depends(session_user), c: Container = Depends(container)):
    await _local_only(c)
    return await c.auth.totp_begin(p.user)


@router.post("/totp/enable")
async def totp_enable(body: CodeIn, p: Principal = Depends(session_user), c: Container = Depends(container)):
    await _local_only(c)
    await c.auth.totp_enable(p.user.id, body.code)
    return {"ok": True}


@router.post("/totp/disable")
async def totp_disable(p: Principal = Depends(session_user), c: Container = Depends(container)):
    await _local_only(c)
    await c.auth.totp_disable(p.user.id, p.actor)
    return {"ok": True}


@router.get("/sessions")
async def sessions(p: Principal = Depends(session_user), c: Container = Depends(container)):
    return await c.auth.sessions(p.user.id, p.token)


@router.delete("/sessions/{sid}")
async def revoke(sid: str, p: Principal = Depends(session_user), c: Container = Depends(container)):
    await c.auth.revoke_session(p.user.id, sid)
    return {"ok": True}
