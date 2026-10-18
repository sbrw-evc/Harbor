# Installation

## Requirements

- A host on the home network for the panel and the gateway: an LXC container (Proxmox) or a VM with Docker 24+ and
  Compose v2; 2–3 GB RAM, 10+ GB disk. For LXC: unprivileged, `nesting=1`, `/dev/net/tun` passed through.
- MikroTik with RouterOS 7.
- Optionally a self-hosted Amnezia server with an AmneziaWG container (`amnezia-awg2`, AmneziaWG protocol 2.x/3.x).
- Optionally Keystone (SPA) for single sign-on; without it the panel has its own users.

### LXC on Proxmox

```bash
pct create 302 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst --hostname network-control \
  --cores 2 --memory 3072 --swap 1024 --rootfs local-lvm:20 \
  --net0 name=eth0,bridge=vmbr0,gw=192.168.0.1,ip=192.168.0.102/24,type=veth \
  --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1 --dev0 /dev/net/tun
pct start 302
pct exec 302 -- bash -c "apt-get update && apt-get install -y curl git && curl -fsSL https://get.docker.com | sh"
```

Enable forwarding inside the container (the gateway routes the tunnel):

```bash
cat >/etc/sysctl.d/99-gw.conf <<EOF
net.ipv4.ip_forward=1
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
EOF
sysctl -p /etc/sysctl.d/99-gw.conf
```

## Installing the panel

```bash
git clone https://onedev.evcsbrw.ru/Monitoring/Harbor /opt/network-control
cd /opt/network-control
./install.sh
```

`install.sh` creates `.env` with random passwords for the bundled PostgreSQL and ClickHouse, the data folders and the
OpenBao unseal key, starts the stack and prints:

- the address of the panel,
- the **setup code** (also in `docker compose logs backend`),
- the passwords of PostgreSQL and ClickHouse for the storage step.

Open the address and go through the **setup wizard** (see [[Configuration]]): the storage, then the sign-in (a local
administrator or Keystone), whose button applies everything. Save the OpenBao root token and recovery key the wizard shows once.

The **First steps** checklist of the overview then leads through the rest:

1. **Settings → Router**: the address of the MikroTik. Add the SSH key of the panel shown in **Access and storage** to
   the router (the ready command is there) and press **Check the router**. See [[MikroTik]].
2. **Settings → Amnezia server**: the address of the VPS; add the same key to `/root/.ssh/authorized_keys` there.
3. **Settings → Gateway and LAN**: the address of this host, then **Create the gateway client** — the panel writes
   `awg0.conf` and the tunnel starts by itself.
4. **Routing → Upload ip-list.json** — the site list is applied on the router and loaded into ClickHouse.
5. **Settings → Telegram bot** — optionally connect notifications.

Images come from the OneDev registry; the version is `NC_VERSION` in `.env`:

| Image | Platforms |
|---|---|
| `onedev.evcsbrw.ru/monitoring/harbor/backend` | linux/amd64 |
| `onedev.evcsbrw.ru/monitoring/harbor/frontend` | linux/amd64 |
| `onedev.evcsbrw.ru/monitoring/harbor/flow-collector` | linux/amd64 |
| `onedev.evcsbrw.ru/monitoring/harbor/awg-client` | linux/amd64 |

## Updating

```bash
cd /opt/network-control
git pull
sed -i 's/^NC_VERSION=.*/NC_VERSION=2.1.0/' .env
./install.sh
```

The schemas of PostgreSQL and ClickHouse are updated automatically.

### From a version before the setup wizard

Nothing to do by hand: the backend takes the installation over once. It reads the old `.env` (mounted read-only) and
the AppRole files of the former `bao-init` service, moves the passwords and the SPA connection key into OpenBao, turns
`AWG_HOST`, `ROUTER_HOST`, `GATEWAY_IP`, `LAN_CIDR`, `PUBLIC_BASE_URL` and the rest into settings and writes
`data/backend/bootstrap.json`; an installation that signed in through SPA keeps doing so. The log lists what was taken
over; afterwards only the passwords, the version and the ports have to stay in `.env`. PostgreSQL moves to the
TimescaleDB image with the same data folder.

## Keep separately

- `openbao/keys/unseal.key` — the OpenBao auto-unseal key. Without it, OpenBao data and snapshots cannot be restored.
- The OpenBao root token and recovery key shown by the wizard (old installations: `data/bao-init/init.json`).
- `data/backend/bootstrap.json` and `data/backend/approle/` — how the panel reaches its storage.
- `.env`.
