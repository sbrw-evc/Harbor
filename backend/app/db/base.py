import logging
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

log = logging.getLogger(__name__)

MIGRATIONS = (
    "ALTER TABLE vpn_clients ADD COLUMN IF NOT EXISTS server VARCHAR(16) NOT NULL DEFAULT 'amnezia'",
    "CREATE INDEX IF NOT EXISTS ix_vpn_clients_server ON vpn_clients (server)",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS link VARCHAR(1024) NOT NULL DEFAULT ''",
    # Sign-in moves to SPA: users are bound to SPA user IDs; passwords, TOTP 2FA and the lockout go away.
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'local'",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS external_id VARCHAR(256)",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS email VARCHAR(256) NOT NULL DEFAULT ''",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS groups VARCHAR(128)[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_panel_users_external ON panel_users (source, external_id) WHERE external_id IS NOT NULL",
    # Password hashes never live in PostgreSQL: local passwords and TOTP secrets are in OpenBao (services/auth.py).
    "ALTER TABLE panel_users DROP COLUMN IF EXISTS password_hash",
    "DROP TABLE IF EXISTS panel_password_history",
    "DROP TABLE IF EXISTS panel_passkeys",
    # The local sign-in is back (standalone mode): what is not secret about it.
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ",
    "ALTER TABLE panel_users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ",
    "ALTER TABLE panel_sessions ADD COLUMN IF NOT EXISTS pending_2fa BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE api_tokens ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES panel_users (id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS ix_api_tokens_user_id ON api_tokens (user_id)",
    "UPDATE api_tokens t SET user_id = u.id FROM panel_users u WHERE t.user_id IS NULL AND lower(u.username) = lower(t.created_by)",
)


# Time series kept as TimescaleDB hypertables when the extension is there (the image of the compose file has it); on a
# plain PostgreSQL they stay ordinary tables. table → time column.
HYPERTABLES = {"traffic_buckets": "bucket", "connection_events": "ts"}


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(url, pool_size=10, max_overflow=5, pool_pre_ping=True)
        self._factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.timescale = False

    async def create_schema(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for sql in MIGRATIONS:
                await conn.execute(text(sql))
        await self._timescale()

    async def _timescale(self) -> None:
        try:
            async with self.engine.begin() as conn:
                available = (await conn.execute(text(
                    "SELECT count(*) FROM pg_available_extensions WHERE name = 'timescaledb'"))).scalar_one()
                if not available:
                    return
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        except Exception as e:  # not allowed to create extensions on an external server: ordinary tables then
            log.warning("TimescaleDB is not enabled: %s", e)
            return
        async with self.engine.begin() as conn:
            done = {r[0] for r in await conn.execute(text(
                "SELECT hypertable_name FROM timescaledb_information.hypertables"))}
            for table, column in HYPERTABLES.items():
                if table in done:
                    continue
                if table == "connection_events":
                    # A hypertable's unique keys must include the time column.
                    await conn.execute(text("ALTER TABLE connection_events DROP CONSTRAINT IF EXISTS connection_events_pkey"))
                    await conn.execute(text("ALTER TABLE connection_events ADD PRIMARY KEY (id, ts)"))
                await conn.execute(text(
                    f"SELECT create_hypertable('{table}', '{column}', migrate_data => true, if_not_exists => true)"))
                log.info("TimescaleDB: %s is a hypertable now", table)
        self.timescale = True

    async def drop_chunks(self, tables, before) -> None:
        if not self.timescale:
            return
        async with self.engine.begin() as conn:
            for table in tables:
                await conn.execute(text(f"SELECT drop_chunks('{table}', older_than => :before)"), {"before": before})

    @asynccontextmanager
    async def session(self):
        s: AsyncSession = self._factory()
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
        finally:
            await s.close()
