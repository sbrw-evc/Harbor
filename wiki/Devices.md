# Network devices

**English** · [Русский](ru-Devices)

Harbor manages routers, switches, firewalls, access points, controllers and servers from home and corporate
vendors. Each device is polled on a schedule; the panel keeps its facts, interfaces with live rates, LLDP / CDP / MNDP
neighbours, devices behind a controller and the clients it sees. The same data builds the [network map](Network-Map)
and names exporters, interfaces and LAN hosts in [flow analytics](Flows).

## Supported platforms

| Platform | Segment | Transports | What is collected | What can be changed |
|---|---|---|---|---|
| MikroTik RouterOS 7 (RouterBOARD, CRS, hAP, cAP, CHR) | any | REST API (HTTPS / HTTP), SSH (7.13+), SNMP | facts, interfaces + counters, MNDP / LLDP / CDP, ARP, DHCP leases, Wi-Fi (wifi / wireless / CAPsMAN), bridge hosts, CAPsMAN APs | name, interfaces, DNS, NTP, NetFlow / IPFIX export, reboot, CLI, `/export` |
| MikroTik RouterOS 6 | any | SNMP | standard MIBs | name, interface state (SNMP write) |
| TP-Link JetStream / Omada switches (standalone) | SMB | SSH, SNMP | facts, ports, LLDP, ARP, MAC table | name, ports, NTP, reboot, CLI, running config |
| TP-Link Archer / Deco / Easy Smart | home | SNMP | standard MIBs | — |
| TP-Link Omada SDN controller (software, OC200 / OC300) | SMB | HTTPS Open API | APs, switches, gateways (uplinks), clients (SSID, signal, port) | reboot a managed device, block / unblock clients |
| OPNsense | SMB | HTTPS API, SNMP | facts, interfaces + counters, ARP, DHCP (ISC / Kea / dnsmasq) | NetFlow export, reboot, `config.xml` |
| pfSense (pfSense-pkg-RESTAPI v2) | SMB | HTTPS API, SNMP | facts, interfaces + counters, ARP, DHCP | hostname, DNS, reboot, shell command, `config.xml` |
| OpenWrt (GL.iNet, Turris, Cudy…) | home | SSH, SNMP | board, interfaces + counters, ARP, DHCP, hostapd Wi-Fi clients, lldpd | hostname, interfaces, DNS (dnsmasq), NTP, softflowd export, reboot, shell, `uci export` |
| Keenetic | home | HTTP / HTTPS RCI | facts, interfaces, hotspot hosts (Wi-Fi / wired) | name, interfaces, DNS, NTP, reboot, block clients, CLI, running config |
| Cisco IOS / IOS-XE (Catalyst, ISR, ASR) | enterprise | SSH, SNMP | `show version`, `show interfaces`, CDP + LLDP, ARP, MAC table | hostname, interfaces, DNS, NTP, Flexible NetFlow, reload, CLI, running config |
| Cisco NX-OS (Nexus) | enterprise | SSH, SNMP | as IOS | hostname, interfaces, DNS, NTP, reload, CLI, running config |
| Cisco Meraki Dashboard | enterprise | HTTPS API | organisation devices, statuses, clients | reboot a device |
| Ubiquiti UniFi Network (UDM, UCG, Cloud Key, self-hosted) | SMB | HTTPS API | APs, switches, gateways with uplinks, port counters, clients | reboot a device, block / unblock clients |
| Ubiquiti EdgeRouter / VyOS | SMB | SSH, SNMP | Linux data | hostname, interfaces, reboot, shell |
| Zyxel (GS / XGS / XS switches, USG / ATP / USG FLEX) | SMB | SSH, SNMP | facts, ports, LLDP, ARP, MAC table | hostname, ports, NTP, reboot, CLI, running config |
| HP ProCurve / HPE Aruba ArubaOS-Switch | enterprise | SSH, SNMP | `show system`, ports, LLDP, ARP, MAC table | hostname, ports, DNS, SNTP, sFlow export, reboot, CLI |
| HPE Aruba AOS-CX | enterprise | SSH, SNMP | as Cisco | hostname, interfaces, DNS, NTP, reboot, CLI |
| HPE FlexNetwork / OfficeConnect / H3C (Comware) | enterprise | SSH, SNMP | `display` commands | sysname, interfaces, DNS, NTP, sFlow export, reboot, CLI |
| Dell EMC PowerSwitch OS10 | enterprise | SSH, SNMP | as Cisco | hostname, interfaces, DNS, NTP, reload, CLI |
| Dell PowerSwitch N / PowerConnect | SMB | SSH, SNMP | as Cisco | hostname, interfaces, DNS, NTP, reload, CLI |
| Juniper Junos (EX, SRX, MX, QFX) | enterprise | SSH (`display json`), SNMP | facts, interfaces + counters, LLDP, ARP, Ethernet switching table | host-name, interfaces, name servers, NTP, reboot, CLI, `display set` |
| Huawei VRP (S, CE, AR) | enterprise | SSH, SNMP | `display` commands | sysname, interfaces, DNS, NTP, reboot, CLI |
| Fortinet FortiGate | enterprise | HTTPS API, SNMP | status, interfaces + counters, LLDP, ARP, DHCP | hostname, interfaces, DNS, NTP, NetFlow / sFlow collector, reboot, config backup |
| Eltex MES | enterprise | SSH, SNMP | as Cisco | hostname, interfaces, DNS, NTP, reload, CLI |
| Servers via Redfish (Dell iDRAC, HPE iLO, Lenovo XCC, Supermicro) | enterprise | HTTPS | system, BIOS, BMC firmware, power state, NICs | graceful restart, host name |
| Linux (Debian / Ubuntu / Proxmox / Synology / ASUS Merlin) | any | SSH, SNMP | interfaces + counters, ARP, DHCP leases, lldpd | hostname, interface up / down, reboot, shell |
| Netgear, D-Link and any other device | any | SNMP v1 / v2c / v3 | SNMPv2-MIB, IF-MIB, IP-MIB (ARP), BRIDGE / Q-BRIDGE (MAC table), LLDP-MIB | name and interface state (SNMP write) |

