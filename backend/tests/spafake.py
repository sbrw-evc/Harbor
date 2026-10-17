"""SPA stand-in for tests: an in-process OpenID Connect issuer (discovery, authorize → code, token, JWKS with an RS256
key) and the federation API (config, users with ETag, role catalogue), served to the SPA client through
httpx.ASGITransport."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

ISSUER = "http://spa.test"
CLIENT_ID = "app_nc"
CLIENT_SECRET = "s3cret"
TOKEN = "fed-token"


def connection_key(**over) -> str:
    body = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "token": TOKEN, **over}
    return "spa1." + base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()


class FakeSpa:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "k1"
        self.users: dict[str, dict] = {}
        self.signed_in: str | None = None  # the SPA user who is signed in at SPA
        self.codes: dict[str, dict] = {}
        self.published: list[dict] = []
        self.requests: list[tuple[str, str, dict]] = []
        self.tamper: dict = {}  # claims to override in the next ID tokens (aud, iss, nonce, …)
        self.import_open = False  # an administrator of SPA allowed moving users for the application
        self.imported: list[dict] = []
        self.app = self._build()

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app)

    def add_user(self, uid: str, username: str, roles: list[str], **kw) -> dict:
        u = {"id": uid, "username": username, "email": f"{username}@example.com", "name": username.title(),
             "disabled": False, "roles": roles, "groups": kw.pop("groups", ["noc"]), "source": "password",
             "updated_at": "2026-10-15T12:00:00Z", **kw}
        self.users[uid] = u
        return u

    def etag(self) -> str:
        return '"' + hashlib.sha256(json.dumps(self.users, sort_keys=True).encode()).hexdigest()[:16] + '"'

    def id_token(self, claims: dict) -> str:
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})

    def _build(self) -> FastAPI:
        app = FastAPI()
        spa = self

        def bearer_ok(request: Request) -> bool:
            return request.headers.get("authorization") == f"Bearer {TOKEN}"

        @app.middleware("http")
        async def record(request: Request, call_next):
            spa.requests.append((request.method, request.url.path, dict(request.headers)))
            return await call_next(request)

        @app.get("/.well-known/openid-configuration")
        async def discovery():
            return {"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/oauth2/authorize",
                    "token_endpoint": f"{ISSUER}/oauth2/token", "jwks_uri": f"{ISSUER}/oauth2/jwks",
                    "userinfo_endpoint": f"{ISSUER}/oauth2/userinfo", "end_session_endpoint": f"{ISSUER}/oauth2/logout",
                    "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"]}

        @app.get("/oauth2/jwks")
        async def jwks():
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(spa.key.public_key()))
            return {"keys": [{**jwk, "kid": spa.kid, "use": "sig", "alg": "RS256"}]}

        @app.get("/oauth2/authorize")
        async def authorize(request: Request):
            q = request.query_params
            assert q["client_id"] == CLIENT_ID and q["response_type"] == "code"
            assert q["code_challenge_method"] == "S256" and q["code_challenge"]
            assert set(q["scope"].split()) == {"openid", "profile", "email", "groups", "roles"}
            back = q["redirect_uri"]
            user = spa.users.get(spa.signed_in or "")
            if not user or user["disabled"] or not user["roles"]:
                err = {"error": "access_denied", "error_description": "No role in this application", "state": q["state"]}
                return RedirectResponse(f"{back}?{urlencode(err)}", status_code=302)
            code = secrets.token_urlsafe(16)
            spa.codes[code] = {"user": user["id"], "nonce": q["nonce"], "challenge": q["code_challenge"],
                               "redirect_uri": back}
            return RedirectResponse(f"{back}?{urlencode({'code': code, 'state': q['state']})}", status_code=302)

        @app.post("/oauth2/token")
        async def token(grant_type: str = Form(), code: str = Form(), redirect_uri: str = Form(),
                        client_id: str = Form(), client_secret: str = Form(""), code_verifier: str = Form("")):
            c = spa.codes.pop(code, None)
            if not c or client_id != CLIENT_ID or client_secret != CLIENT_SECRET or redirect_uri != c["redirect_uri"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
            if challenge != c["challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, status_code=400)
            u = spa.users[c["user"]]
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": u["id"], "iat": now, "exp": now + 300,
                      "nonce": c["nonce"], "preferred_username": u["username"], "name": u["name"], "email": u["email"],
                      "email_verified": True, "roles": u["roles"], "groups": u["groups"], "amr": ["hwk", "user", "mfa"],
                      "auth_time": now, "sid": "sid1", **spa.tamper}
            return {"access_token": "at", "token_type": "Bearer", "expires_in": 600, "id_token": spa.id_token(claims)}

        @app.get("/api/spa/v1/config")
        async def config(request: Request):
            if not bearer_ok(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return {"application": {"id": CLIENT_ID, "name": "Network Control", "type": "network-control"},
                    "roles": spa.published[-1]["roles"] if spa.published else [],
                    "methods": [{"id": "passkey", "title": "Passkey"}],
                    "sign_out_url": f"{ISSUER}/oauth2/logout", "account_url": f"{ISSUER}/profile",
                    "user_import": spa.import_open}

        @app.post("/api/spa/v1/users/import")
        async def import_users(request: Request):
            """docs/protocol.md of Keystone: the import window, conflicts by user name or e-mail, known hash formats,
            roles from the published catalogue; the window closes after a successful import."""
            if not bearer_ok(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            if not spa.import_open:
                return JSONResponse({"error": "import_not_allowed"}, status_code=403)
            body = await request.json()
            roles = {r["id"] for r in (spa.published[-1]["roles"] if spa.published else [])}
            out = {"imported": [], "conflicts": [], "rejected": []}
            for u in body.get("users") or []:
                name, h = u.get("username") or "", u.get("password_hash")
                taken = {x["username"].lower() for x in spa.users.values()}
                mails = {x["email"].lower() for x in spa.users.values()}
                if not name:
                    out["rejected"].append({"username": name, "reason": "invalid_username"})
                elif name.lower() in taken:
                    out["conflicts"].append({"username": name, "reason": "username_taken"})
                elif u.get("email") and u["email"].lower() in mails:
                    out["conflicts"].append({"username": name, "reason": "email_taken"})
                elif u.get("role") not in roles:
                    out["rejected"].append({"username": name, "reason": "invalid_role"})
                elif h is not None and not (h.startswith("$argon2id$") or h.startswith("pbkdf2-sha256$")):
                    out["rejected"].append({"username": name, "reason": "invalid_hash"})
                else:
                    uid = f"usr_imp{len(spa.users) + 1}"
                    spa.add_user(uid, name, [u["role"]], email=u.get("email") or f"{name}@example.com",
                                 name=u.get("name") or name)
                    spa.imported.append({**u, "id": uid})
                    out["imported"].append({"username": name, "id": uid})
            if out["imported"]:
                spa.import_open = False
            return out

        @app.get("/api/spa/v1/users")
        async def users(request: Request):
            if not bearer_ok(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            tag = spa.etag()
            if request.headers.get("if-none-match") == tag:
                return Response(status_code=304, headers={"etag": tag})
            return JSONResponse({"users": list(spa.users.values())}, headers={"etag": tag})

        @app.put("/api/spa/v1/roles")
        async def roles(request: Request):
            if not bearer_ok(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            spa.published.append(await request.json())
            return {"ok": True}

        return app
