# Разработка

## Структура

```
backend/app/
  domain/        сущности-значения и ошибки
  ports.py       абстракции внешних систем
  adapters/      Amnezia (SSH), MikroTik (SSH), OpenBao, ClickHouse, DB-IP, Telegram, IEEE OUI
  adapters/devices/  драйверы устройств: REST / RCI / Open API, профили SSH CLI и парсеры, SNMP, поиск
  db/            модели SQLAlchemy, Database, репозитории
  services/      бизнес-логика
  api/           роутеры FastAPI, схемы, зависимости
  cluster.py     шина событий, выбор лидера, распределённые блокировки
  workers.py     планировщик периодических задач
  container.py   composition root
  cli.py         резервное копирование и восстановление
frontend/src/
  lib/           HttpClient и ресурсные API, SSE, уведомления, форматирование, хуки
  components/    UI-примитивы, графики, карта, макет, клиенты, администрирование
  pages/         страницы SPA
flow-collector/  goflow2 + обогащение потоков
awg-client/      образ клиента AmneziaWG для шлюза
openbao/         конфигурация и bootstrap
clickhouse/      ограничения ресурсов
mikrotik/        скрипты настройки и отката роутера
```

## Принципы

- **SOLID**: сервисы зависят от абстракций в `ports.py`; конкретные адаптеры создаются только в `container.py`.
  Новый тип роутера или хранилища секретов — новый адаптер без изменения сервисов.
- **DRY**: общий `Repository[T]`, единый `ServerConfigEditor` для всех изменений конфига сервера,
  `useAction` (мутация + уведомление + инвалидация), `notify` для всех сообщений и `Resource` на фронтенде.
- Сервер AmneziaWG — источник истины для пиров; PostgreSQL хранит метаданные и статистику; ключи — только в OpenBao.
- Изменения сначала применяются к серверу, затем к БД; расхождения устраняет синхронизация.
- Асинхронный ввод-вывод везде; CPU-ёмкое — в пуле потоков.

## Локальный запуск фронтенда

```bash
cd frontend
npm install
VITE_API_TARGET=http://<host>:8080 npm run dev
```

## Сборка

```bash
docker compose build backend frontend flow-collector
docker compose up -d
```

Сборка фронтенда выполняет проверку типов (`tsc -b`).

## Добавление измерения потоков

1. Добавьте `Field` в `backend/app/services/flowfilter.py` (имя, тип, колонка, подпись, SQL-выражение измерения).
2. При необходимости — колонку в `FlowSchema` и заполнение в `flow-collector/collector.py`.
3. Фронтенд получает список полей из `/api/flows/fields` автоматически.

## Добавление платформы устройств

1. Напишите драйвер в `backend/app/adapters/devices/` (или `CliProfile` в `cli.py` для CLI, похожего на Cisco или
   Comware): `collect()` возвращает `Snapshot` (сведения, интерфейсы, соседи, клиенты, дочерние устройства), `apply()`
   выполняет действия из `capabilities`, `backup()` возвращает текст конфигурации.
2. Зарегистрируйте его в `PLATFORMS` (`adapters/devices/__init__.py`) с подключениями, портами и полями учётных данных —
   форма, матрица возможностей и API берут их оттуда.
3. Добавьте тесты парсеров с реальным выводом в `backend/tests/test_drivers.py`.

## Тесты

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/test python -m pytest -q tests
```

Тесты выполняют настоящий SQL ClickHouse (схема, миграции, агрегаты, визуализация, правила) на встроенном движке
`chdb`, сервис устройств и карту сети — на PostgreSQL (пропускаются без `TEST_DATABASE_URL`), CLI-драйверы — через
настоящую SSH-сессию с эмулятором Cisco IOS, HTTP-драйверы — на подменённых API.
