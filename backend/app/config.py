import os
from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """What the backend process needs before it can read its settings: where its files are. The storage (PostgreSQL,
    ClickHouse, OpenBao) is chosen in the setup wizard (app/bootstrap.py) and everything else lives in the settings
    (services/settings.py). Values from older .env files are imported once."""
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    data_dir: str = "/data"
    geoip_dir: str = "/data/geoip"
    backup_dir: str = "/data/backups"
    # Where the shared files for awg-client and flow-collector are written (gateway.json, awg0.conf, collector.json).
    gateway_config_dir: str = "/gateway"
    shared_dir: str = "/shared"
    # The AppRole files of the old bao-init service, read once when an old installation is taken over.
    bao_approle_dir: str = "/bao/approle"

    # SPA (Keystone): the connection key is entered in the panel and kept in OpenBao (spa/connection_key); these
    # still override it for installations that set them.
    spa_connection_key: str = ""
    spa_connection_key_file: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


LEGACY_ENV_FILE = "/legacy.env"


def legacy_environ(path: str = LEGACY_ENV_FILE) -> dict[str, str]:
    """The values of an older installation, read once to take it over: its .env (mounted read-only by the compose
    file) under the environment of the process. Nothing here is needed by a new installation."""
    values: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                values[k.strip()] = v
    except OSError:
        pass
    old = {k: v for k, v in os.environ.items()}
    merged = {**values, **old}
    # The old compose file built DATABASE_URL from POSTGRES_PASSWORD for the bundled PostgreSQL.
    if not merged.get("DATABASE_URL") and merged.get("POSTGRES_PASSWORD"):
        merged["DATABASE_URL"] = (f"postgresql+asyncpg://amnezia:{quote(merged['POSTGRES_PASSWORD'], safe='')}"
                                  "@postgres:5432/amnezia")
    return merged
