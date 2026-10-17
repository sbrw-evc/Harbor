# Network map

**English** · [[Русский|ru-Network-Map]]

**Network map** draws the network from what the [[devices|Devices]] report, refreshed every 20 seconds and on every
poll (SSE):

| Element | Source |
|---|---|
| Managed devices | the device registry; colour by role, red outline — down or poll error |
| Links between devices | LLDP / CDP / MNDP neighbours, matched by MAC, IP or name; label — port and rate; colour — utilisation (amber > 50 %, red > 80 %) |
| Devices behind controllers | Omada, UniFi, Meraki, CAPsMAN: APs, switches and gateways with their uplinks |
| Discovered neighbours | LLDP / CDP neighbours that are not managed; **Add to system** prefills the form with the guessed platform |
| Clients | online clients attached to their AP (Wi-Fi, dashed green) or switch port / router (wired); size — traffic over the last 5 minutes from the flows |
| Internet and VPN | routers and firewalls connect to the Internet; the AmneziaWG server is connected to the router of the settings with the tunnel |

## Interaction

- Click — details: addresses, Wi-Fi, traffic, links with ports and utilisation; links to the device page and to the
  client's traffic in flow analytics.
- Double click on a device — opens its page.
- Drag a device — its position is saved for everyone (operator and above); **Reset positions** returns to the
  automatic layout. **Fixed layout** stops the force layout from moving nodes.
- Search highlights nodes by name, IP, MAC or vendor; **Offline clients** adds clients that are not online now.

The map is also available as JSON: `GET /api/topology?offline=false&endpoints=true`.
