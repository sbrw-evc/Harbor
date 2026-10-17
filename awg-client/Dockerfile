FROM amneziavpn/amneziawg-go:latest
RUN apk add --no-cache bash iproute2 iptables jq coreutils
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
