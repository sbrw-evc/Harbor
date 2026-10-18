"""Where the panel keeps its data: PostgreSQL (TimescaleDB), ClickHouse and OpenBao, chosen in the setup wizard.

Before the wizard the panel runs in setup mode: only /api/setup answers, and a one-time setup code (printed to the
log and written to <data>/setup-code) is required for every step. The storage step writes <data>/bootstrap.json —
addresses and user names, never a password — and the AppRole credentials of OpenBao to <data>/approle/. The
passwords of PostgreSQL and ClickHouse go to OpenBao (infra/postgres, infra/clickhouse): the AppRole secret is the
only secret on disk.

Installations from before the wizard had DATABASE_URL, CLICKHOUSE_* and the AppRole files of the bao-init service:
they are taken over once (the passwords are moved to OpenBao) and bootstrap.json is written."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import string
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from .adapters.openbao import OpenBaoSecretStore
from .domain.errors import DomainError, Forbidden

log = logging.getLogger(__name__)

VERSION = 1
KV_MOUNT = "amnezia"
ROLE = "backend"
POLICY = "amnezia-backend"
POLICY_HCL = """path "amnezia/data/*"     { capabilities = ["create", "read", "update", "delete"] }
path "amnezia/metadata/*" { capabilities = ["read", "list", "delete"] }
path "sys/storage/raft/snapshot" { capabilities = ["read"] }
"""
PG_SECRET = "infra/postgres"
CH_SECRET = "infra/clickhouse"
CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
CODE_LENGTH = 12


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


@dataclass
class Storage:
    """The resolved connection parameters, passwords included (kept in memory only)."""
    database_url: str
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_db: str
    bao_addr: str
    bao_mount: str
    bao_approle_dir: str
    secrets: OpenBaoSecretStore


def pg_url(host: str, port: int, database: str, user: str, password: str) -> str:
    return (f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{int(port)}/"
            f"{quote(database, safe='')}")


def parse_pg_url(url: str) -> dict:
    u = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return {"host": u.hostname or "", "port": u.port or 5432, "database": unquote(u.path.lstrip("/")),
            "user": unquote(u.username or ""), "password": unquote(u.password or "")}


class SetupGate:
    """The one-time setup code: shared by the processes through a file, compared in constant time, attempts limited
    per address."""

    def __init__(self, data_dir: str, limit: int = 10, window: int = 600):
        self._path = Path(data_dir) / "setup-code"
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._limit, self._window = limit, window

    def ensure(self) -> str:
        try:
            code = self._path.read_text().strip()
            if code:
                return code
        except OSError:
            pass
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process was first
            return self._path.read_text().strip()
        with os.fdopen(fd, "w") as f:
            f.write(code + "\n")
        return code

    def announce(self) -> None:
        code = self.ensure()
        log.warning("Harbor is not set up yet. Open the panel in a browser and enter the setup code: %s "
                    "(also in %s)", code, self._path)

    def check(self, code: str | None, ip: str) -> None:
        now = time.monotonic()
        hits = [t for t in self._hits[ip] if now - t < self._window]
        self._hits[ip] = hits
        if len(hits) >= self._limit:
            raise Forbidden("Too many wrong setup codes. Wait ten minutes.")
        try:
            expected = self._path.read_text().strip()
        except OSError:
            expected = ""
        given = (code or "").strip().upper().replace("-", "").replace(" ", "")
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            hits.append(now)
            raise Forbidden("Wrong setup code: it is in the log of the backend container (docker compose logs backend)")

    def finish(self) -> None:
        """The wizard is done: the setup code and the OpenBao root token kept for retries go away."""
        for path in (self._path, self._path.parent / "openbao-init.json"):
            try:
                path.unlink()
            except OSError:
                pass


class Bootstrap:
    def __init__(self, data_dir: str, legacy_approle_dir: str = "/bao/approle"):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "bootstrap.json"
        self.approle_dir = self.data_dir / "approle"
        self._legacy_approle_dir = Path(legacy_approle_dir)

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    def write(self, data: dict) -> None:
        _write_private(self.path, json.dumps({"version": VERSION, **data}, indent=2) + "\n")

    async def resolve(self) -> Storage:
        """Reads bootstrap.json and the passwords from OpenBao."""
        d = self.read()
        bao = d["openbao"]
        store = OpenBaoSecretStore(bao["addr"], bao.get("approle_dir") or str(self.approle_dir),
                                   bao.get("mount") or KV_MOUNT)
        pg = d["postgres"]
        ch = d["clickhouse"]
        pg_pw = (await store.get(PG_SECRET) or {}).get("password", "")
        ch_pw = (await store.get(CH_SECRET) or {}).get("password", "")
        return Storage(pg_url(pg["host"], pg["port"], pg["database"], pg["user"], pg_pw), ch["url"].rstrip("/"),
                       ch["user"], ch_pw, ch.get("database") or "flows", bao["addr"], bao.get("mount") or KV_MOUNT,
                       bao.get("approle_dir") or str(self.approle_dir), store)

    async def import_legacy(self, environ: dict[str, str] | None = None) -> bool:
        """Takes over an installation from before the wizard (DATABASE_URL in the environment and the AppRole files of
        bao-init). Returns whether bootstrap.json was written."""
        env = os.environ if environ is None else environ
        url = env.get("DATABASE_URL", "").strip()
        approle = self._legacy_approle_dir
        if self.exists() or not url or not (approle / "role_id").exists():
            return False
        addr = env.get("BAO_ADDR", "http://openbao:8200")
        mount = env.get("BAO_KV_MOUNT", KV_MOUNT)
        store = OpenBaoSecretStore(addr, str(approle), mount)
        pg = parse_pg_url(url)
        await store.put(PG_SECRET, {"password": pg.pop("password")})
        await store.put(CH_SECRET, {"password": env.get("CLICKHOUSE_PASSWORD", "")})
        self.write({
            "postgres": pg,
            "clickhouse": {"url": env.get("CLICKHOUSE_URL", "http://clickhouse:8123"),
                           "user": env.get("CLICKHOUSE_USER", "amnezia"), "database": env.get("CLICKHOUSE_DB", "flows")},
            "openbao": {"addr": addr, "mount": mount, "approle_dir": str(approle)},
        })
        log.warning("took over the storage settings from .env (DATABASE_URL, CLICKHOUSE_*): the passwords are in "
                    "OpenBao now, the addresses in %s; remove them from .env", self.path)
        return True


# Checks of the storage step ------------------------------------------------------------------------------------------

async def check_postgres(host: str, port: int, database: str, user: str, password: str) -> dict:
    import asyncpg
    try:
        conn = await asyncio.wait_for(asyncpg.connect(host=host, port=int(port), database=database, user=user,
                                                      password=password), 10)
    except Exception as e:  # asyncpg raises many kinds; the message is what the person needs
        return {"ok": False, "error": f"PostgreSQL: {e or type(e).__name__}"}
    try:
        version = await conn.fetchval("SHOW server_version")
        tsdb = await conn.fetchval("SELECT count(*) FROM pg_available_extensions WHERE name = 'timescaledb'")
        return {"ok": True, "version": version, "timescaledb": bool(tsdb)}
    finally:
        await conn.close()


async def check_clickhouse(url: str, user: str, password: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.get(url.rstrip("/") + "/", params={"query": "SELECT version()"}, auth=(user, password))
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"ClickHouse: {e or type(e).__name__}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"ClickHouse: HTTP {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "version": r.text.strip()}


async def check_openbao(addr: str) -> dict:
    try:
        async with httpx.AsyncClient(base_url=addr, timeout=10) as h:
            r = await h.get("/v1/sys/seal-status")
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"OpenBao: {e or type(e).__name__}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"OpenBao: HTTP {r.status_code}"}
    s = r.json()
    return {"ok": True, "initialized": s.get("initialized"), "sealed": s.get("sealed"), "version": s.get("version")}


async def approle_login(addr: str, role_id: str, secret_id: str, mount: str) -> None:
    """Checks external AppRole credentials: login, then a write and a read of a test secret."""
    async with httpx.AsyncClient(base_url=addr, timeout=10) as h:
        r = await h.post("/v1/auth/approle/login", json={"role_id": role_id, "secret_id": secret_id})
        if r.status_code != 200:
            raise DomainError(f"OpenBao: AppRole login failed ({r.status_code})")
        token = r.json()["auth"]["client_token"]
        r = await h.post(f"/v1/{mount}/data/setup-check", headers={"X-Vault-Token": token},
                         json={"data": {"ok": True}})
        if r.status_code not in (200, 204):
            raise DomainError(f"OpenBao: the AppRole cannot write to {mount}/ ({r.status_code})")


class BaoInitializer:
    """Sets up the OpenBao of the compose file through its HTTP API, as openbao/bootstrap.sh did: initialisation (the
    static seal unseals it), the KV v2 mount, AppRole with the backend policy and role. The root token and the
    recovery key are shown once. Until the wizard is done they wait in <data>/openbao-init.json (0600), so that a step
    failing after the initialisation can be repeated without them; the wizard deletes the file at its end."""

    def __init__(self, addr: str, approle_dir: Path, transport: httpx.AsyncBaseTransport | None = None,
                 pending: Path | None = None, wait: float = 1.0):
        self._addr = addr
        self._dir = approle_dir
        self._transport = transport
        self._pending = pending or approle_dir.parent / "openbao-init.json"
        self._wait = wait

    async def _active(self, h: httpx.AsyncClient) -> None:
        """Waits until the node is unsealed and active (raft elects itself a moment after the unseal)."""
        for _ in range(60):
            r = await h.get("/v1/sys/health")
            if r.status_code == 200:
                return
            await asyncio.sleep(self._wait)
        raise DomainError("OpenBao does not become active: check openbao/keys/unseal.key and its log")

    async def run(self, root_token: str | None = None) -> dict:
        async with httpx.AsyncClient(base_url=self._addr, timeout=30, transport=self._transport) as h:
            status = (await h.get("/v1/sys/init")).json()
            shown: dict = {}
            if not status.get("initialized"):
                r = await h.put("/v1/sys/init", json={"recovery_shares": 1, "recovery_threshold": 1})
                if r.status_code != 200:
                    raise DomainError(f"OpenBao: initialisation failed ({r.status_code}): {r.text[:200]}")
                init = r.json()
                root_token = init["root_token"]
                shown = {"root_token": root_token,
                         "recovery_key": (init.get("recovery_keys_base64") or init.get("recovery_keys") or [""])[0]}
                _write_private(self._pending, json.dumps(shown))
            elif not root_token and self._pending.exists():
                shown = json.loads(self._pending.read_text())  # the first attempt failed after the initialisation
                root_token = shown["root_token"]
            elif not root_token:
                raise DomainError("OpenBao is initialised already: enter its root token (shown when it was "
                                  "initialised; for an old installation in data/bao-init/init.json)")
            await self._active(h)
            t = {"X-Vault-Token": root_token}

            async def call(method: str, path: str, ok=(200, 204), **kw):
                r = await h.request(method, path, headers=t, **kw)
                if r.status_code not in ok:
                    raise DomainError(f"OpenBao: {method} {path} returned {r.status_code}: {r.text[:200]}")
                return r

            mounts = (await call("GET", "/v1/sys/mounts")).json()
            if f"{KV_MOUNT}/" not in (mounts.get("data") or mounts):
                await call("POST", f"/v1/sys/mounts/{KV_MOUNT}", json={"type": "kv", "options": {"version": "2"}})
            auths = (await call("GET", "/v1/sys/auth")).json()
            if "approle/" not in (auths.get("data") or auths):
                await call("POST", "/v1/sys/auth/approle", json={"type": "approle"})
            await call("PUT", f"/v1/sys/policies/acl/{POLICY}", json={"policy": POLICY_HCL})
            await call("POST", f"/v1/auth/approle/role/{ROLE}", json={
                "token_policies": [POLICY], "token_ttl": "1h", "token_max_ttl": "24h", "secret_id_ttl": 0,
                "secret_id_num_uses": 0})
            role_id = (await call("GET", f"/v1/auth/approle/role/{ROLE}/role-id")).json()["data"]["role_id"]
            secret_id = (await call("POST", f"/v1/auth/approle/role/{ROLE}/secret-id")).json()["data"]["secret_id"]
        _write_private(self._dir / "role_id", role_id + "\n")
        _write_private(self._dir / "secret_id", secret_id + "\n")
        return shown


def write_approle(approle_dir: Path, role_id: str, secret_id: str) -> None:
    _write_private(approle_dir / "role_id", role_id.strip() + "\n")
    _write_private(approle_dir / "secret_id", secret_id.strip() + "\n")
