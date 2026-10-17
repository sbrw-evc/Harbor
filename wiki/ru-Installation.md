# Установка

## Требования

- Хост в домашней сети для панели и шлюза: LXC-контейнер (Proxmox) или ВМ с Docker 24+ и Compose v2; 2–3 ГБ ОЗУ,
  10+ ГБ диска. Для LXC: непривилегированный, `nesting=1`, проброшен `/dev/net/tun`.
- MikroTik с RouterOS 7.
- По желанию: собственный сервер Amnezia с контейнером AmneziaWG (`amnezia-awg2`, протокол AmneziaWG 2.x/3.x).
- По желанию: Keystone (SPA) для единого входа; без него у панели свои пользователи.

### LXC на Proxmox

```bash
pct create 302 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst --hostname network-control \
  --cores 2 --memory 3072 --swap 1024 --rootfs local-lvm:20 \
  --net0 name=eth0,bridge=vmbr0,gw=192.168.0.1,ip=192.168.0.102/24,type=veth \
  --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1 --dev0 /dev/net/tun
pct start 302
pct exec 302 -- bash -c "apt-get update && apt-get install -y curl git && curl -fsSL https://get.docker.com | sh"
```

Включите пересылку пакетов внутри контейнера (шлюз маршрутизирует туннель):

```bash
cat >/etc/sysctl.d/99-gw.conf <<EOF
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
EOF
sysctl -p /etc/sysctl.d/99-gw.conf
```

## Установка панели

```bash
git clone https://github.com/sbrw-evc/Network-Control.git /opt/network-control
cd /opt/network-control
./install.sh
```

`install.sh` создаёт `.env` со случайными паролями встроенных PostgreSQL и ClickHouse, папки данных и ключ
распечатывания OpenBao, запускает стек и печатает:

- адрес панели,
- **код установки** (он же есть в `docker compose logs backend`),
- пароли PostgreSQL и ClickHouse для шага «Хранилища».

Откройте адрес и пройдите **мастер первичной настройки** (см. [ru-Configuration](ru-Configuration)): хранилища, затем вход (локальный
администратор или Keystone). Сохраните root-токен и ключ восстановления OpenBao, которые мастер показывает один раз.

Дальше чек-лист **«Первые шаги»** на обзоре проводит по остальному:

1. **«Настройки → Роутер»**: адрес MikroTik. Добавьте на роутер SSH-ключ панели из карточки **«Доступ и хранилища»**
   (там же готовая команда) и нажмите **«Проверить роутер»**. См. [ru-MikroTik](ru-MikroTik).
2. **«Настройки → Сервер Amnezia»**: адрес VPS; добавьте тот же ключ в `/root/.ssh/authorized_keys` на нём.
3. **«Настройки → Шлюз и локальная сеть»**: адрес этого хоста, затем **«Создать клиента шлюза»**. Панель запишет
   `awg0.conf`, и туннель поднимется сам.
4. **«Маршрутизация → Загрузить ip-list.json»**: список сайтов применяется на роутере и загружается в ClickHouse.
5. **«Настройки → Telegram-бот»**: по желанию подключите уведомления.

Образы берутся из GitHub Container Registry, версия задаётся `NC_VERSION` в `.env`:

| Образ | Платформы |
|---|---|
| `ghcr.io/sbrw-evc/network-control-backend` | linux/amd64, linux/arm64 |
| `ghcr.io/sbrw-evc/network-control-frontend` | linux/amd64, linux/arm64 |
| `ghcr.io/sbrw-evc/network-control-flow-collector` | linux/amd64, linux/arm64 |
| `ghcr.io/sbrw-evc/network-control-awg-client` | linux/amd64 |

## Обновление

```bash
cd /opt/network-control
git pull
sed -i 's/^NC_VERSION=.*/NC_VERSION=2.1.0/' .env
./install.sh
```

Схемы PostgreSQL и ClickHouse обновляются автоматически.

### С версии до мастера первичной настройки

Вручную ничего делать не нужно: backend один раз перенимает установку. Он читает старый `.env` (примонтирован только
для чтения) и файлы AppRole бывшего сервиса `bao-init`, переносит пароли и ключ подключения SPA в OpenBao, превращает
`AWG_HOST`, `ROUTER_HOST`, `GATEWAY_IP`, `LAN_CIDR`, `PUBLIC_BASE_URL` и остальные переменные в настройки и записывает
`data/backend/bootstrap.json`. Установка, где вход шёл через SPA, продолжит работать так же. В журнале будет список
перенесённого; после этого в `.env` нужно оставить только пароли, версию и порты. PostgreSQL переходит на образ
TimescaleDB с той же папкой данных.

## Что хранить отдельно

- `openbao/keys/unseal.key`: ключ автоматического распечатывания OpenBao. Без него данные и снимки OpenBao не
  восстановить.
- Root-токен и ключ восстановления OpenBao, которые показал мастер (у старых установок: `data/bao-init/init.json`).
- `data/backend/bootstrap.json` и `data/backend/approle/`: как панель подключается к хранилищам.
- `.env`.
