import datetime as dt
import uuid

from sqlalchemy import (BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String,
                        Text, func)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..domain.models import BlockReason, ClientStatus, Role, Severity


def _enum(e):
    return Enum(e, values_callable=lambda x: [m.value for m in x], native_enum=False, length=16)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PanelUser(TimestampMixin, Base):
    """A person who may use Network Control: a local user (source "local", signs in with a password kept in OpenBao)
    or a record synchronised from Keystone / SPA (source "spa", external_id = the SPA user ID). When the panel moves to
    Keystone, local records are bound to SPA once by username. Nothing secret is kept here: password hashes and TOTP
    secrets are in OpenBao (services/auth.py)."""
    __tablename__ = "panel_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16), default="spa", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str] = mapped_column(String(256), default="", server_default="")
    groups: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list, server_default="{}")
    synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class PanelSession(TimestampMixin, Base):
    __tablename__ = "panel_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("panel_users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    # A local sign-in waiting for its TOTP code: not a session yet.
    pending_2fa: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class VpnClient(TimestampMixin, Base):
    __tablename__ = "vpn_clients"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    public_key: Mapped[str] = mapped_column(String(64), unique=True)
    server: Mapped[str] = mapped_column(String(16), default="amnezia", server_default="amnezia", index=True)
    name: Mapped[str] = mapped_column(String(128))
    owner: Mapped[str] = mapped_column(String(128), default="")
    contact: Mapped[str] = mapped_column(String(128), default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    address: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[ClientStatus] = mapped_column(_enum(ClientStatus), default=ClientStatus.ACTIVE)
    block_reason: Mapped[BlockReason | None] = mapped_column(_enum(BlockReason))
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    last_handshake_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_endpoint: Mapped[str | None] = mapped_column(String(64))
    country_code: Mapped[str | None] = mapped_column(String(2))
    country: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    raw_rx: Mapped[int] = mapped_column(BigInteger, default=0)
    raw_tx: Mapped[int] = mapped_column(BigInteger, default=0)
    total_rx: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tx: Mapped[int] = mapped_column(BigInteger, default=0)
    config_downloaded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TrafficBucket(Base):
    __tablename__ = "traffic_buckets"
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vpn_clients.id", ondelete="CASCADE"), primary_key=True)
    bucket: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rx: Mapped[int] = mapped_column(BigInteger, default=0)
    tx: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (Index("ix_traffic_bucket", "bucket"),)


class ConnectionEvent(Base):
    __tablename__ = "connection_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vpn_clients.id", ondelete="CASCADE"), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ip: Mapped[str] = mapped_column(String(64))
    port: Mapped[int | None] = mapped_column(Integer)
    country_code: Mapped[str | None] = mapped_column(String(2))
    country: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)


class AuditEntry(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    target_name: Mapped[str] = mapped_column(String(128), default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    severity: Mapped[Severity] = mapped_column(_enum(Severity))
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text, default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(64))
    link: Mapped[str] = mapped_column(String(1024), default="", server_default="")


class ShareLink(TimestampMixin, Base):
    __tablename__ = "share_links"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vpn_clients.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    uses_left: Mapped[int] = mapped_column(Integer, default=1)


class ApiToken(TimestampMixin, Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    role: Mapped[Role] = mapped_column(_enum(Role))
    created_by: Mapped[str] = mapped_column(String(64))
    # The person the token belongs to: it works only while they are enabled in SPA, with at most their role.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("panel_users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SavedFilter(TimestampMixin, Base):
    __tablename__ = "saved_filters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    filter: Mapped[str] = mapped_column(Text)
    shared: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64))


class SettingRow(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)


class NetworkDevice(TimestampMixin, Base):
    """A network device managed by the panel: polled for facts, interfaces, neighbours and clients, and configured
    through its vendor driver. Credentials live in OpenBao at devices/<id>."""
    __tablename__ = "network_devices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(32), index=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    transport: Mapped[str] = mapped_column(String(16), default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    site: Mapped[str] = mapped_column(String(128), default="")
    location: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="other")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_seconds: Mapped[int] = mapped_column(Integer, default=300)
    flow_exporter_ip: Mapped[str] = mapped_column(String(64), default="")
    use_system_key: Mapped[bool] = mapped_column(Boolean, default=False)
    ssh_host_key: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_poll_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    facts: Mapped[dict] = mapped_column(JSONB, default=dict)
    interfaces: Mapped[list] = mapped_column(JSONB, default=list)
    neighbors: Mapped[list] = mapped_column(JSONB, default=list)
    children: Mapped[list] = mapped_column(JSONB, default=list)
    map_x: Mapped[float | None] = mapped_column(Float)
    map_y: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(String(64), default="")


class EndpointSighting(Base):
    """Where a device last saw a MAC: an ARP entry, a DHCP lease, a Wi-Fi association, a port of a switch."""
    __tablename__ = "endpoint_sightings"
    mac: Mapped[str] = mapped_column(String(17), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("network_devices.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    hostname: Mapped[str] = mapped_column(String(128), default="")
    interface: Mapped[str] = mapped_column(String(128), default="")
    ssid: Mapped[str] = mapped_column(String(64), default="")
    signal: Mapped[int | None] = mapped_column(Integer)
    vlan: Mapped[int | None] = mapped_column(Integer)
    attached_to: Mapped[str] = mapped_column(String(17), default="")
    port: Mapped[str] = mapped_column(String(64), default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NetworkEndpoint(Base):
    """A consumer of the network (phone, laptop, TV, camera, server…) merged from all sightings."""
    __tablename__ = "network_endpoints"
    mac: Mapped[str] = mapped_column(String(17), primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    alias: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    vendor: Mapped[str] = mapped_column(String(128), default="")
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("network_devices.id", ondelete="SET NULL"), index=True)
    interface: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(16), default="")
    attached_to: Mapped[str] = mapped_column(String(17), default="")
    port: Mapped[str] = mapped_column(String(64), default="")
    ssid: Mapped[str] = mapped_column(String(64), default="")
    signal: Mapped[int | None] = mapped_column(Integer)
    vlan: Mapped[int | None] = mapped_column(Integer)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class FlowRule(TimestampMixin, Base):
    """Traffic detection rule evaluated on ClickHouse flows; firing groups become alerts."""
    __tablename__ = "flow_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    filter: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(_enum(Severity), default=Severity.WARNING)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_eval_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")


class SavedView(TimestampMixin, Base):
    __tablename__ = "saved_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    shared: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64))
