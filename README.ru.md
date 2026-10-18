# Harbor

[English](README.md) · **Русский**

*Ранее Network Control.* Harbor собирает в одном месте сеть одного дома или небольшого офиса: шлюз с VPN и выборочной
маршрутизацией, устройства, клиентов и трафик. Работает самостоятельно (со своими пользователями) или со входом через
[Keystone](https://onedev.evcsbrw.ru/Monitoring/Keystone), рядом с приложениями семейства: Ledger (инвентаризация), Beacon
(инциденты) и Current (сеть организации).

Панель управления домашней или корпоративной сетью: **сетевые устройства** 25+ платформ с настройкой из панели,
**живая карта сети** вплоть до каждого телефона и телевизора, **анализ потоков** (NetFlow / IPFIX / sFlow → ClickHouse)
с правилами обнаружения и списками угроз — вместе с self-hosted VPN на **AmneziaWG** и выборочной маршрутизацией
(сайты из списка — через туннель, остальное — напрямую через провайдера), алертами в Telegram, резервным копированием
и хранением секретов в OpenBao.

![stack](https://img.shields.io/badge/backend-FastAPI-009688) ![stack](https://img.shields.io/badge/frontend-React%20%2B%20Vite-61dafb)
![stack](https://img.shields.io/badge/db-PostgreSQL%2016-336791) ![stack](https://img.shields.io/badge/flows-ClickHouse-ffcc01)
![stack](https://img.shields.io/badge/secrets-OpenBao-5c4ee5) ![stack](https://img.shields.io/badge/VPN-AmneziaWG%203.x-orange)

## Скриншоты

Обзор с чек-листом **«Первые шаги»** — в светлой и тёмной теме:

| Светлая | Тёмная |
|---|---|
| <img src="docs/screens/overview-light.png" alt="Обзор, светлая тема"> | <img src="docs/screens/overview-dark.png" alt="Обзор, тёмная тема"> |

Остальные страницы показаны в теме вашего GitHub (обе версии — в [`docs/screens`](docs/screens)):

| | |
|---|---|
| **Сетевые устройства**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/devices-dark.png"><img src="docs/screens/devices-light.png" alt="Сетевые устройства"></picture> | **Карта сети**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/network-map-dark.png"><img src="docs/screens/network-map-light.png" alt="Карта сети"></picture> |
| **Анализ потоков**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/flows-dark.png"><img src="docs/screens/flows-light.png" alt="Анализ потоков"></picture> | **Клиенты VPN**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/vpn-clients-dark.png"><img src="docs/screens/vpn-clients-light.png" alt="Клиенты VPN"></picture> |
| **Вход через SPA**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/sign-in-dark.png"><img src="docs/screens/sign-in-light.png" alt="Вход через SPA"></picture> | |

Скриншоты сняты с настоящего приложения на демо-данных: `./scripts/screenshots.sh` (см. [Разработку](#разработка)). Интерфейс на
скриншотах английский; русский включается в меню пользователя.

## Возможности

- **Сетевые устройства** — MikroTik, TP-Link / Omada, UniFi, Keenetic, OpenWrt, OPNsense, pfSense, Cisco, HP / Aruba,
  Dell, Zyxel, Juniper, Huawei, FortiGate, Eltex, серверы Redfish и любые SNMP-устройства: добавление и удаление,
  опрос сведений, интерфейсов со скоростью, соседей LLDP / CDP / MNDP и клиентов; смена имени, интерфейсов, DNS, NTP,
  экспорт потоков, перезагрузка, консоль CLI и резервные копии конфигурации из панели; поиск устройств в подсети.
- **Клиенты сети** — все телефоны, ноутбуки, ТВ и камеры, объединённые из ARP, DHCP, Wi-Fi, контроллеров и портов
  коммутаторов, с производителем по MAC, именами и блокировкой на контроллере.
- **Карта сети** — живой граф по данным устройств: каналы с загрузкой, контроллеры и их точки доступа, клиенты по
  портам и Wi-Fi, трафик каждого клиента, обнаруженные соседи, интернет и VPN.
- **Клиенты VPN** — создание, срок доступа, месячная квота, теги, владелец; блокировка вручную и автоматически
  (срок, квота), перевыпуск ключей, массовые действия, экспорт CSV.
- **Выдача конфигов** — QR и `.conf` для AmneziaWG, ключ `vpn://` для Amnezia VPN, одноразовая ссылка с ограничением по времени.
- **VPN на MikroTik** — WireGuard-сервер на домашнем роутере под управлением панели наравне с Amnezia: настройка в один клик,
  тот же жизненный цикл клиентов, квоты, трафик и конфиги; клиентам доступна домашняя сеть, сайты из списка — через Amnezia.
- **Мониторинг** — live-скорость (SSE), трафик по периодам, топ клиентов, тепловая карта активности, страны.
- **Карта подключений** — OpenStreetMap (собственный кэширующий прокси тайлов), геолокация DB-IP Lite.
- **Анализ потоков** — NetFlow v5 / v9, IPFIX и sFlow с любых устройств в ClickHouse с 5-минутными и часовыми
  агрегатами (до 2 лет); экспортёры и интерфейсы, приложения, имена клиентов, обратный DNS; визуализация
  (stacked / 100% / lines / grid / sankey), топ по среднему / пикам / последним, двунаправленный режим, сравнение с
  прошлым периодом, язык фильтров, сохранённые фильтры и представления, состояние в URL.
- **Правила и угрозы** — пороги, объёмные и SYN DDoS, сканирование портов и хостов, аномалии и падения относительно
  недельной нормы, молчащие экспортёры, загрузка каналов, контакты со списками FireHOL / Spamhaus / abuse.ch → алерты
  со ссылкой на трафик.
- **Маршрутизация** — статус туннеля на MikroTik, загрузка списка сайтов (`ip-list.json`), свои домены и подсети,
  проверка «куда пойдёт трафик», автоматический переход на провайдера при падении туннеля.
- **Сервер AWG** — параметры обфускации, редактор `awg0.conf` с резервными копиями и применением без разрыва соединений.
- **Безопасность** — вход и доступ управляются в [SPA — единой точке авторизации](https://onedev.evcsbrw.ru/Monitoring/Keystone) (пароли, ключи доступа, MFA,
  каталоги — там), роли (администратор / оператор / наблюдатель) назначаются в SPA, сессии с отзывом,
  API-токены, журнал аудита, секреты только в OpenBao.
- **Первые шаги и пошаговая настройка** — на обзоре чек-лист от установки до работающей панели (подключение SPA,
  адрес приложения, первое устройство, приём потоков, VPN-клиент, уведомления в Telegram, резервные копии), а на
  страницах устройств, анализа потоков, VPN-клиентов, маршрутизации и настроек — пошаговые руководства; каждый шаг
  проверяется по реальным данным и отмечается сам, когда выполнен.
- **Уведомления** — всплывающие уведомления (до 5 одновременно) для всех событий приложения и центр уведомлений с историей.
- **Эксплуатация** — алерты (VPS, шлюз, туннель, OpenBao, квоты, сроки) с уведомлениями в Telegram,
  ежедневные резервные копии (PostgreSQL + снапшот OpenBao), несколько процессов API с выбором лидера.

## Архитектура

```
 LAN ──► MikroTik ──(dst ∈ vpn-sites)──► шлюз (Docker-хост) ──awg0──► сервер Amnezia (VPS)
            │  └─(остальное)──► провайдер
 маршрутизаторы, коммутаторы, МСЭ ── NetFlow / IPFIX / sFlow ──► flow-collector ──► ClickHouse
            ▲ REST / RCI / SSH CLI / SNMP                                            ▲
 браузер ──► frontend (nginx) ──► backend (FastAPI ×N) ──► PostgreSQL · OpenBao · SSH (VPS)
```

| Сервис | Назначение |
|---|---|
| `awg-client` | AmneziaWG-клиент шлюза: policy routing LAN → `awg0`, NAT, MSS clamp |
| `backend` | REST API, SSE, драйверы устройств, фоновые задачи (опрос VPN и устройств, политики, правила, здоровье, бэкапы) |
| `frontend` | веб-интерфейс (React) + прокси API и тайлов OpenStreetMap |
| `postgres` | клиенты, статистика, аудит, алерты, пользователи |
| `openbao` | секреты (пароли локальных пользователей и их TOTP, ключи клиентов, SSH-ключ, токен Telegram, пароли баз, ключ подключения к Keystone), auto-unseal; инициализируется мастером |
| `clickhouse` | сетевые потоки и агрегаты; словари сайтов, угроз, экспортёров, интерфейсов и хостов |
| `flow-collector` | goflow2 (NetFlow / IPFIX UDP 2055, sFlow UDP 6343) + обогащение (страна, ASN, направление, маршрут, экспортёр) |

Подробности — в [Wiki](https://onedev.evcsbrw.ru/Monitoring/Harbor/~wiki/main/ru-Home).

## Поддерживаемые устройства

| Сегмент | Платформы |
|---|---|
| Дом | MikroTik, Keenetic, OpenWrt (GL.iNet, Turris, Cudy…), TP-Link Archer / Deco (SNMP), ASUS Merlin, Synology (SSH / SNMP) |
| Малый бизнес | контроллер TP-Link Omada и коммутаторы JetStream, Ubiquiti UniFi и EdgeRouter / VyOS, OPNsense, pfSense, Zyxel, Dell PowerSwitch N, Netgear, D-Link |
| Корпоративный | Cisco IOS / IOS-XE / NX-OS и Meraki, HP ProCurve / HPE Aruba (ArubaOS-Switch, AOS-CX), HPE Comware / H3C, Dell OS10, Juniper Junos, Huawei VRP, Fortinet FortiGate, Eltex, серверы через Redfish (iDRAC, iLO, XCC, Supermicro) |
| Всё остальное | SNMP v1 / v2c / v3 со стандартными MIB |

Что собирается и что можно менять на каждой платформе — [Wiki → Сетевые устройства](https://onedev.evcsbrw.ru/Monitoring/Harbor/~wiki/main/ru-Devices).

## Быстрый старт

Требования: Docker 24+ с Compose v2 и MikroTik с RouterOS 7; по желанию VPS с Amnezia и Keystone (SPA).

```bash
git clone https://onedev.evcsbrw.ru/Monitoring/Harbor /opt/network-control
cd /opt/network-control
./install.sh              # создаст пароли, поднимет стек, покажет адрес и код установки
```

Всё остальное задаётся в панели: откройте адрес и пройдите **мастер первичной настройки**. Сначала хранилища
(встроенные PostgreSQL с TimescaleDB, ClickHouse и OpenBao уже заполнены, OpenBao мастер инициализирует сам), затем
вход. В `.env` остаются только пароли встроенных баз, версия и порты.

### Два способа входа

- **Пользователи Harbor**: самостоятельная установка. Первый администратор создаётся в мастере; пароли
  (argon2id) и секреты TOTP хранятся в OpenBao и никогда не попадают в PostgreSQL.
- **Keystone**: [SPA / Keystone](https://onedev.evcsbrw.ru/Monitoring/Keystone) (OpenID Connect)
  выполняет вход и назначает роли (`admin`, `operator`, `viewer`), панель синхронизирует людей каждые 5 минут.
  Ключ подключения `spa1.…` вставляется в мастере или в **«Настройки → SPA»** (хранится в OpenBao).

Самостоятельную панель можно позже перевести на Keystone: разрешите в Keystone перенос пользователей для приложения,
нажмите **«Перенести локальных пользователей»** (пароли и роли переносятся), затем **«Перейти на Keystone»**. Переход не
сработает, пока ни у кого там нет роли `admin`, поэтому доступ не потеряется. См.
[Конфигурацию](https://onedev.evcsbrw.ru/Monitoring/Harbor/~wiki/main/ru-Configuration).

После мастера чек-лист **«Первые шаги»** на обзоре проводит по остальному:

1. **«Настройки → Роутер»**: адрес MikroTik; добавьте SSH-ключ, который показывает панель, и проверьте подключение.
2. **«Настройки → Сервер Amnezia»** и **«Шлюз и локальная сеть»**, затем **«Создать клиента шлюза»**: панель сама
   запишет конфигурацию туннеля.
3. Загрузите список сайтов в разделе «Маршрутизация».

Пошагово — [Установка](https://onedev.evcsbrw.ru/Monitoring/Harbor/~wiki/main/ru-Installation).

## Docker-образы

Каждый релиз публикует образы в реестр OneDev (теги `X.Y.Z`, `X.Y`, `latest`); те же теги копируются в GitHub Container Registry как `ghcr.io/sbrw-evc/network-control-*`:

| Образ | Платформы |
|---|---|
| `onedev.evcsbrw.ru/monitoring/harbor/backend` | linux/amd64 |
| `onedev.evcsbrw.ru/monitoring/harbor/frontend` | linux/amd64 |
| `onedev.evcsbrw.ru/monitoring/harbor/flow-collector` | linux/amd64 |
| `onedev.evcsbrw.ru/monitoring/harbor/awg-client` | linux/amd64 |

Версия задаётся `NC_VERSION` в `.env`; `install.sh` скачивает образы, `docker compose up -d --build` собирает их локально.
На странице релиза также есть `harbor-X.Y.Z-deploy.tar.gz` — всё необходимое для запуска без клонирования репозитория.

## Разработка

```bash
cd frontend && npm install && VITE_API_TARGET=http://<host>:8080 npm run dev
```

Демо-данные и скриншоты: `backend/demo/run.py` запускает настоящий backend на базе PostgreSQL, которую он очищает
и заполняет демо-данными; SPA, ClickHouse, OpenBao, VPN-сервер, роутер и драйверы устройств заменены заглушками
(встроенный OpenID Connect-провайдер, встраиваемый chdb, имитации с растущими счётчиками), а вход проходит по
настоящему протоколу SPA от имени демо-администратора. `./scripts/screenshots.sh` собирает веб-приложение, запускает
демо и снимает `docs/screens/*.png` через Playwright в обеих темах:

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
DEMO_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/nc_demo ./scripts/screenshots.sh
```

Структура, принципы и API — в [Wiki → Разработка](https://onedev.evcsbrw.ru/Monitoring/Harbor/~wiki/main/ru-Development).
Интерактивная документация API доступна на `/api/docs`.

## Совместимость после переименования

Идентификаторы в протоколах и на диске сохраняют прежнее написание, поэтому существующие установки и подключения к
Keystone продолжают работать:

| Осталось | Где |
|---|---|
| `network-control` | тип приложения в Keystone, проект compose, имена образов `ghcr.io/sbrw-evc/network-control-*`, репозиторий |
| `nc-*`, `nc_*` | имена контейнеров, cookie сессии `nc_session`, ключи хранилища браузера |
| `X-Requested-With: network-control` | проверка CSRF в API |
| `amnezia` | база данных и пользователь PostgreSQL, хранилище KV в OpenBao |

## Лицензия

MIT
