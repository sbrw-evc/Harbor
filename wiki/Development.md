# Development

## Structure

```
backend/app/
  domain/        value objects and errors
  ports.py       abstractions of external systems
  adapters/      Amnezia (SSH), MikroTik (SSH), OpenBao, ClickHouse, DB-IP, Telegram, IEEE OUI
  adapters/devices/  device drivers: REST / RCI / Open API, SSH CLI profiles and parsers, SNMP, discovery
  db/            SQLAlchemy models, Database, repositories
  services/      business logic
  api/           FastAPI routers, schemas, dependencies
  cluster.py     event bus, leader election, distributed locks
  workers.py     periodic job scheduler
  container.py   composition root
  cli.py         backup and restore
frontend/src/
  lib/           HttpClient and resource APIs, SSE, notifications, formatting, hooks
  components/    UI primitives and form controls, charts, map, layout, clients, administration
  pages/         SPA pages
flow-collector/  goflow2 + flow enrichment
awg-client/      AmneziaWG client image for the gateway
openbao/         configuration and bootstrap
clickhouse/      resource limits
mikrotik/        router setup and rollback scripts
```

## Principles

- **SOLID**: services depend on the abstractions in `ports.py`; concrete adapters are created only in `container.py`.
  A new router type or secret store is a new adapter, with no changes to services.
- **DRY**: a shared `Repository[T]`, a single `ServerConfigEditor` for every server config change,
  `useAction` (mutation + notification + invalidation), `notify` for all messages and `Resource` on the frontend.
- The AmneziaWG server is the source of truth for peers; PostgreSQL stores metadata and statistics; keys live only in OpenBao.
- Changes are applied to the server first, then to the database; synchronization resolves any drift.
- Asynchronous I/O everywhere; CPU-heavy work runs in a thread pool.

## Running the frontend locally

```bash
cd frontend
npm install
VITE_API_TARGET=http://<host>:8080 npm run dev
```

## Building

```bash
docker compose build backend frontend flow-collector
docker compose up -d
```

The frontend build runs a type check (`tsc -b`).

## Adding a flow dimension

1. Add a `Field` to `backend/app/services/flowfilter.py` (name, type, column, label, dimension SQL expression).
2. If needed, add a column to `FlowSchema` and populate it in `flow-collector/collector.py`.
3. The frontend picks up the field list from `/api/flows/fields` automatically.

## Adding a device platform

1. Write a driver in `backend/app/adapters/devices/` (or a `CliProfile` in `cli.py` for a CLI that looks like Cisco or
   Comware): `collect()` returns a `Snapshot` (facts, interfaces, neighbours, endpoints, children), `apply()` implements
   the actions listed in `capabilities`, `backup()` returns the configuration text.
2. Register it in `PLATFORMS` (`adapters/devices/__init__.py`) with its transports, default ports and credential
   fields — the form, the capability matrix and the API pick it up from there.
3. Add parser tests with captured output to `backend/tests/test_drivers.py`.

## Tests

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/test python -m pytest -q tests
```

The suite runs the real ClickHouse SQL (schema, migrations, aggregates, visualization, detector rules) on the embedded
engine `chdb`, the device service and the network map on PostgreSQL (skipped without `TEST_DATABASE_URL`), the CLI
drivers over a real SSH session against an emulated Cisco IOS, and the HTTP drivers against mocked APIs.
