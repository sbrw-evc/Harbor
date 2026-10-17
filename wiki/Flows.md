# Flow analytics

The module follows the ideas of [Akvorado](https://github.com/akvorado/akvorado) in a lightweight form: no Kafka and
no separate orchestration, with storage in ClickHouse and the console built into the panel.

## Pipeline

```
routers, switches, firewalls ──NetFlow v5 / v9, IPFIX (UDP 2055) · sFlow v5 (UDP 6343)──► goflow2 ──JSON──►
    enrichment ──batch──► ClickHouse.flows ──materialized views──► flows_5m · flows_1h
```

Any device can be an exporter: MikroTik Traffic Flow, Cisco Flexible NetFlow, OPNsense, OpenWrt softflowd, FortiGate,
HPE / Aruba sFlow and others. The **Flow export** action on the [device page](Devices) configures it.

Enrichment in `flow-collector`:

| Field | Source |
|---|---|
| `Direction` | `out` — source in the LAN, `in` — source outside |
| `Route` | `vpn` — next hop = gateway (outbound) or destination = router's LAN address (inbound via masquerade); otherwise `direct` |
| `LocalAddr` / `RemoteAddr` | Local and remote sides, taking direction into account |
| `RemoteCountry` | DB-IP City Lite |
| `RemoteAS` / `RemoteASName` | DB-IP ASN Lite |
| `Bytes` / `Packets` | Adjusted for the sampling rate |
| `ExporterAddress`, `InIf`, `OutIf`, `SamplingRate` | the exporter's source address, ifIndex of the input / output interface, sampling |
| `TCPFlags` | OR of the TCP flags of the flow (SYN-only flows reveal scans and SYN floods) |
| `ServicePort` | the server-side port (the lower of the two ports), the base of the `Application` dimension |

`Direction = internal` — traffic between two local addresses (inter-VLAN, LAN ↔ server); turn it off with
`KEEP_INTERNAL=0` for the collector. Local networks are `LAN_CIDR` plus every private IPv4 subnet and IPv6 LAN prefix
(/48–/64) found on the interfaces of managed devices (refreshed every minute); IPv6 unique local (`fc00::/7`) and
link-local (`fe80::/10`) addresses are always local. Excluded: tunnel transport to the VPS, multicast/broadcast.

IPv4 and IPv6 flows are collected alike. Addresses are stored in `IPv6` columns (IPv4 as `::ffff:a.b.c.d`) and shown in
their usual form; an installation upgraded from 2.0 converts its tables at the first start.
Site attribution happens at query time through the ClickHouse dictionary `sites` (`ip_trie`), which is filled when
`ip-list.json` is uploaded and custom prefixes are added — so a new mapping applies to existing data as well.

Names come from ClickHouse dictionaries refreshed every 2 minutes from the device registry: `exporters` (exporter
address → device), `if_names` (exporter + ifIndex → interface name and speed), `hosts` (LAN address → client name),
`ptr` (remote address → reverse DNS for the top 300 addresses of the last hour, every 10 minutes) and `threats`
(public blocklists, see below).

## Overview

Flows per second, time of the last flow, share of traffic through the VPN over 24 h, database size; a 24-hour chart;
top sites, AS, countries, protocols, remote ports, LAN devices, applications, exporters and threat contacts over the
last hour; the last flow with all fields.

## Visualization

| Option | Values |
|---|---|
| Period | 15 min … 1 year, custom range, shift back/forward, zoom out, auto refresh (10 s – 5 min) |
| Dimensions | up to 6, reorderable, a button to reverse the order |
| Limit | 1–50 series, the rest is grouped as "Other" |
| Top by | average (`avg`), peaks (`max`), latest values (`last`) |
| Units | bits/s, packets/s, flows/s |
| Graph | Stacked, 100%, Lines, Grid (small multiples), Sankey |
| Bidirectional | inbound traffic is drawn as negative values |
| Previous period | a dashed "total" line for the previous hour / day / week / month / year |

The table under the chart shows minimum, maximum, last, average, 95th percentile and total (bytes / packets / flows).
All options are kept in the URL — you can share a link. `Ctrl+Enter` applies.

### Dimensions

`Direction`, `Route`, `LocalAddr`, `LocalHost` (client name), `RemoteAddr`, `RemoteNet` (/24 for IPv4, /64 for IPv6), `RemoteHost` (PTR),
`RemoteAS`, `RemoteASName`, `RemoteCountry`, `Site`, `SiteGroup`, `Threat`, `Proto`, `Application`, `ServicePort`,
`LocalPort`, `RemotePort`, `TCPFlags`, `Exporter`, `ExporterName`, `InIfName`, `OutIfName`.

### Aggregates

Besides the raw `flows` table (`FLOWS_TTL_DAYS`, 30 days) ClickHouse keeps `flows_5m` (90 days) and `flows_1h`
(2 years) — SummingMergeTree tables filled by materialized views with the low-cardinality dimensions (direction,
route, exporter and interfaces, local address, AS, country, protocol, service port). A query over more than a day
whose dimensions and filter fields all exist there is answered from `flows_5m`, over more than a week — from
`flows_1h`; the chart shows which table was used. On upgrade the aggregates are filled from the existing raw flows.

### Saved views

The whole visualization state (period, dimensions, filter, graph) can be saved under a name and shared.

## Filter language

```
condition  := Field operator value | Field IN (value, ...) | Field NOTIN (value, ...)
expression := condition | NOT expression | (expression) | expression AND expression | expression OR expression
```

| Field type | Operators | Values |
|---|---|---|
| Address (`LocalAddr`, `RemoteAddr`, `RemoteNet`, `Exporter`) | `=`, `!=` | `203.0.113.5`, `203.0.113.0/24`, `2001:db8::5`, `2001:db8::/48` |
| AS (`RemoteAS`) | `=`, `!=`, `<`, `<=`, `>`, `>=`, `IN` | `AS13335` or `13335` |
| Port, protocol | numeric, `IN` | `443`; protocol — `tcp`, `udp`, `icmp` or a number |
| Strings (`Site`, `RemoteCountry`, `RemoteASName`, `SiteGroup`) and `Direction`, `Route` | `=`, `!=`, `IN`, `NOTIN`, `LIKE`, `UNLIKE`, `ILIKE`, `IUNLIKE` | `vpn`, `'%google%'` |

Field names are case-insensitive, strings use single or double quotes, comments are `--` and `/* */`.
All values are passed to ClickHouse as query parameters.

Examples:

```
Route = vpn AND RemoteCountry IN (US, NL)
Site LIKE '%google%' AND NOT Proto = udp
Direction = out AND LocalAddr = 192.168.0.50
RemoteAS = AS13335 OR RemoteASName ILIKE '%cloudflare%'
Route = direct AND SiteGroup = video   -- listed sites that bypassed the VPN
```

### Saved filters

A filter can be saved under a name; saved filters are visible to all operators. They can be deleted by their author or an administrator.

## Flows

A table of the last 200 flows within the hour, with a filter and auto refresh: client name, PTR name, application,
TCP flags, exporter and interfaces, threat list.

## Exporters

Every device sending flows in the last 24 hours: name from the device registry, flows per second, traffic, sampling
rate, last flow, and per-interface input / output rates over the last 5 minutes with the interface speed and
utilisation.

## Detection rules

Rules are evaluated every minute by the leader process; a firing group raises an alert (and a Telegram message) with a
**Traffic** link to the matching visualization, and resolves after `clear_count` evaluations without the condition.

| Type | Condition (defaults) |
|---|---|
| Traffic threshold | any dimensions, bps or pps `>` or `<` a threshold over a window |
| DDoS: volumetric | inbound bps ≥ 500 Mbit/s or pps ≥ 100k to one local address over 60 s, optionally above k × its rate in the previous hour |
| DDoS: SYN flood | SYN-without-ACK packets ≥ 2000 pps and ≥ 60 % of TCP packets to one local address |
| Scan | inbound: one remote address hits ≥ 100 ports or ≥ 50 local addresses in 5 min; outbound: a local device contacts ≥ 50 addresses (infected device) |
| Weekly anomaly | the last 5 minutes deviate from the median of the same time on previous weeks by more than max(k × 1.4826 × MAD, ratio × median) |
| Traffic drop | the last 5 minutes are below `ratio` × the usual traffic at this time |
| Exporter silent | an exporter seen in the last 24 h sent nothing for N minutes |
| Threat contact | traffic with an address from the threat lists |
| Link utilization | interface rate ≥ N % of its speed (speed from the device registry) |

Each rule has a filter in the same language as the visualization, a severity, *for* (evaluations in a row before
firing) and *clear* counts. **Test** evaluates the rule now and shows the groups that would fire. Five rules are
created on first start (volumetric DDoS, SYN flood, threat contact, scan — disabled, silent exporter).

## Threats

Public blocklists are downloaded every `threat_refresh_hours` (6) into the `threats` dictionary: FireHOL level1,
Spamhaus DROP, abuse.ch Feodo Tracker (CINS Army is available, off by default). Private, reserved and multicast ranges,
`LAN_CIDR` and the networks from `threat_exclude` are never flagged. The **Threats** tab lists local devices that
exchanged traffic with listed addresses; the `Threat` dimension and the threat-contact rule use the same data.

## Resources

ClickHouse is limited to 768 MB of memory, system logs are disabled, raw flows are kept for `FLOWS_TTL_DAYS` days,
5-minute aggregates for 90 days and hourly aggregates for 2 years.
For a home network this is tens of megabytes per day.
