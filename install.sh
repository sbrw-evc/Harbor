#!/bin/sh
# Installs or updates Network Control. The first run creates .env with random passwords for the bundled PostgreSQL and
# ClickHouse, prepares the data folders and starts everything; the rest is done in the setup wizard of the panel.
set -eu
cd "$(dirname "$0")"

secret() { head -c 32 /dev/urandom | base64 | tr -d '/+=' | cut -c1-28; }

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(secret)/" .env
  sed -i "s/^CLICKHOUSE_PASSWORD=.*/CLICKHOUSE_PASSWORD=$(secret)/" .env
  chmod 600 .env
fi

mkdir -p data/postgres data/openbao data/backend data/shared data/approle data/clickhouse data/geoip data/tiles \
  openbao/keys awg-client/config
[ -s openbao/keys/unseal.key ] || head -c 32 /dev/urandom > openbao/keys/unseal.key

OPENBAO_VERSION=$(sed -n 's/^OPENBAO_VERSION=//p' .env); OPENBAO_VERSION=${OPENBAO_VERSION:-2.6.2}
BAO_IMAGE="openbao/openbao:$OPENBAO_VERSION"
docker pull -q "$BAO_IMAGE" >/dev/null
BAO_UID=$(docker run --rm --entrypoint id "$BAO_IMAGE" -u openbao)
BAO_GID=$(docker run --rm --entrypoint id "$BAO_IMAGE" -g openbao)
chown -R "$BAO_UID:$BAO_GID" data/openbao openbao/keys
chmod 700 openbao/keys data/backend data/shared
chmod 400 openbao/keys/unseal.key
chown 101:101 data/tiles

docker compose pull --ignore-pull-failures --quiet || true
docker compose up -d

PORT=$(sed -n 's/^PANEL_PORT=//p' .env); PORT=${PORT:-8080}
ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
if [ -s data/backend/bootstrap.json ]; then
  echo "Network Control is running: http://${ADDR:-localhost}:$PORT"
  exit 0
fi
for _ in $(seq 1 30); do [ -s data/backend/setup-code ] && break; sleep 2; done
echo "Network Control is running. Open http://${ADDR:-localhost}:$PORT and finish the setup wizard."
echo
echo "  Setup code:           $(cat data/backend/setup-code 2>/dev/null || echo '(see: docker compose logs backend)')"
echo "  PostgreSQL password:  $(sed -n 's/^POSTGRES_PASSWORD=//p' .env)"
echo "  ClickHouse password:  $(sed -n 's/^CLICKHOUSE_PASSWORD=//p' .env)"
echo
echo "Keep openbao/keys/unseal.key and the OpenBao root token and recovery key the wizard shows in a safe place."
