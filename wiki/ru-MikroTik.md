# Настройка MikroTik

Файл `mikrotik/network-control.rsc` настраивает policy routing и экспорт NetFlow. Отредактируйте переменные в начале
(`gateway`, `tunnelProbe`, `lanIface`, `wanIface`, `flowPort`), загрузите файл на роутер и импортируйте:

```
/export file=before-amnezia
/system backup save name=before-amnezia
/import file-name=network-control.rsc
```

## Что создаётся

| Объект | Назначение |
|---|---|
| Таблица `to-vpn` | Отдельная таблица маршрутизации для трафика к сайтам из списка |
| Маршрут `10.8.1.0/32` → шлюз | Проверка туннеля: адрес сервера внутри туннеля доступен только через шлюз |
| `0.0.0.0/0` → `10.8.1.0` в `to-vpn` | Рекурсивный маршрут с `check-gateway=ping` — активен, только пока туннель жив |
| Резервный `0.0.0.0/0` → провайдер (distance 10) | При падении туннеля сайты идут напрямую, а не пропадают |
| Скрипт DHCP-клиента WAN | Обновляет шлюз резервного маршрута при смене адреса провайдера |
| Правило `routing-mark=to-vpn` | Поиск в таблице `to-vpn` |
| Mangle (2 правила) | Помечает новые соединения к `vpn-sites` и направляет их в `to-vpn` |
| NAT masquerade | Ответы из туннеля возвращаются через роутер (симметричный conntrack) |
| Fasttrack `connection-mark=no-mark` | Помеченные соединения не уходят в fasttrack |
| `send-redirects=no` | Роутер не отправляет ICMP redirect на шлюз |
| Traffic Flow (NetFlow v9) | Экспорт потоков на коллектор, порт 2055/udp |

Address-list `vpn-sites` заполняет панель (раздел «Маршрутизация»), применяя изменения диффом.

## Как классифицируется трафик в потоках

- Источник в LAN → `Direction = out`; `Route = vpn`, если next-hop — шлюз.
- Источник вне LAN → `Direction = in`; `Route = vpn`, если получатель — адрес роутера в LAN (masquerade), иначе `direct`.
- Внутренний трафик LAN и транспорт туннеля к VPS исключаются.

## Нагрузка

Fasttrack сохраняется для прямого трафика. Помеченные VPN-соединения проходят медленным путём.
Traffic Flow добавляет нагрузку на CPU; на слабых коммутаторах (CRS3xx) следите за `cpu-load`.

## Откат

```
/import file-name=network-control-remove.rsc
```

или `/system backup load name=before-amnezia`.
