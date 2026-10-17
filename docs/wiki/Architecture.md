# Architecture

## Components

| Container | Image | Role |
|---|---|---|
| `nc-awg-client` | `amneziavpn/amneziawg-go` | The gateway's AmneziaWG client. `network_mode: host`. All traffic arriving on the LAN interface goes into `awg0` (table 200), with NAT and MSS clamp. The protocol version matches the Amnezia server. |
| `nc-postgres` | `postgres:16-alpine` | Clients, traffic buckets (5 min), connections, audit, alerts, operators, sessions, API tokens, saved filters, settings. |
| `nc-openbao` | `openbao/openbao:2.6` | Secret store (KV v2 `amnezia/`), raft storage, static auto-unseal. |
| (setup wizard) | — | The backend initialises OpenBao on the first start: init, KV, policy, `backend` AppRole; the panel creates its SSH key. |
| `nc-clickhouse` | `clickhouse/clickhouse-server:24.8` | `flows` table, `site_prefixes` table and the `sites` dictionary (`ip_trie`). Memory limits for small hosts. |
| `nc-flow-collector` | goflow2 + Python | Receives NetFlow v5/v9, IPFIX (UDP 2055) and sFlow (UDP 6343), enriches records (country, ASN, direction, route, exporter, interfaces, TCP flags) and writes them to ClickHouse in batches. |
| `nc-backend` | Python 3.12, FastAPI | REST API, SSE, background jobs. Several uvicorn processes. |
| `nc-frontend` | nginx + React SPA | SPA static files, `/api` proxy, caching OpenStreetMap tile proxy at `/tiles`. |

Networks: `internal` (no outbound access: PostgreSQL, OpenBao, ClickHouse) and `egress` (backend, frontend, collector).

## Backend

Layers (ports and adapters):

- `domain` — value objects and enums with no I/O (`Role`, `ClientStatus`, `PeerRuntime`, …).
- `ports` — abstractions: `VpnServerGateway`, `RouterGateway`, `SecretStore`, `GeoLocator`, `Notifier`, `CommandRunner`, `ConfigCodec`.
- `adapters` — implementations: Amnezia in Docker over SSH, MikroTik over SSH, OpenBao (AppRole), DB-IP, Telegram, ClickHouse,
  IEEE OUI, and `adapters/devices` — device drivers on a common model (`Snapshot`: facts, interfaces, neighbours,
  endpoints, children) over REST / RCI / Open API (`mikrotik`, `appliances`, `controllers`), SSH CLI profiles and parsers
  (`cli`, `parsers`), SNMP (`snmp`) and discovery (`discovery`).
- `db` — SQLAlchemy models and repositories (a shared generic `Repository[T]`).
- `services` — business logic: clients, policies, traffic, analytics, flows, routing, alerts, notifications, backups, authentication, tokens.
- `api` — FastAPI routers and dependencies (authentication, RBAC, CSRF).
- `container.py` — composition root: the only place where concrete adapters are created.

### Multiple processes

The API runs in several processes (`WEB_CONCURRENCY`). They coordinate through PostgreSQL:

- **Event bus** — `LISTEN/NOTIFY`: an event published in any process reaches every SSE client.
- **Leader** — a session advisory lock. Only the leader runs background jobs (server polling, policies, checks,
  backups, ClickHouse schema). If the leader dies, another process takes over within ~10 seconds.
- **Distributed lock** — all changes to `awg0.conf` are serialized across processes.
- **LiveState** — a per-process cache of live metrics, fed from the bus.

CPU-heavy work (QR codes, site list parsing, database decompression) runs in a thread pool.

### Background jobs (leader)

| Job | Interval | What it does |
|---|---|---|
| traffic | `poll_seconds` | `awg show dump`: traffic deltas, handshakes, endpoint changes, geolocation, live event |
| policy | 60 s | Block/unblock by expiry and quota, warnings |
| health | 60 s | OpenBao, gateway handshake freshness |
| router | 60 s | Route status on MikroTik |
| sync | 5 min | Import peers created outside the panel |
| backup | daily | Backup and rotation |
| flow-schema | 5 min | ClickHouse schema and TTL |
| purge | hourly | Delete statistics older than `retention_days` |
| devices | 30 s | Poll devices whose `poll_seconds` elapsed (6 at a time), merge network clients, `device.down` alerts |
| flow-enrichment | 2 min | Exporter, interface and LAN host names, local subnets → ClickHouse dictionaries |
| flow-rules | 60 s | Traffic detection rules → alerts |
| threats | `threat_refresh_hours` | Download public blocklists into the `threats` dictionary |
| rdns | 10 min | Reverse DNS for the most active remote addresses |
| oui | weekly | IEEE OUI registry for MAC vendors |

## Frontend

React 18 + TypeScript + Vite, Tailwind, Framer Motion, AutoAnimate, ECharts, Leaflet, TanStack Query, Zustand.
The API layer is a set of classes — `HttpClient` and resource APIs (`ClientsApi`, `FlowsApi`, …) sharing a base `Resource`.
Live data: a single SSE connection (`LiveChannel`) and `LiveQuerySync`, which maps events to cached queries.
Form controls (select, checkbox, radio, number input) are custom components, so they look the same in every browser.

## PostgreSQL data model

`panel_users`, `panel_sessions`, `api_tokens`, `vpn_clients`, `traffic_buckets`, `connection_events`, `audit_log`,
`alerts`, `share_links`, `saved_filters`, `saved_views`, `settings`, `network_devices` (registry, facts, interfaces,
neighbours, controller children as JSONB), `endpoint_sightings` (where each MAC was seen), `network_endpoints`
(merged network clients), `flow_rules`. The schema is created at startup. Device credentials live in OpenBao
`devices/<id>`.

## ClickHouse

```sql
flows(TimeReceived DateTime, Direction Enum8('out','in','internal'), Route Enum8('direct','vpn'),
      LocalAddr IPv6, RemoteAddr IPv6, Proto UInt8, LocalPort UInt16, RemotePort UInt16,
      RemoteAS UInt32, RemoteASName String, RemoteCountry String, Bytes UInt64, Packets UInt64, Flows UInt32,
      ExporterAddress IPv6, InIf UInt32, OutIf UInt32, TCPFlags UInt8, SamplingRate UInt32, ServicePort UInt16)
ENGINE = MergeTree PARTITION BY toYYYYMMDD(TimeReceived) TTL TimeReceived + INTERVAL 30 DAY

flows_5m, flows_1h  ← MATERIALIZED VIEW (SummingMergeTree, 90 days / 2 years)

site_prefixes(prefix, site, grp)      →  DICTIONARY sites     LAYOUT(IP_TRIE())
threat_prefixes(prefix, list)         →  DICTIONARY threats   LAYOUT(IP_TRIE())
exporters_data, hosts_data, ptr_data  →  DICTIONARY exporters, hosts, ptr  LAYOUT(HASHED())
if_names_data(addr, idx, name, speed) →  DICTIONARY if_names  LAYOUT(COMPLEX_KEY_HASHED())
local_networks(prefix)                   read by the collector
```
