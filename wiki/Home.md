# Harbor

**English** · [Русский](ru-Home.md)

**Harbor** manages a home or corporate network: devices of 25+ platforms (MikroTik, TP-Link / Omada, UniFi,
Keenetic, OpenWrt, OPNsense, pfSense, Cisco, HP / Aruba, Dell, Zyxel, Juniper, Huawei, FortiGate, Eltex, SNMP), a live
network map with every client, flow analytics with detection rules and threat intelligence — together with a
self-hosted AmneziaWG VPN and selective routing (sites from a list go through the tunnel, the rest goes through the ISP).

## Contents

| Page | About |
|---|---|
| [Architecture](Architecture.md) | Components, data flow, multi-process mode, data storage |
| [Installation](Installation.md) | Installing the gateway and the panel, initial setup |
| [MikroTik](MikroTik.md) | Policy routing, fallback route, NetFlow, rollback |
| [Configuration](Configuration.md) | The setup wizard, settings, sign-in modes |
| [Clients](Clients.md) | VPN client lifecycle, quotas, expiry, config delivery |
| [MikroTik-VPN](MikroTik-VPN.md) | WireGuard server on MikroTik: setup, clients, configs |
| [OPNsense-VPN](OPNsense-VPN.md) | WireGuard instance on OPNsense: API key, clients, configs |
| [Monitoring](Monitoring.md) | Dashboard, analytics, map, alerts, notifications, Telegram |
| [Devices](Devices.md) | Supported platforms, adding devices, polling, settings from the panel, network clients |
| [Network-Map](Network-Map.md) | The live network map |
| [Flows](Flows.md) | Flow analytics: exporters, dimensions, filter language, aggregates, detection rules, threats |
| [Routing](Routing.md) | Site list, custom prefixes, route check |
| [Security](Security.md) | Sign-in through SPA, roles, sessions, API tokens, OpenBao, audit |
| [Backups](Backups.md) | Backup and restore |
| [API](API.md) | REST API, token authentication, SSE |
| [Troubleshooting](Troubleshooting.md) | Diagnostics and common issues |
| [Development](Development.md) | Code structure, principles, local run |

## Overview

```
 LAN devices ──► MikroTik ──(dst ∈ vpn-sites)──► gateway (LXC/VM with Docker) ──awg0──► Amnezia server (VPS)
                    │  └──(everything else)──► ISP
                    └── NetFlow / IPFIX / sFlow (any device) ──► flow-collector ──► ClickHouse
 browser ──► Harbor (nginx → FastAPI) ──► PostgreSQL · OpenBao · ClickHouse · SSH to VPS and MikroTik
```

## Key features

- AmneziaWG client management: create, block, expiry, quotas, key reissue, bulk operations.
- QR / `.conf` / `vpn://` and one-time links for handing out access.
- Live monitoring, traffic analytics, connection map on OpenStreetMap.
- Network devices: inventory, interfaces, neighbours, clients, settings, CLI, backups; discovery.
- A network map built from LLDP / CDP / MNDP, controllers, switch ports and Wi-Fi, with client traffic.
- Flow analytics in the style of Akvorado / Umbrella Flow: exporters and interfaces, applications, aggregates,
  DDoS / scan / anomaly rules, threat intelligence, reverse DNS, saved views.
- Toast notifications and a notification center, alerts and Telegram notifications, audit log, backups.
- Sign-in and access managed in SPA (Single Point of Authorization), RBAC, API tokens, secrets in OpenBao.
