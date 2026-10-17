# Monitoring and alerts

## Overview

- KPIs: online now, clients, traffic today / 7 / 30 days, current throughput.
- Health: VPN server, home gateway, route on MikroTik, open alerts.
- Live throughput chart (updated over SSE on every server poll).
- Client statuses, top clients by traffic, recent connections.

Every page refreshes by itself: server events (`live`, `clients`, `alert`, `audit`, `connection`) invalidate
the related data, plus a background refresh every 30 seconds and when you return to the tab.

## Analytics

VPN traffic for 1 h – 90 days, top clients, activity heatmap by hour and weekday,
connections by country, CSV export.

## Connection map

Points are places clients connected from (by endpoint IP), sized by the number of connections; green — clients online;
yellow — the server. OpenStreetMap tiles are served through the panel's caching proxy (`/tiles`) with an identifying
User-Agent — browsers never contact OSM servers directly. Geolocation — DB-IP Lite (updated monthly).

## Alerts

| Key | Severity | Condition |
|---|---|---|
| `server.unreachable` | critical | No SSH access to the VPS / AmneziaWG container |
| `gateway.stale.*` | critical | No handshake from the home gateway for longer than the threshold |
| `router.tunnel_down` | critical | The tunnel route on MikroTik is inactive (fallback is in use) |
| `router.unreachable` | warning | No SSH access to MikroTik |
| `openbao.down` | critical | OpenBao is unreachable or sealed |
| `client.quota_warn.*` | warning | Quota threshold reached |
| `client.quota.*` | warning | Quota exhausted, client blocked |
| `client.expiring.*` | info | Access expires soon |
| `client.expired.*` | info | Access expired, client blocked |

Alerts are deduplicated by key and closed automatically when the condition clears. They can be acknowledged.

## In-app notifications

Every panel message — action results, sign-in and data-loading errors, server alerts — is shown as a toast
in the bottom-right corner: at most 5 at a time, dismissed automatically (success after 4 s, errors after 9 s)
or with the close button. Repeated errors from background refreshes are not duplicated.

The bell at the bottom of the sidebar opens the **notification center**: the last 100 messages
(kept in the browser), an unread counter, a type filter, "mark as read" and "clear".

## Telegram

1. Create a bot with @BotFather.
2. **Settings → Telegram bot** — paste the token (validated via `getMe` and stored in OpenBao).
3. Set the chat id (user, group or channel), enable notifications and choose the minimum severity.
4. "Test message" — check the setup.

New alerts at or above the selected severity are sent, plus "resolved" messages for critical ones.
