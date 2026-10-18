FROM amneziavpn/amneziawg-go:latest
RUN apk add --no-cache bash iproute2 iptables jq coreutils
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# root on purpose: the gateway creates the TUN interface, routes and iptables rules of the host network (NET_ADMIN
# works for root only; docker gives no ambient capabilities to other users).
USER root
ENTRYPOINT ["/entrypoint.sh"]
