"""Files the side containers read instead of environment variables, written by the backend from the settings.

* ``gateway.json`` (awg-client): the LAN interface, the tunnel network and the port forwards of the gateway; next to it
  ``awg0.conf``, the AmneziaWG config of the gateway's own client, written by "Create the gateway client".
* ``collector.json`` (flow-collector): the networks that count as local, the addresses of the gateway, the VPS and the
  router, and how to reach ClickHouse (its password included: the file is 0600 on a volume only the collector reads).

awg-client and flow-collector re-read their file when it changes."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..runtime import RuntimeConfig

log = logging.getLogger(__name__)

GATEWAY_FILE = "gateway.json"
AWG_CONF = "awg0.conf"
COLLECTOR_FILE = "collector.json"


def _write(path: Path, text: str, mode: int) -> bool:
    try:
        if path.exists() and path.read_text() == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        return True
    except OSError as e:  # the volume is not mounted (development): the side containers keep their defaults
        log.warning("cannot write %s: %s", path, e)
        return False


class GatewayFiles:
    def __init__(self, gateway_dir: str, shared_dir: str, clickhouse: dict):
        self._gateway = Path(gateway_dir)
        self._shared = Path(shared_dir)
        self._clickhouse = clickhouse

    def gateway(self, rc: RuntimeConfig) -> dict:
        return {"lan_if": rc.lan_if, "tunnel_net": rc.tunnel_net, "port_forwards": rc.port_forwards}

    def collector(self, rc: RuntimeConfig) -> dict:
        lan = [n for n in (rc.lan_cidr, rc.mt_wg_network) if n]
        return {"lan_cidr": ",".join(lan), "gateway_ip": rc.gateway_ip, "awg_host": rc.awg_host,
                "router_host": rc.router_host, "clickhouse": self._clickhouse}

    def write(self, rc: RuntimeConfig) -> None:
        if _write(self._gateway / GATEWAY_FILE, json.dumps(self.gateway(rc), indent=2) + "\n", 0o644):
            log.info("gateway.json updated")
        if _write(self._shared / COLLECTOR_FILE, json.dumps(self.collector(rc), indent=2) + "\n", 0o600):
            log.info("collector.json updated")

    def has_awg_conf(self) -> bool:
        return (self._gateway / AWG_CONF).exists()

    def write_awg_conf(self, text: str) -> None:
        if not _write(self._gateway / AWG_CONF, text if text.endswith("\n") else text + "\n", 0o600) \
                and not self.has_awg_conf():
            raise OSError("the awg-client config folder is not mounted into the backend")
