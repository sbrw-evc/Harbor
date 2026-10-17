import datetime as dt
import uuid

from pydantic import BaseModel, Field


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    owner: str = ""
    contact: str = ""
    tags: list[str] = []
    note: str = ""
    expires_at: dt.datetime | None = None
    quota_gb: float | None = Field(default=None, ge=0)
    server: str = Field(default="amnezia", pattern="^(amnezia|mikrotik|opnsense)$")


class ClientPatch(BaseModel):
    name: str | None = None
    owner: str | None = None
    contact: str | None = None
    tags: list[str] | None = None
    note: str | None = None
    expires_at: dt.datetime | None = None
    quota_gb: float | None = Field(default=None, ge=0)


class BulkIn(BaseModel):
    ids: list[uuid.UUID]
    action: str


class ShareIn(BaseModel):
    hours: int | None = Field(default=None, ge=1, le=24 * 30)


class ServerConfigIn(BaseModel):
    config: str = Field(max_length=500_000)
