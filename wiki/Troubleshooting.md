# Troubleshooting

```bash
cd /opt/network-control
docker compose ps
docker compose logs -f backend
docker compose logs -f awg-client
docker compose logs -f flow-collector
```

## Gateway tunnel

```bash
docker exec nc-awg-client awg show awg0
ping -c3 -I awg0 10.8.1.0
curl --interface awg0 https://ifconfig.me
ip rule; ip route show table 200
```

- No handshake — make sure the obfuscation parameters and keys in `awg-client/config/awg0.conf` match the server
  (reissue the gateway client in the panel and replace the file).
- The tunnel answers pings but LAN traffic does not pass — check `DOCKER-USER` (`iptables -S DOCKER-USER`)
  and `net.ipv4.ip_forward=1`.

## Routing on MikroTik

```
/ip route print where routing-table=to-vpn
/ip firewall mangle print stats where comment~"amnezia"
/tool traceroute 142.250.74.46
```

If the tunnel route is inactive — the router pings `10.8.1.0` through the gateway; check the `10.8.1.0/32` route
and that `awg-client` is running. While the tunnel is down, traffic uses the fallback route.

## No flows

- `/ip traffic-flow print` — `enabled: yes`, a target pointing at the gateway address, port 2055.
- `docker compose logs flow-collector` — the `goflow2 listening` line; received/written counters every 5 minutes.
- The gateway host firewall must allow UDP 2055.

## Signing in

- "This application is not connected to SPA" — paste the connection key in Settings → SPA (see [Configuration](Configuration.md)); a damaged key is reported on the same page.
- "You have no role in Harbor" — an SPA administrator has to give you `admin`, `operator` or `viewer`.
- Passwords, passkeys and MFA are managed in SPA (user menu → Account and security).
- "SPA rejected the connection key" in Settings — the key was reissued in SPA: paste the new one in Settings → SPA.
- Old UI after an update — `Ctrl+F5` (HTML is not cached, assets are versioned).

## OpenBao

```bash
docker exec nc-openbao bao status -address=http://127.0.0.1:8200
docker compose logs backend | grep -i -E "openbao|setup"
```

`Sealed: true` after a restart — check that `openbao/keys/unseal.key` exists and has the right permissions (owned by the openbao user).

## Map without tiles

Tiles are served through the panel's `/tiles/…`. Check `curl -I http://<panel>/tiles/3/4/2.png` — expect 200 and an
`X-Tile-Cache` header. The server needs access to `tile.openstreetmap.org`.

## Slow downloads from GitHub through the VPN

Symptom: `git clone` and archive downloads from GitHub run at 1–3 Mbit/s, other sites through the VPN are fine.

Cause: packet loss of a few percent on the path from the VPS to home. Senders with loss-based congestion control
(CUBIC, used by GitHub) collapse under it; senders with BBR are barely affected. Check: `iperf3 -u -R` through
the tunnel shows loss VPS → home, `mtr` from the VPS shows loss on the last hops.

Fix: terminate TCP on the VPS and send it home with BBR.

1. On the VPS: `sh vps/github-proxy/install.sh` (set `PAC_PROXIES=<gateway>:3128,10.8.1.0:3128`). It enables BBR
   and starts `gh-proxy` (HTTPS CONNECT, port 443 only) listening on the tunnel address `10.8.1.0:3128`.
2. On the gateway: Settings → Gateway and LAN → port forwards `3128=10.8.1.0:3128` (awg-client restarts the tunnel by itself) —
   the proxy becomes available in the LAN as `<gateway>:3128`.
3. Git: `git config --global http.https://github.com/.proxy http://<gateway>:3128`.
4. Browser: system proxy setting "Use setup script" — `http://<gateway>:3128/proxy.pac` (GitHub domains only,
   falls back to a direct connection).
5. Large files: `aria2c -x16 -s16 --all-proxy=http://<gateway>:3128 <url>`.

Result in a real network: 1.4 → 150–190 Mbit/s for a single stream.
