# MikroTik setup

`mikrotik/amnezia-control.rsc` configures policy routing and NetFlow export. Edit the variables at the top
(`gateway`, `tunnelProbe`, `lanIface`, `wanIface`, `flowPort`), upload the file to the router and import it:

```
/export file=before-amnezia
/system backup save name=before-amnezia
/import file-name=amnezia-control.rsc
```

## What gets created

| Object | Purpose |
|---|---|
| Table `to-vpn` | A separate routing table for traffic to listed sites |
| Route `10.8.1.0/32` → gateway | Tunnel probe: the server's in-tunnel address is reachable only through the gateway |
| `0.0.0.0/0` → `10.8.1.0` in `to-vpn` | Recursive route with `check-gateway=ping` — active only while the tunnel is alive |
| Fallback `0.0.0.0/0` → ISP (distance 10) | If the tunnel is down, listed sites go directly instead of failing |
| WAN DHCP client script | Updates the fallback route gateway when the ISP address changes |
| Rule `routing-mark=to-vpn` | Lookup in the `to-vpn` table |
| Mangle (2 rules) | Marks new connections to `vpn-sites` and routes them into `to-vpn` |
| NAT masquerade | Replies from the tunnel come back through the router (symmetric conntrack) |
| Fasttrack `connection-mark=no-mark` | Marked connections are not fasttracked |
| `send-redirects=no` | The router does not send ICMP redirects towards the gateway |
| Traffic Flow (NetFlow v9) | Flow export to the collector, port 2055/udp |

The `vpn-sites` address list is filled by the panel ("Routing" section), which applies changes as a diff.

## How flows are classified

- Source in the LAN → `Direction = out`; `Route = vpn` if the next hop is the gateway.
- Source outside the LAN → `Direction = in`; `Route = vpn` if the destination is the router's LAN address (masquerade), otherwise `direct`.
- Traffic inside the LAN and the tunnel transport to the VPS are excluded.

## Load

Fasttrack stays enabled for direct traffic. Marked VPN connections take the slow path.
Traffic Flow adds CPU load; on weak switches (CRS3xx) keep an eye on `cpu-load`.

## Rollback

```
/import file-name=amnezia-control-remove.rsc
```

or `/system backup load name=before-amnezia`.
