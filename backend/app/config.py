from functools import lru_cache

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
