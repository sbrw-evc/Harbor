#!/bin/bash
# The tunnel of the gateway. Its settings come from /config/gateway.json, written by the panel from its settings
# (LAN interface, tunnel network, port forwards); the AmneziaWG config is /config/awg0.conf, written by the panel's
# "Create the gateway client". The container waits for the config, and restarts the tunnel when either file changes.
# LAN_IF, TUNNEL_NET and PORT_FORWARDS in the environment are the defaults of older installations.
set -u
IF=awg0
CONF=/config/awg0.conf
SETTINGS=/config/gateway.json
TABLE=200

FWD=DOCKER-USER
iptables -nL $FWD >/dev/null 2>&1 || FWD=FORWARD

read_settings() {
  LAN_IF=${ENV_LAN_IF:-eth0}
  TUNNEL=${ENV_TUNNEL_NET:-10.8.1.0/24}
  FORWARDS=${ENV_PORT_FORWARDS:-}
  if [ -s "$SETTINGS" ]; then
    LAN_IF=$(jq -r '.lan_if // empty' "$SETTINGS" 2>/dev/null || true); LAN_IF=${LAN_IF:-${ENV_LAN_IF:-eth0}}
    TUNNEL=$(jq -r '.tunnel_net // empty' "$SETTINGS" 2>/dev/null || true); TUNNEL=${TUNNEL:-${ENV_TUNNEL_NET:-10.8.1.0/24}}
    FORWARDS=$(jq -r '.port_forwards // ""' "$SETTINGS" 2>/dev/null || true)
  fi
}

forwards() {
  for f in ${FORWARDS:-}; do
    iptables -t nat "$1" PREROUTING -i "$LAN_IF" -m addrtype --dst-type LOCAL -p tcp --dport "${f%%=*}" \
      -j DNAT --to-destination "${f#*=}" 2>/dev/null
  done
}

down() {
  iptables -D $FWD -i "$LAN_IF" -o $IF -j ACCEPT 2>/dev/null
  iptables -D $FWD -i $IF -o "$LAN_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null
  ip rule del iif "$LAN_IF" lookup $TABLE 2>/dev/null
  iptables -t nat -D POSTROUTING -o $IF -j MASQUERADE 2>/dev/null
  iptables -t mangle -D FORWARD -o $IF -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null
  forwards -D
  awg-quick down "$CONF" 2>/dev/null
}

up() {
  read_settings
  grep -q '^Table' "$CONF" || sed -i '/^\[Interface\]/a Table = off' "$CONF"
  sed -i '/^DNS *=/d' "$CONF"
  export WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go
  awg-quick up "$CONF" || return 1

  ip route replace default dev $IF table $TABLE
  ip route replace "$TUNNEL" dev $IF table $TABLE
  for n in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
    ip route replace throw $n table $TABLE
  done
  ip rule add iif "$LAN_IF" lookup $TABLE priority 1000
  iptables -I $FWD 1 -i "$LAN_IF" -o $IF -j ACCEPT
  iptables -I $FWD 1 -i $IF -o "$LAN_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  iptables -t nat -A POSTROUTING -o $IF -j MASQUERADE
  iptables -t mangle -A FORWARD -o $IF -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
  forwards -A
  echo "awg0 up (LAN $LAN_IF, tunnel $TUNNEL${FORWARDS:+, forwards $FORWARDS})"; awg show $IF
}

stamp() { stat -c '%Y %s' "$CONF" "$SETTINGS" 2>/dev/null | tr '\n' ' '; }

ENV_LAN_IF=${LAN_IF:-}
ENV_TUNNEL_NET=${TUNNEL_NET:-}
ENV_PORT_FORWARDS=${PORT_FORWARDS:-}
read_settings
trap 'down; exit 0' TERM INT

while [ ! -s "$CONF" ]; do
  echo "waiting for $CONF: create the gateway client in the panel (Settings → Infrastructure)"
  sleep 30 & wait $!
done

down
up || { echo "awg0 did not start"; sleep 10; exit 1; }
last=$(stamp)
while true; do
  sleep 10 & wait $!
  now=$(stamp)
  if [ "$now" != "$last" ]; then
    echo "the gateway settings or the config changed: restarting the tunnel"
    down
    up || { echo "awg0 did not start"; exit 1; }
    last=$now
  fi
  ip link show $IF >/dev/null 2>&1 || { echo "awg0 gone"; down; exit 1; }
done
