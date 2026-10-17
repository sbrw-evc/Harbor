# API

Interactive OpenAPI documentation: `/api/docs` (schema — `/api/openapi.json`).

## Authentication

- **Browser** — sign-in through SPA (`GET /api/auth/spa/start?return=…` → SPA → `GET /api/auth/spa/callback`), then the
  session cookie + the `X-Requested-With: network-control` header for state-changing requests.
- **Integrations** — an API token: `Authorization: Bearer ac_…` (the role is set when the token is issued; at most the
  role of its owner, and it stops working when SPA disables the owner).

```bash
curl -H "Authorization: Bearer $TOKEN" http://<panel>/api/analytics/summary
```

## Main endpoints

| Method | Path | Role | Description |
|---|---|---|---|
| GET | `/api/clients` | viewer | Client list (`q`, `status`, `tag`) |
| POST | `/api/clients` | operator | Create a client |
| GET/PATCH/DELETE | `/api/clients/{id}` | viewer / operator | Details, update, delete |
| POST | `/api/clients/{id}/block` · `/unblock` · `/reissue` | operator | Block, unblock, reissue |
| GET | `/api/clients/{id}/config` · `/config.conf` | operator | Config (QR, `.conf`, `vpn://`) |
| POST | `/api/clients/{id}/share` | operator | One-time link |
| POST | `/api/clients/bulk` · `/sync` | operator | Bulk actions, synchronization |
| GET | `/api/analytics/summary` · `/live` · `/traffic` · `/top` · `/heatmap` · `/geo` | viewer | Analytics |
| GET | `/api/flows/status` · `/overview` · `/fields` | viewer | Flows: status and overview |
| POST | `/api/flows/visualize` · `/recent` | viewer | Visualization and recent flows |
| GET/POST/DELETE | `/api/flows/filters` | viewer | Saved filters |
| GET | `/api/routing/status` · `/custom` | viewer | Routing |
| POST | `/api/routing/check` | viewer | Where will the traffic go |
| POST | `/api/routing/site-list` · `/custom` | administrator | Site list upload, custom prefixes |
| GET/PUT | `/api/server/config` | administrator | AWG server config |
| GET/POST/DELETE | `/api/backups` | administrator | Backups |
| GET/POST/DELETE | `/api/tokens` | administrator | API tokens |
| GET | `/api/users` | administrator | Users (read-only, synchronised from SPA) |
| GET · POST | `/api/spa` · `/api/spa/sync` | administrator | SPA connection status; Synchronize now |
| GET · POST | `/api/auth/config` · `/api/auth/logout` | — / session | SPA connection for the sign-in page; sign-out (returns SPA's sign-out address) |
| GET/PUT | `/api/settings` | viewer / administrator | Settings |
| GET | `/api/alerts` · `/api/audit` | viewer / administrator | Alerts, audit |
| GET | `/api/events` | viewer | Server-Sent Events |
| GET | `/api/devices/platforms` | viewer | Supported platforms, transports, credentials and capabilities |
| GET/POST | `/api/devices` | viewer / administrator | Device list, add a device (`credentials` go to OpenBao) |
| GET/PATCH/DELETE | `/api/devices/{id}` | viewer / administrator | Details with interfaces, neighbours, children and clients; update; delete |
| POST | `/api/devices/test` · `/discover` | administrator | Test unsaved parameters; probe an address or subnet |
| POST | `/api/devices/{id}/poll` | operator | Poll now |
| POST | `/api/devices/{id}/actions` | operator / administrator | `{"action": "hostname|interface|dns|ntp|flow_export|reboot|endpoint_block|command", "params": {…}}` |
| GET | `/api/devices/{id}/backup` | administrator | Running configuration as text |
| GET | `/api/endpoints` | viewer | Network clients (`q`, `online`, `device_id`) |
| PATCH/DELETE | `/api/endpoints/{mac}` · POST `/block` | operator | Name and note, forget, block on the controller |
| GET · PUT | `/api/topology` · `/api/topology/positions` | viewer · operator | Network map graph; saved device positions |
| GET | `/api/flows/exporters` | viewer | Exporters and their interfaces |
| GET/POST/PATCH/DELETE | `/api/flows/rules` · POST `/rules/{id}/test` | viewer / administrator | Detection rules |
| GET · POST | `/api/flows/threats` · `/threats/refresh` | viewer · administrator | Threat contacts and lists |
| GET/POST/DELETE | `/api/flows/views` | viewer | Saved visualization views |

### Example: flow visualization

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://<panel>/api/flows/visualize \
  -d '{"dimensions":["Site","Route"],"limit":10,"graph":"stacked","unit":"bps","filter":"Direction = out"}'
```

## Events (SSE)

`GET /api/events` — a `text/event-stream`:

| Event | Data |
|---|---|
| `live` | `t`, `rx`, `tx` (bytes/s), `online`, `clients` — per-client rates |
| `clients` | A client changed (`id`) or a synchronization happened |
| `alert` | A new or resolved alert |
| `audit` | A new audit entry |
| `connection` | A new client connection (city, country) |
| `devices` | A device was added, changed, deleted or polled (`id`, `action`, `status`) |

## Errors

Error response: `{"detail": "text"}`. Codes: 400 — invalid data, 401 — sign-in required, 403 — no permission or CSRF,
404 — not found, 409 — conflict, 422 — validation, 502 — the VPS, MikroTik, OpenBao or ClickHouse is unavailable.
