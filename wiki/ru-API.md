# API

Интерактивная документация OpenAPI: `/api/docs` (схема — `/api/openapi.json`).

## Авторизация

- **Браузер** — вход через SPA (`GET /api/auth/spa/start?return=…` → SPA → `GET /api/auth/spa/callback`), затем
  cookie сессии + заголовок `X-Requested-With: network-control` для изменяющих запросов.
- **Интеграции** — API-токен: `Authorization: Bearer ac_…` (роль задаётся при выпуске; не выше роли владельца,
  токен перестаёт работать, когда SPA отключает владельца).

```bash
curl -H "Authorization: Bearer $TOKEN" http://<panel>/api/analytics/summary
```

## Основные эндпоинты

| Метод | Путь | Роль | Описание |
|---|---|---|---|
| GET | `/api/clients` | наблюдатель | Список клиентов (`q`, `status`, `tag`) |
| POST | `/api/clients` | оператор | Создать клиента |
| GET/PATCH/DELETE | `/api/clients/{id}` | наблюдатель / оператор | Карточка, изменение, удаление |
| POST | `/api/clients/{id}/block` · `/unblock` · `/reissue` | оператор | Блокировка, разблокировка, перевыпуск |
| GET | `/api/clients/{id}/config` · `/config.conf` | оператор | Конфиг (QR, `.conf`, `vpn://`) |
| POST | `/api/clients/{id}/share` | оператор | Одноразовая ссылка |
| POST | `/api/clients/bulk` · `/sync` | оператор | Массовые действия, синхронизация |
| GET | `/api/analytics/summary` · `/live` · `/traffic` · `/top` · `/heatmap` · `/geo` | наблюдатель | Аналитика |
| GET | `/api/flows/status` · `/overview` · `/fields` | наблюдатель | Потоки: статус и обзор |
| POST | `/api/flows/visualize` · `/recent` | наблюдатель | Визуализация и последние потоки |
| GET/POST/DELETE | `/api/flows/filters` | наблюдатель | Сохранённые фильтры |
| GET | `/api/routing/status` · `/custom` | наблюдатель | Маршрутизация |
| POST | `/api/routing/check` | наблюдатель | Куда пойдёт трафик |
| POST | `/api/routing/site-list` · `/custom` | администратор | Загрузка списка, свои подсети |
| GET/PUT | `/api/server/config` | администратор | Конфиг сервера AWG |
| GET/POST/DELETE | `/api/backups` | администратор | Резервные копии |
| GET/POST/DELETE | `/api/tokens` | администратор | API-токены |
| GET | `/api/users` | администратор | Пользователи (только чтение, синхронизируются из SPA) |
| GET · POST | `/api/spa` · `/api/spa/sync` | администратор | Состояние подключения к SPA; синхронизировать сейчас |
| GET · POST | `/api/auth/config` · `/api/auth/logout` | — / сессия | Подключение к SPA для страницы входа; выход (возвращает адрес выхода в SPA) |
| GET/PUT | `/api/settings` | наблюдатель / администратор | Настройки |
| GET | `/api/alerts` · `/api/audit` | наблюдатель / администратор | Алерты, аудит |
| GET | `/api/events` | наблюдатель | Server-Sent Events |
| GET | `/api/devices/platforms` | наблюдатель | Платформы, подключения, учётные данные и возможности |
| GET/POST | `/api/devices` | наблюдатель / администратор | Список устройств, добавление (`credentials` уходят в OpenBao) |
| GET/PATCH/DELETE | `/api/devices/{id}` | наблюдатель / администратор | Подробности с интерфейсами, соседями, дочерними устройствами и клиентами; изменение; удаление |
| POST | `/api/devices/test` · `/discover` | администратор | Проверка параметров без сохранения; поиск по адресу или подсети |
| POST | `/api/devices/{id}/poll` | оператор | Опросить сейчас |
| POST | `/api/devices/{id}/actions` | оператор / администратор | `{"action": "hostname|interface|dns|ntp|flow_export|reboot|endpoint_block|command", "params": {…}}` |
| GET | `/api/devices/{id}/backup` | администратор | Текущая конфигурация текстом |
| GET | `/api/endpoints` | наблюдатель | Клиенты сети (`q`, `online`, `device_id`) |
| PATCH/DELETE | `/api/endpoints/{mac}` · POST `/block` | оператор | Имя и заметка, удаление, блокировка на контроллере |
| GET · PUT | `/api/topology` · `/api/topology/positions` | наблюдатель · оператор | Граф карты сети; сохранённые позиции устройств |
| GET | `/api/flows/exporters` | наблюдатель | Экспортёры и их интерфейсы |
| GET/POST/PATCH/DELETE | `/api/flows/rules` · POST `/rules/{id}/test` | наблюдатель / администратор | Правила обнаружения |
| GET · POST | `/api/flows/threats` · `/threats/refresh` | наблюдатель · администратор | Контакты с угрозами и списки |
| GET/POST/DELETE | `/api/flows/views` | наблюдатель | Сохранённые представления |

### Пример: визуализация потоков

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://<panel>/api/flows/visualize \
  -d '{"dimensions":["Site","Route"],"limit":10,"graph":"stacked","unit":"bps","filter":"Direction = out"}'
```

## События (SSE)

`GET /api/events` — поток `text/event-stream`:

| Событие | Данные |
|---|---|
| `live` | `t`, `rx`, `tx` (байт/с), `online`, `clients` — скорости по клиентам |
| `clients` | Изменение клиента (`id`) или синхронизация |
| `alert` | Новый или решённый алерт |
| `audit` | Новая запись аудита |
| `connection` | Новое подключение клиента (город, страна) |
| `devices` | Устройство добавлено, изменено, удалено или опрошено (`id`, `action`, `status`) |

## Ошибки

Ответ ошибки: `{"detail": "текст"}`. Коды: 400 — неверные данные, 401 — нужен вход, 403 — нет прав или CSRF,
404 — не найдено, 409 — конфликт, 422 — валидация, 502 — недоступен VPS, MikroTik, OpenBao или ClickHouse.
