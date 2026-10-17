"""People of the panel. In the local mode (standalone) they are created and managed here, with their passwords and TOTP
secrets in OpenBao (services/auth.py). In the Keystone mode the records are synchronised from Keystone / SPA
(services/identity/spa.py) and are read-only here."""
from ..db.base import Database
from ..db.models import PanelUser
from ..db.repositories import SessionRepo, UserRepo
from ..domain.errors import Conflict, DomainError
from ..domain.models import Role
from .audit import AuditService
from .auth import AuthService
from .common import Actor


def user_dto(u: PanelUser) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "role": u.role.value,
            "is_active": u.is_active, "source": u.source, "external_id": u.external_id, "groups": list(u.groups or []),
            "totp_enabled": bool(u.totp_enabled), "must_change_password": bool(u.must_change_password),
            "locked": bool(u.locked_until),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "synced_at": u.synced_at.isoformat() if u.synced_at else None}


class PanelUserService:
    def __init__(self, db: Database, auth: AuthService | None = None, audit: AuditService | None = None):
        self._db = db
        self._auth = auth
        self._audit = audit

    async def list(self) -> list[dict]:
        async with self._db.session() as s:
            return [user_dto(u) for u in await UserRepo(s).all(order_by=PanelUser.id)]

    async def create(self, actor: Actor, username: str, display_name: str, role: Role, password: str,
                     email: str = "", must_change: bool = True) -> dict:
        username = username.strip()
        if not username or len(username) > 64 or any(c.isspace() for c in username):
            raise DomainError("Enter a user name without spaces")
        self._auth.hasher.validate_strength(password)
        async with self._db.session() as s:
            repo = UserRepo(s)
            if await repo.by_username(username):
                raise Conflict("A user with this name exists already")
            u = repo.add(PanelUser(username=username, display_name=display_name.strip()[:128], email=email.strip()[:256],
                                   role=role, source="local", is_active=True, must_change_password=must_change))
            await s.flush()
            user_id = u.id
        try:
            await self._auth.set_password(user_id, password)
        except Exception:
            async with self._db.session() as s:
                await UserRepo(s).remove(await UserRepo(s).require(user_id))
            raise
        async with self._db.session() as s:
            u = await UserRepo(s).require(user_id)
            self._audit.record(s, actor, "panel_user.created", "panel_user", u.id, u.username, role=role.value)
            return user_dto(u)

    @staticmethod
    def _local(u: PanelUser) -> None:
        if u.source != "local":
            raise DomainError("This user comes from Keystone: change it there")

    async def update(self, actor: Actor, user_id: int, *, display_name=None, email=None, role: Role | None = None,
                     is_active: bool | None = None) -> dict:
        async with self._db.session() as s:
            repo = UserRepo(s)
            u = await repo.require(user_id)
            self._local(u)
            demoting = (role and role != Role.ADMIN and u.role == Role.ADMIN) or (is_active is False and u.role == Role.ADMIN)
            if demoting and await repo.count_admins() <= 1:
                raise DomainError("The last active administrator cannot be demoted or disabled")
            if u.username == actor.username and (is_active is False or (role and role != u.role)):
                raise DomainError("You cannot change your own role or disable yourself")
            changes = {}
            if display_name is not None:
                u.display_name = display_name.strip()[:128]
            if email is not None:
                u.email = email.strip()[:256]
            if role is not None and role != u.role:
                changes["role"] = [u.role.value, role.value]
                u.role = role
            if is_active is not None and is_active != u.is_active:
                changes["is_active"] = is_active
                u.is_active = is_active
                if not is_active:
                    await SessionRepo(s).revoke_user(u.id)
            self._audit.record(s, actor, "panel_user.updated", "panel_user", u.id, u.username, **changes)
            return user_dto(u)

    async def reset_password(self, actor: Actor, user_id: int, password: str) -> None:
        async with self._db.session() as s:
            self._local(await UserRepo(s).require(user_id))
        await self._auth.set_password(user_id, password)
        async with self._db.session() as s:
            u = await UserRepo(s).require(user_id)
            u.must_change_password = True
            u.locked_until = None
            u.failed_attempts = 0
            await SessionRepo(s).revoke_user(u.id)
            self._audit.record(s, actor, "panel_user.password_reset", "panel_user", u.id, u.username)

    async def delete(self, actor: Actor, user_id: int) -> None:
        async with self._db.session() as s:
            repo = UserRepo(s)
            u = await repo.require(user_id)
            self._local(u)
            if u.username == actor.username:
                raise DomainError("You cannot delete yourself")
            if u.role == Role.ADMIN and await repo.count_admins() <= 1:
                raise DomainError("The last administrator cannot be deleted")
            self._audit.record(s, actor, "panel_user.deleted", "panel_user", u.id, u.username)
            await repo.remove(u)
        await self._auth.forget(user_id)
