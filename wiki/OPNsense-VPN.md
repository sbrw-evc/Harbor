# VPN on OPNsense

**English** · [Русский](ru-OPNsense-VPN.md)

The third VPN server of the panel, next to Amnezia on the VPS and WireGuard on MikroTik: a WireGuard instance on an
OPNsense firewall (24.1 or newer, the WireGuard plugin is part of the core). The panel manages the peers of one
instance through the OPNsense REST API; they are regular panel clients — creation, blocking, quotas, access periods,
key reissue, traffic, the connection map, QR codes and one-time links work as for the other servers.

| | Amnezia (VPS) | MikroTik (home) | OPNsense |
|---|---|---|---|
| Protocol | AmneziaWG (obfuscated) | WireGuard | WireGuard |
| Exit to the internet | VPS | Home ISP; sites from the list — through Amnezia | By the routing of OPNsense |
| Access to the home network | — | Yes | By the firewall rules of OPNsense |
| Who sets up the server | The panel | The panel (**Set up on MikroTik**) | You, in OPNsense |
| Reached by the panel | SSH | SSH | HTTPS (API key) |

## What is set up in OPNsense

The panel does not create the instance, NAT or firewall rules — they are set up in OPNsense once:

1. **VPN → WireGuard → Instances**: an instance (for example `wg0`, port `51821`, tunnel address `10.10.0.1/24`).
   **VPN → WireGuard → Settings**: WireGuard enabled.
2. Firewall: a WAN rule that passes the port (UDP `51821`); rules on the WireGuard interface for what clients may
   reach; outbound NAT for `10.10.0.0/24` if clients go to the internet through OPNsense.
3. If OPNsense stands behind another router (for example MikroTik), a port forward of UDP `51821` to OPNsense.
4. **System → Access → Users**: a user for the panel with the privileges of the WireGuard pages
   (`VPN: WireGuard`), and an API key for it (**API keys → +**, a file with `key` and `secret` is downloaded).

## Connecting the panel

1. **Settings → WireGuard on OPNsense**: the address of OPNsense (`opn_host`, e.g. `192.168.0.50`), the instance
   (`opn_wg_instance`: its name or device, `wg0` by default) and whether to check the TLS certificate
   (`opn_verify_tls`, off for the self-signed certificate OPNsense has after installation).
2. **VPN on OPNsense → OPNsense API key**: the key and the secret. The panel tries them against the API first and
   keeps them only in OpenBao (`opnsense/api`); nothing secret is stored in the database. **Remove** deletes the key —
   the panel stops managing the peers, the peers stay on OPNsense.

The page shows the state of the instance: its device, port, client network, the address of OPNsense in the tunnel, the
public key, the number of peers (blocked, online).

## Client config parameters

| Setting | Default | Description |
|---|---|---|
| Connection address (`opn_endpoint_host`) | the public IP of the MikroTik in front (when the router is set) | A domain or an address; the port is the port of the instance |
| DNS (`opn_client_dns`) | the address of OPNsense in the tunnel (`10.10.0.1`) | Unbound of OPNsense must allow the tunnel network (Access Lists) |
| AllowedIPs (`opn_allowed_ips`) | `0.0.0.0/0` | "Home network only" preset: `LAN_CIDR, <network of the instance>` |

Changes apply to configs downloaded afterwards.

## How the panel works with the API

| Action | API |
|---|---|
| The instance | `GET /api/wireguard/server/searchServer`, `getServer/{uuid}` |
| The peers | `POST /api/wireguard/client/searchClient` — the peers whose `servers` include the instance |
| New client | `addClient` with `enabled`, `name`, `pubkey`, `psk`, `tunneladdress` (the next free address of the instance network), `servers` |
| Block / unblock, rename, reissue keys | `setClient/{uuid}` with `enabled` / `name` / `pubkey` — always with `servers`, read by `getClient` first (without it OPNsense unbinds the peer from the instance) |
| Delete | `delClient/{uuid}` |
| Apply | `POST /api/wireguard/service/reconfigure` after every change |
| Traffic | `GET /api/wireguard/service/show`: rx/tx, latest handshake, endpoint of the peers of the instance device |

Peers created in OPNsense by hand are imported by **Synchronize** without a private key — reissue the keys to get a
config. Peers of other instances are not touched.

## Troubleshooting

| Symptom | Cause |
|---|---|
| "API denied access (401/403)" | Wrong key, or the user lacks the WireGuard privileges |
| "API is unreachable" | Address, HTTPS port, the web GUI listening interfaces, or TLS check on with a self-signed certificate |
| "WireGuard instance 'wg0' was not found" | The name in `opn_wg_instance` matches neither the name nor the device of an instance |
| Client connects, no handshake | The port forward / WAN rule, or the endpoint address in the config |
| Handshake, but no traffic | Firewall rules of the WireGuard interface, outbound NAT, DNS access list |
