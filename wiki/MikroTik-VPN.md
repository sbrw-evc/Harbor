# VPN on MikroTik

**English** · [Русский](ru-MikroTik-VPN)

Besides the Amnezia server on the VPS, the panel manages a WireGuard server on the home MikroTik (RouterOS 7).
Its clients are regular panel clients: creation, blocking, quotas, access periods, traffic, the connection map,
QR codes and one-time links work exactly as for Amnezia clients.

| | Amnezia (VPS) | MikroTik (home) |
|---|---|---|
| Protocol | AmneziaWG (obfuscated) | WireGuard |
| Exit to the internet | VPS | Home ISP; sites from the list — through Amnezia |
| Access to the home network | — | Yes |
| Client apps | AmneziaWG, Amnezia VPN (`vpn://`) | WireGuard, Amnezia VPN (QR or `.conf`) |

## Setup

Open **VPN on MikroTik** and press **Set up on MikroTik** (administrator). The panel idempotently creates:

| Object | Value |
|---|---|
| WireGuard interface | `wg-ac`, port `51820/udp`, MTU 1420, comment `amnezia-control` |
| Address | `10.9.0.1/24` on `wg-ac` |
| Firewall input | `amnezia: wireguard` (UDP 51820), `amnezia: wireguard dns udp/tcp` (DNS from `wg-ac`) |
| Mangle | `amnezia: mark new wg conns to vpn-sites`, `amnezia: route wg conns via tunnel` |

Existing rules are not changed; running the setup again only adds what is missing. The interface, network and port
are set by `MT_WG_IFACE`, `MT_WG_NETWORK`, `MT_WG_PORT` (see [Configuration](Configuration)).

The router must have a public IP on the WAN interface (or a port forward to it). Without one, clients
from the internet cannot connect.

## Client config parameters

| Setting | Default | Description |
|---|---|---|
| Connection address | public IP of the router (`/ip cloud` or the WAN address) | You can set a domain name |
| DNS | `10.9.0.1` (router resolver) | The same DNS as LAN devices, so domain rules in [Routing](Routing) match |
| AllowedIPs | `0.0.0.0/0` | "Home network only" preset: `LAN_CIDR, MT_WG_NETWORK` |

Changes apply to configs downloaded afterwards; already issued configs keep the old values.

## Creating a client

**VPN clients → New client → VPN server: MikroTik (WireGuard)**. The panel generates the keys and a per-client
preshared key (both are stored in OpenBao), allocates the next free address in `10.9.0.0/24` and adds the peer
with the comment `amnezia-control: <name>`. Peers added on the router manually are imported by **Synchronize**
(without a private key — reissue keys to get a config).

## Traffic

The panel polls `/interface wireguard peers` with the same interval as the Amnezia server: rx/tx, last handshake,
endpoint. Traffic of `10.9.0.0/24` clients is also visible in [Flows](Flows) — the network is added to the local networks
of the flow collector.

## Rollback

`mikrotik/amnezia-control-remove.rsc` removes the WireGuard interface, its peers, address and rules along with the rest
of the panel objects.
