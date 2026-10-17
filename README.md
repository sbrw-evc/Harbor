# Harbor

**English** · [Русский](README.ru.md)

*Formerly Network Control.* Harbor is the network of one home or small office in one place: the gateway with its
VPN and selective routing, the devices, the clients and the traffic. It stands on its own (its own users) or signs
people in through [Keystone](https://github.com/sbrw-evc/Keystone), next to the applications of the family —
Ledger (inventory), Beacon (incidents) and Current (the network of an organisation).

A control panel for a home or corporate network: **network devices** of 25+ platforms with settings from the panel,
a **live network map** down to every phone and TV, **flow analytics** (NetFlow / IPFIX / sFlow → ClickHouse) with
detection rules and threat intelligence — together with a self-hosted **AmneziaWG** VPN and selective routing
(sites from a list go through the tunnel, everything else goes directly through your ISP), Telegram alerts, backups
and secret storage in OpenBao.

![stack](https://img.shields.io/badge/backend-FastAPI-009688) ![stack](https://img.shields.io/badge/frontend-React%20%2B%20Vite-61dafb)
![stack](https://img.shields.io/badge/db-PostgreSQL%2016-336791) ![stack](https://img.shields.io/badge/flows-ClickHouse-ffcc01)
![stack](https://img.shields.io/badge/secrets-OpenBao-5c4ee5) ![stack](https://img.shields.io/badge/VPN-AmneziaWG%203.x-orange)

## Screenshots

The overview with the **First steps** checklist, in the light and the dark theme:

| Light | Dark |
|---|---|
| <img src="docs/screens/overview-light.png" alt="Overview, light theme"> | <img src="docs/screens/overview-dark.png" alt="Overview, dark theme"> |

The other pages follow the theme of your GitHub (both versions are in [`docs/screens`](docs/screens)):

| | |
|---|---|
| **Network devices**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/devices-dark.png"><img src="docs/screens/devices-light.png" alt="Network devices"></picture> | **Network map**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/network-map-dark.png"><img src="docs/screens/network-map-light.png" alt="Network map"></picture> |
| **Flow analysis**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/flows-dark.png"><img src="docs/screens/flows-light.png" alt="Flow analysis"></picture> | **VPN clients**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/vpn-clients-dark.png"><img src="docs/screens/vpn-clients-light.png" alt="VPN clients"></picture> |
| **Sign-in through SPA**<br><picture><source media="(prefers-color-scheme: dark)" srcset="docs/screens/sign-in-dark.png"><img src="docs/screens/sign-in-light.png" alt="Sign-in through SPA"></picture> | |

The screenshots are taken from the real application with demo data: `./scripts/screenshots.sh` (see [Development](#development)).

## Features

- **Network devices** — MikroTik, TP-Link / Omada, UniFi, Keenetic, OpenWrt, OPNsense, pfSense, Cisco, HP / Aruba, Dell,
  Zyxel, Juniper, Huawei, FortiGate, Eltex, Redfish servers and any SNMP device: add, remove, poll facts, interfaces
  with live rates, LLDP / CDP / MNDP neighbours and clients; change name, interfaces, DNS, NTP, flow export, reboot,
  CLI console and configuration backups from the panel; subnet discovery with platform detection.
- **Network clients** — every phone, laptop, TV and camera merged from ARP, DHCP, Wi-Fi, controllers and switch ports,
  with vendor by MAC, names and blocking on the controller.
- **Network map** — a live graph built from the devices' data: links with utilisation, controllers and their APs,
  clients by port and Wi-Fi, traffic per client, discovered neighbours, Internet and VPN.
- **VPN clients** — create clients with an access period, monthly quota, tags and owner; block manually or automatically
  (expiry, quota); reissue keys; bulk actions; CSV export.
- **Config delivery** — QR code and `.conf` for AmneziaWG, a `vpn://` key for Amnezia VPN, one-time links with an expiry.
- **VPN on MikroTik** — a WireGuard server on the home router managed alongside Amnezia: one-click setup, the same client
  lifecycle, quotas, traffic and configs; clients reach the home network, sites from the list go through Amnezia.
- **Monitoring** — live throughput (SSE), traffic per period, top clients, activity heatmap, countries.
- **Connection map** — OpenStreetMap (served through the panel's own caching tile proxy), DB-IP Lite geolocation.
- **Flow analytics** — NetFlow v5 / v9, IPFIX and sFlow from any device into ClickHouse with 5-minute and hourly
  aggregates (up to 2 years); exporters and interfaces, applications, client names, reverse DNS; visualization
  (stacked / 100% / lines / grid / sankey), top by average / peak / last, bidirectional mode, previous-period
  comparison, a filter language, saved filters and views, state in the URL.
- **Detection rules and threats** — thresholds, volumetric and SYN DDoS, port / host scans, weekly-baseline anomalies
  and drops, silent exporters, link utilisation, contacts with FireHOL / Spamhaus / abuse.ch blocklists → alerts with
  a link to the traffic.
- **Routing** — tunnel status on MikroTik, site list upload (`ip-list.json`), custom domains and prefixes,
  "where will this traffic go" check, automatic fallback to the ISP when the tunnel is down.
- **AWG server** — obfuscation parameters, an `awg0.conf` editor with backups that applies changes without dropping connections.
- **Security** — sign-in and access managed in [SPA — Single Point of Authorization](https://github.com/sbrw-evc/SPA-Single-point-of-authorization-) (passwords, passkeys, MFA,
  directories live there), roles (administrator / operator / viewer) assigned in SPA, revocable sessions,
  API tokens, audit log, secrets kept only in OpenBao.
- **First steps and setup guides** — the overview shows a checklist from installation to a working panel (SPA
  connection, application address, first device, flow intake, VPN client, Telegram notifications, backups), and the
  devices, flow analysis, VPN clients, routing and settings pages have step-by-step guides; every step is checked
  against real data and ticks itself off when done.
- **Notifications** — toast notifications (up to 5 at once) for every event in the app and a notification center with history.
- **Operations** — alerts (VPS, gateway, tunnel, OpenBao, quotas, expiry) with Telegram notifications,
  daily backups (PostgreSQL + OpenBao snapshot), several API processes with leader election.

## Architecture

```
 LAN ──► MikroTik ──(dst ∈ vpn-sites)──► gateway (Docker host) ──awg0──► Amnezia server (VPS)
            │  └─(everything else)──► ISP
 routers, switches, firewalls ── NetFlow / IPFIX / sFlow ──► flow-collector ──► ClickHouse
            ▲ REST / RCI / SSH CLI / SNMP                                         ▲
 browser ──► frontend (nginx) ──► backend (FastAPI ×N) ──► PostgreSQL · OpenBao · SSH (VPS)
```

| Service | Purpose |
|---|---|
| `awg-client` | The gateway's AmneziaWG client: policy routing LAN → `awg0`, NAT, MSS clamp |
| `backend` | REST API, SSE, device drivers, background jobs (VPN and device polling, policies, detection rules, health checks, backups) |
| `frontend` | Web UI (React) + API proxy + OpenStreetMap tile proxy |
| `postgres` | Clients, statistics, audit, alerts, users |
| `openbao` | Secrets (passwords of local users and their TOTP, client keys, SSH key, Telegram token, database passwords, the Keystone connection key), auto-unseal; initialised by the setup wizard |
| `clickhouse` | Network flows and aggregates; site, threat, exporter, interface and host dictionaries |
| `flow-collector` | goflow2 (NetFlow / IPFIX UDP 2055, sFlow UDP 6343) + enrichment (country, ASN, direction, route, exporter) |

See the [Wiki](https://github.com/sbrw-evc/Network-Control/wiki) for details.

## Supported devices

| Segment | Platforms |
|---|---|
| Home | MikroTik, Keenetic, OpenWrt (GL.iNet, Turris, Cudy…), TP-Link Archer / Deco (SNMP), ASUS Merlin, Synology (SSH / SNMP) |
| SMB | TP-Link Omada controller and JetStream switches, Ubiquiti UniFi and EdgeRouter / VyOS, OPNsense, pfSense, Zyxel, Dell PowerSwitch N, Netgear, D-Link |
| Enterprise | Cisco IOS / IOS-XE / NX-OS and Meraki, HP ProCurve / HPE Aruba (ArubaOS-Switch, AOS-CX), HPE Comware / H3C, Dell OS10, Juniper Junos, Huawei VRP, Fortinet FortiGate, Eltex, servers via Redfish (iDRAC, iLO, XCC, Supermicro) |
| Anything else | SNMP v1 / v2c / v3 with standard MIBs |

What each platform collects and allows to change: [Wiki → Network devices](https://github.com/sbrw-evc/Network-Control/wiki/Devices).

## Quick start

Requirements: Docker 24+ with Compose v2 and MikroTik with RouterOS 7; optionally an Amnezia VPS and Keystone (SPA).

```bash
git clone https://github.com/sbrw-evc/Network-Control.git /opt/network-control
cd /opt/network-control
./install.sh              # generates the passwords, starts the stack, prints the address and the setup code
```

Everything else is set in the panel: open the address and go through the **setup wizard** — the storage (the bundled
PostgreSQL with TimescaleDB, ClickHouse and OpenBao are filled in; OpenBao is initialised by the wizard), then the
sign-in. `.env` keeps only the passwords of the bundled databases, the version and the ports.

### Two ways to sign in

- **Users of Harbor** — a standalone installation: the first administrator is created in the wizard; passwords
  (argon2id) and TOTP secrets are kept in OpenBao, never in PostgreSQL.
- **Keystone** — [SPA / Keystone](https://github.com/sbrw-evc/SPA-Single-point-of-authorization-) (OpenID Connect)
  signs people in and decides their roles (`admin`, `operator`, `viewer`); the panel synchronises them every 5 minutes.
  The connection key `spa1.…` is pasted in the wizard or in **Settings → SPA** (kept in OpenBao).

A standalone panel can move to Keystone later: allow moving users for the application in Keystone, press **Move the
local users** (their passwords and roles go along), then **Switch to Keystone** — refused while nobody has the `admin`
role there, so nobody is locked out. See [Configuration](https://github.com/sbrw-evc/Network-Control/wiki/Configuration).

After the wizard the **First steps** checklist of the overview leads through the rest:

1. **Settings → Router**: the address of the MikroTik; add the SSH key the panel shows and check the connection.
2. **Settings → Amnezia server** and **Gateway and LAN**, then **Create the gateway client** — the panel writes the
   tunnel config itself.
3. Upload the site list in the "Routing" section.

Step by step — [Installation](https://github.com/sbrw-evc/Network-Control/wiki/Installation).

## Docker images

Every release publishes images to GitHub Container Registry (tags `X.Y.Z`, `X.Y`, `latest`):

| Image | Platforms |
|---|---|
| `ghcr.io/sbrw-evc/network-control-backend` | linux/amd64, linux/arm64 |
| `ghcr.io/sbrw-evc/network-control-frontend` | linux/amd64, linux/arm64 |
| `ghcr.io/sbrw-evc/network-control-flow-collector` | linux/amd64, linux/arm64 |
| `ghcr.io/sbrw-evc/network-control-awg-client` | linux/amd64 |

The version is set by `NC_VERSION` in `.env`; `install.sh` pulls the images, `docker compose up -d --build` builds them locally.
The release page also has `harbor-X.Y.Z-deploy.tar.gz` — everything needed to run the stack without cloning the repository.

## Development

```bash
cd frontend && npm install && VITE_API_TARGET=http://<host>:8080 npm run dev
```

Demo data and screenshots: `backend/demo/run.py` starts the real backend against a PostgreSQL database that it empties
and fills with demo data; SPA, ClickHouse, OpenBao, the VPN server, the router and the device drivers are stand-ins
(an in-process OpenID Connect issuer, the embedded chdb, fakes with growing counters), and sign-in goes through the
real SPA flow as the demo administrator. `./scripts/screenshots.sh` builds the web app, starts the demo and takes
`docs/screens/*.png` with Playwright in both themes:

```bash
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
DEMO_DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:5432/nc_demo ./scripts/screenshots.sh
```

Structure, principles and API — see [Wiki → Development](https://github.com/sbrw-evc/Network-Control/wiki/Development).
Interactive API documentation is available at `/api/docs`.

## Compatibility after the rename

Identifiers on the wire and on disk keep the old spelling, so existing installations and Keystone connections keep
working:

| Kept | Where |
|---|---|
| `network-control` | the application type in Keystone, the compose project, the image names `ghcr.io/sbrw-evc/network-control-*`, the repository |
| `nc-*`, `nc_*` | container names, the session cookie `nc_session`, browser storage keys |
| `X-Requested-With: network-control` | the CSRF check of the API |
| `amnezia` | the PostgreSQL database and user, the OpenBao KV mount |

## License

MIT
