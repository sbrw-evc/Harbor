# Архитектура

## Компоненты

| Контейнер | Образ | Роль |
|---|---|---|
| `nc-awg-client` | `amneziavpn/amneziawg-go` | Клиент AmneziaWG шлюза. `network_mode: host`. Весь трафик, пришедший с LAN-интерфейса, уходит в `awg0` (таблица 200), NAT и MSS clamp. Версия протокола совпадает с сервером Amnezia. |
| `nc-postgres` | `postgres:16-alpine` | Клиенты, бакеты трафика (5 мин), подключения, аудит, алерты, операторы, сессии, API-токены, сохранённые фильтры, настройки. |
| `nc-openbao` | `openbao/openbao:2.6` | Хранилище секретов (KV v2 `amnezia/`), raft-storage, static auto-unseal. |
| (мастер настройки) | — | Backend инициализирует OpenBao при первом запуске: init, KV, политика, AppRole `backend`; панель сама создаёт SSH-ключ. |
| `nc-clickhouse` | `clickhouse/clickhouse-server:24.8` | Таблица `flows`, таблица `site_prefixes` и словарь `sites` (`ip_trie`). Лимиты памяти для слабых хостов. |
| `nc-flow-collector` | goflow2 + Python | Принимает NetFlow v5/v9, IPFIX (UDP 2055) и sFlow (UDP 6343), обогащает записи (страна, ASN, направление, маршрут, экспортёр, интерфейсы, TCP-флаги) и пишет их в ClickHouse пачками. |
| `nc-backend` | Python 3.12, FastAPI | REST API, SSE, фоновые задачи. Несколько процессов uvicorn. |
| `nc-frontend` | nginx + React SPA | Статика SPA, прокси `/api`, кэширующий прокси тайлов OpenStreetMap `/tiles`. |

Сети: `internal` (без выхода наружу: PostgreSQL, OpenBao, ClickHouse) и `egress` (backend, frontend, коллектор).

## Бэкенд

Слои (порты и адаптеры):

- `domain` — сущности-значения и перечисления без ввода-вывода (`Role`, `ClientStatus`, `PeerRuntime`, …).
- `ports` — абстракции: `VpnServerGateway`, `RouterGateway`, `SecretStore`, `GeoLocator`, `Notifier`, `CommandRunner`, `ConfigCodec`.
- `adapters` — реализации: Amnezia в Docker по SSH, MikroTik по SSH, OpenBao (AppRole), DB-IP, Telegram, ClickHouse.
- `db` — модели SQLAlchemy и репозитории (общий базовый `Repository[T]`).
- `services` — бизнес-логика: клиенты, политики, трафик, аналитика, потоки, маршрутизация, алерты, уведомления, бэкапы, аутентификация, токены.
- `api` — роутеры FastAPI и зависимости (аутентификация, RBAC, CSRF).
- `container.py` — composition root: единственное место, где создаются конкретные адаптеры.

### Несколько процессов

API запускается в нескольких процессах (`WEB_CONCURRENCY`). Согласование — через PostgreSQL:

- **Шина событий** — `LISTEN/NOTIFY`: событие из любого процесса получают все SSE-клиенты.
- **Лидер** — session advisory lock. Только лидер выполняет фоновые задачи (опрос сервера, политики, проверки,
  бэкапы, схема ClickHouse). При падении лидера другой процесс перехватывает роль за ~10 секунд.
- **Распределённая блокировка** — все изменения `awg0.conf` сериализуются между процессами.
- **LiveState** — кэш live-метрик в каждом процессе, наполняемый из шины.

CPU-ёмкие операции (QR-коды, разбор списка сайтов, распаковка баз) выполняются в пуле потоков.

### Фоновые задачи (лидер)

| Задача | Период | Что делает |
|---|---|---|
| traffic | `poll_seconds` | `awg show dump`: дельты трафика, handshake, смена endpoint, геолокация, live-событие |
| policy | 60 с | Блокировка/разблокировка по сроку и квоте, предупреждения |
| health | 60 с | OpenBao, свежесть handshake шлюза |
| router | 60 с | Статус маршрута на MikroTik |
| sync | 5 мин | Импорт пиров, созданных вне панели |
| backup | сутки | Резервная копия и ротация |
| flow-schema | 5 мин | Схема ClickHouse и TTL |
| purge | час | Удаление статистики старше `retention_days` |
| devices | 30 с | Опрос устройств, у которых истёк `poll_seconds` (по 6), объединение клиентов сети, алерты `device.down` |
| flow-enrichment | 2 мин | Имена экспортёров, интерфейсов и хостов LAN, локальные подсети → словари ClickHouse |
| flow-rules | 60 с | Правила обнаружения по трафику → алерты |
| threats | `threat_refresh_hours` | Загрузка публичных списков угроз в словарь `threats` |
| rdns | 10 мин | Обратный DNS для самых активных удалённых адресов |
| oui | неделя | Реестр IEEE OUI для производителей по MAC |

## Фронтенд

React 18 + TypeScript + Vite, Tailwind, Framer Motion, AutoAnimate, ECharts, Leaflet, TanStack Query, Zustand.
API-слой — классы `HttpClient` и ресурсные API (`ClientsApi`, `FlowsApi`, …) с общим базовым `Resource`.
Живые данные: одно SSE-соединение (`LiveChannel`) и `LiveQuerySync`, сопоставляющий события с кэшем запросов.
Элементы форм (выпадающий список, чекбокс, переключатель, числовое поле) — собственные компоненты, поэтому выглядят одинаково во всех браузерах.

## Модель данных PostgreSQL

`panel_users`, `panel_sessions`, `api_tokens`, `vpn_clients`, `traffic_buckets`, `connection_events`, `audit_log`,
`alerts`, `share_links`, `saved_filters`, `saved_views`, `settings`, `network_devices` (реестр, сведения, интерфейсы,
соседи, устройства контроллеров в JSONB), `endpoint_sightings` (где видели каждый MAC), `network_endpoints`
(объединённые клиенты сети), `flow_rules`. Схема создаётся при старте. Учётные данные устройств хранятся в OpenBao
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