Every platform with SSH or an API can also be polled over SNMP instead (choose the "SNMP" transport).

## Adding a device

**Devices → Add**: choose the platform and transport, enter the address, credentials and, optionally, the location
and the address the device exports flows from. **Test connection** collects everything without saving and shows what
was found. **Find in network** probes an address or a subnet (up to /22) — SSH banner, web interface and SNMP
sysObjectID — and suggests the platform.

When the router address is set (Settings → Router), the router is added automatically (MikroTik over SSH with the panel key from
OpenBao `infra/ssh`, RouterOS 7.13+). For RouterOS below 7.13 switch it to the REST API or SNMP.

### Accounts on the devices

Use a dedicated account with the least rights that cover what you want to do from the panel:

| Platform | Monitoring only | Monitoring and changes |
|---|---|---|
| MikroTik | group with `read,api,rest-api` (REST) or `read,ssh` | add `write,reboot` |
| Cisco / Dell / Aruba / Eltex | privilege 1 + `show` commands (or `enable` password in the form) | privilege 15 |
| Juniper | class `read-only` | class `super-user` or a custom class |
| OPNsense | API key of a user with *Diagnostics* pages | add *System: Firmware / Reboot*, *Diagnostics: NetFlow* |
| pfSense | REST API key with read privileges | add the endpoints you use |
| FortiGate | REST API admin with a read-only profile | read-write profile |
| Omada | Open API application with the *Viewer* role | *Admin* role |
| UniFi | local *View Only* user | *Site Admin* |
| SNMP | read-only community / v3 user | write community / v3 user with a write view |

## Polling

Devices are polled every `poll_seconds` (300 s by default, at least 60 s) by the leader backend process, six at a
time. Interface rates and utilisation are computed from counter deltas between polls. Two failed polls in a row set
the status to *down* and raise a critical alert `device.down.<id>` (also to Telegram); it is resolved by the next
successful poll. **Poll now** on the device page runs a poll immediately.

## Settings from the panel

The **Management** tab shows only what the platform and the chosen transport support:

- **Name**, **DNS**, **NTP**, **interfaces** (enable / disable, description) — operator and administrator.
- **Flow export** — configures NetFlow v5 / v9, IPFIX or sFlow towards the panel's collector (by default the address of the gateway,
  port 2055; sFlow — 6343). After that the device shows up under *Flow analytics → Exporters*.
- **Reboot**, **CLI console**, **configuration backup** — administrator only.
- **Block a client** — on UniFi, Omada and Keenetic (from the *Clients* tab or the *Network clients* page).

Every change is written to the audit log with its parameters (CLI commands are truncated to 300 characters).
Values that end up in device commands are validated strictly: names `[A-Za-z0-9._-]`, descriptions without quotes or
shell characters, servers as IP addresses or host names.

## Network clients

Clients are merged from all devices by MAC address: ARP, DHCP leases, Wi-Fi associations, controller client lists and
MAC tables of switch ports (ports with an LLDP / CDP neighbour are uplinks and are skipped). The attachment point is
the most specific source: Wi-Fi > controller > switch port > DHCP > ARP. The vendor comes from the IEEE OUI registry
(downloaded weekly to `data/backend/oui.tsv`); randomised Wi-Fi MACs are marked as such.

A client is online while it was seen within `endpoint_offline_minutes` (15); clients without a name that were not
seen for `endpoint_retention_days` (90) are forgotten. A name given to a client is used on the map and in flow
analytics (`LocalHost` dimension).

## Security

- Credentials are stored only in OpenBao (`devices/<id>`); the API returns `*_set` flags, never secrets.
- SSH host keys are pinned on the first successful connection (trust on first use); a changed key stops polling until
  an administrator resets it on the device page.
- HTTPS certificates of devices are not verified by default (self-signed); enable **Verify TLS certificate** where a
  trusted certificate is installed.
