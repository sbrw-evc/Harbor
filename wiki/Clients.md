# VPN clients

## Lifecycle

```
created ──► active ⇄ blocked (manually / expired / quota)
              │
              └──► deleted        "removed on server" — the peer disappeared from awg0.conf outside the panel
```

| Action | What happens on the server |
|---|---|
| Create | An X25519 key pair is generated, a free address is allocated, the peer is added to `awg0.conf` and applied via `awg syncconf`, and an entry is added to `clientsTable` (visible in the Amnezia app). The private key is stored in OpenBao. |
| Block | The peer block is commented out with the `#DISABLED#` prefix and removed from the interface. Key and address are kept. |
| Unblock | The block is uncommented and applied. |
| Reissue keys | A new key pair with the same address and history. The old config stops working. |
| Delete | The peer is removed from the config and `clientsTable`, the key from OpenBao, the history from the database. |

The gateway's system client (`home-gateway`) cannot be blocked or deleted from the panel.

## Access period and quota

- **Access period** — when the date is reached, the client is blocked (`expired`). Extending it (the client page has a "+30 days" button) unblocks the client automatically.
- **Monthly quota** — total traffic since the 1st of the month. When exceeded, the client is blocked (`quota`); it is unblocked automatically when the month changes or the quota is raised.
- **Warnings** — alerts when the quota threshold is reached and N days before expiry.

Checks run every minute.

## Handing out access

In the "Config" dialog:

- **QR** — for AmneziaWG on a phone;
- **`.conf`** — a file for AmneziaWG on a computer;
- **`vpn://`** — a key for the Amnezia VPN app;
- **24 h link** — a one-time public link `/s/<token>` (3 opens) that can be sent to someone without panel access.

Every config disclosure is written to the audit log. A config can be obtained only for clients whose key is stored
in the panel. For clients created in the Amnezia app, use "Reissue".

## Synchronization

Every 5 minutes and on the "Synchronize" button the panel reconciles `awg0.conf`:

- peers created outside the panel are imported (the name is taken from `clientsTable`);
- blocking changes made by hand are reflected in the status;
- missing peers are marked "removed on server".

## Client list

Filters: online, active, blocked, removed on server, no key in panel; tags; search by name, owner, address,
city, key. Sorting by throughput, traffic, handshake. Bulk block, unblock and delete. CSV export.
