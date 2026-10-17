import argparse
import asyncio
import getpass

from .bootstrap import Bootstrap
from .config import get_settings, legacy_environ
from .db.base import Database
from .domain.models import Role
from .services.audit import AuditService
from .services.auth import AuthService
from .services.backups import BackupService
from .services.common import Actor, EventBus
from .services.identity.spa import MODE_LOCAL
from .services.settings import SettingsService
from .services.users import PanelUserService


async def _storage():
    cfg = get_settings()
    boot = Bootstrap(cfg.data_dir, cfg.bao_approle_dir)
    if not boot.exists():
        await boot.import_legacy(legacy_environ())
    if not boot.exists():
        raise SystemExit("Network Control is not set up yet: finish the setup wizard first")
    st = await boot.resolve()
    db = Database(st.database_url)
    await db.create_schema()
    return cfg, st, db


async def backup(action: str, path: str | None) -> None:
    cfg, st, db = await _storage()
    svc = BackupService(db, st.secrets, SettingsService(db), AuditService(db, EventBus()), cfg.backup_dir)
    if action == "create":
        print(await svc.create())
    else:
        print(await svc.restore_database(path))
    await db.engine.dispose()


async def create_admin(username: str) -> None:
    """A local administrator when nobody can sign in (Keystone gone, password forgotten); switches to local sign-in."""
    _, st, db = await _storage()
    audit = AuditService(db, EventBus())
    auth = AuthService(db, audit, 12, st.secrets)
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Again: "):
        raise SystemExit("The passwords differ")
    user = await PanelUserService(db, auth, audit).create(Actor("cli", Role.ADMIN.value), username, "Administrator",
                                                          Role.ADMIN, password)
    await SettingsService(db).update({"auth_mode": MODE_LOCAL}, internal=True)
    print(f"Administrator {user['username']} created; sign-in is local now (the password must be changed at first "
          "sign-in)")
    await db.engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup")
    rd = sub.add_parser("restore-db")
    rd.add_argument("archive")
    ca = sub.add_parser("create-admin")
    ca.add_argument("username")
    args = p.parse_args()
    if args.cmd == "backup":
        asyncio.run(backup("create", None))
    elif args.cmd == "create-admin":
        asyncio.run(create_admin(args.username))
    else:
        asyncio.run(backup("restore", args.archive))


if __name__ == "__main__":
    main()
