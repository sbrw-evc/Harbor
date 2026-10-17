# Configuration

Network Control is configured in the panel: the **setup wizard** on the first start, then the **Settings** page.
Settings are stored in PostgreSQL and picked up by all processes within 15 seconds — no restart.

## `.env`

Only what the bundled containers need before the panel runs. `install.sh` creates the file with random passwords.

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_PASSWORD` | — | Password of the bundled PostgreSQL (TimescaleDB). Entered in the wizard once; the panel keeps it in OpenBao |
| `CLICKHOUSE_PASSWORD` | — | Password of the bundled ClickHouse. Entered in the wizard once; kept in OpenBao |
| `NC_VERSION` | `2.0.0` | Version of the images |
| `PANEL_PORT` | `8080` | Port of the panel |
| `FLOW_PORT` / `SFLOW_PORT` | `2055` / `6343` | UDP ports of the flow collector |
| `OPENBAO_VERSION` / `CLICKHOUSE_VERSION` | `2.6.2` / `24.8` | Image versions of the bundled services |

The backend reads nothing else from the environment. Values of an older `.env` (`AWG_HOST`, `ROUTER_HOST`,
`DATABASE_URL`, `SPA_CONNECTION_KEY`, …) are imported **once**, when the database has none of them yet, and a warning in
the log asks to remove them.

## Setup wizard

Opens by itself while Network Control is not set up. Every step asks for the **setup code**, printed by `install.sh`
and in `docker compose logs backend` (also `data/backend/setup-code`); it disappears when the wizard is done.

1. **Storage** — PostgreSQL (TimescaleDB), ClickHouse and OpenBao. The bundled services are filled in. OpenBao of the
   compose file is initialised by the wizard: the **root token and the recovery key are shown once** — keep them in a
   password manager. An external OpenBao is used with an AppRole (`role_id`, `secret_id`, KV v2 mount).
   The result is `data/backend/bootstrap.json` (addresses and user names, no password) and `data/backend/approle/`
   (the only secret on disk); passwords go to OpenBao (`infra/postgres`, `infra/clickhouse`).
2. **Sign-in** — the address of the panel and how people sign in:
   - **Users of Network Control** — a standalone installation: the first administrator is created here; passwords
     (argon2id) and TOTP secrets are kept in OpenBao (`local/users/<id>`), never in PostgreSQL;
   - **Keystone** — the connection key `spa1.…` (see below); somebody must have the `admin` role of the application.

Then the **First steps** checklist of the overview leads through the rest: the router, the Amnezia server, the gateway,
the first device, flows, a VPN client, Telegram and backups.

## Settings page

| Group | Setting | Description |
|---|---|---|
| Router (MikroTik) | Address, SSH user | Reached over SSH with the key of the panel |
| Amnezia server (VPS) | Address, SSH user, container, interface, utility, config folder | Empty address — no Amnezia server |
| Gateway and LAN | Address of the gateway, home network, LAN interface, tunnel network, port forwards | Written to `gateway.json` for awg-client and `collector.json` for the flow collector |
| WireGuard on MikroTik | Interface, client network, port | See [[MikroTik-VPN]] |
| WireGuard on OPNsense | Address, instance, TLS check | The API key is entered on the VPN on OPNsense page and kept in OpenBao; see [[OPNsense-VPN]] |
| Panel | Address of the panel, address for Keystone redirects, session lifetime, cookies only over HTTPS, flow retention | |
| Access and storage | SSH key of the panel, Check the router / VPS, Create the gateway client | The key pair is created in OpenBao (`infra/ssh`); the public key is shown with the MikroTik command |
| Client configs | DNS for clients, server address in configs, share link lifetime | |
| Defaults | Default quota and access period, warnings | |
| Monitoring | Polling interval, statistics retention, gateway silence, report time zone | |
| Notifications | Enable, minimum severity, Telegram chat id | See [[Monitoring]] |
| Backups | Daily backup, number kept | See [[Backups]] |
| Network and devices | Client offline after, forget inactive clients after, collector address and port | |
| Traffic analysis | Threat lists, refresh, networks never flagged, reverse DNS | See [[Flows]] |

### The gateway

**Create the gateway client** creates the AmneziaWG client `home-gateway` and writes its config to
`awg-client/config/awg0.conf`; awg-client waits for this file and restarts the tunnel whenever it or `gateway.json`
changes.

## Sign-in: local users and Keystone

The **Users** page manages local users in the local mode: create, role, disable, reset the password, turn off TOTP,
delete. Everyone changes the temporary password at the first sign-in and may turn on TOTP in the profile.

### Connecting Keystone (SPA)

1. In Keystone: **Applications → Connect an application**, type `network-control`, the address of the panel. The
   redirect address is `<address>/api/auth/spa/callback`, the address after sign-out `<address>/`.
2. Give the roles `admin`, `operator`, `viewer` to people or groups — yourself `admin` first.
3. Paste the connection key in **Settings → SPA** (it goes to OpenBao `spa/connection_key`).

### Moving local users to Keystone

In the local mode, with the key saved:

1. In Keystone open the application and **allow moving users** (a window that closes after one import or 24 hours).
2. **Move the local users** — they are sent with their argon2id password hashes and roles
   (`POST /api/spa/v1/users/import`); Keystone keeps the passwords (re-hashed at the first sign-in), reports user names
   or e-mails that are taken. TOTP is set up again in Keystone.
3. **Switch to Keystone** — refused while nobody has the `admin` role of the application in Keystone. The
   synchronisation binds the local records by user name and disables the rest; optionally the local passwords and TOTP
   secrets are deleted from OpenBao.

**Back to local users** returns the sign-in to the panel (only people who still have a local password can sign in).
When nobody can sign in at all: `docker compose exec backend python -m app.cli create-admin <name>`.
