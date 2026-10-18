from __future__ import annotations

import datetime
import json
import re
import shlex

from ..domain.errors import ExternalServiceError
from ..domain.models import PeerEntry, PeerRuntime
from ..ports import CommandRunner, ConfigCodec, VpnServerGateway

DISABLED_MARK = "#DISABLED# "
_KV = re.compile(r"\s*+([A-Za-z0-9]++)\s*+=(.*)")  # the value is stripped by parse_kv: no backtracking


def parse_kv(lines: list[str]) -> dict[str, str]:
    out = {}
    for line in lines:
        if line.strip().startswith("#"):
            continue
        m = _KV.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


class AwgConfigCodec(ConfigCodec):
    def parse(self, text: str) -> tuple[dict, list[str], list[PeerEntry]]:
        blocks: list[dict] = []
        cur = None
        for raw in text.splitlines():
            disabled = raw.startswith(DISABLED_MARK)
            line = raw[len(DISABLED_MARK):] if disabled else raw
            if line.strip() in ("[Interface]", "[Peer]"):
                cur = {"type": line.strip(), "lines": [], "disabled": disabled}
                blocks.append(cur)
            if cur is not None:
                cur["lines"].append(line)
        iface = next((b for b in blocks if b["type"] == "[Interface]"), None)
        if not iface:
            raise ExternalServiceError("The server config has no [Interface] section")
        peers = []
        for b in blocks:
            if b["type"] != "[Peer]":
                continue
            lines = b["lines"]
            while lines and not lines[-1].strip():
                lines.pop()
            kv = parse_kv(lines)
            peers.append(PeerEntry(public_key=kv.get("PublicKey", ""), address=kv.get("AllowedIPs", ""),
                                   preshared_key=kv.get("PresharedKey"), disabled=b["disabled"], lines=lines))
        iface_lines = iface["lines"]
        while iface_lines and not iface_lines[-1].strip():
            iface_lines.pop()
        return parse_kv(iface_lines), iface_lines, peers

    def render(self, interface_lines: list[str], peers: list[PeerEntry]) -> str:
        out = list(interface_lines)
        for p in peers:
            out.append("")
            prefix = DISABLED_MARK if p.disabled else ""
            out.extend(prefix + line for line in p.lines)
        return "\n".join(out) + "\n"

    @staticmethod
    def peer_lines(public_key: str, psk: str | None, address: str) -> list[str]:
        lines = ["[Peer]", f"PublicKey = {public_key}"]
        if psk:
            lines.append(f"PresharedKey = {psk}")
        lines.append(f"AllowedIPs = {address}")
        return lines


class AmneziaDockerGateway(VpnServerGateway):
    def __init__(self, runner: CommandRunner, container: str, directory: str, iface: str, binary: str):
        self._ssh = runner
        self._container = container
        self._dir = directory
        self._iface = iface
        self._bin = binary
        self._conf = f"{directory}/{iface}.conf"
        self._table = f"{directory}/clientsTable"

    async def _exec(self, cmd: str, stdin: str | None = None) -> str:
        flag = "-i " if stdin is not None else ""
        return await self._ssh.run(f"docker exec {flag}{self._container} sh -c {shlex.quote(cmd)}", stdin)

    async def read_config(self) -> str:
        return await self._exec(f"cat {self._conf}")

    async def apply_config(self, text: str) -> None:
        AwgConfigCodec().parse(text)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bk = f"{self._dir}/backups"
        # The new config waits next to the current one, in a folder only root writes to, not in the shared /tmp.
        new = f"{self._dir}/panel-new"
        tmp, stripped = f"{new}/{self._iface}.conf", f"{new}/{self._iface}.strip"
        await self._exec(
            f"mkdir -p -m 700 {bk} {new} && cp {self._conf} {bk}/{self._iface}.conf.{ts} && "
            f"ls -1t {bk}/{self._iface}.conf.* | tail -n +51 | xargs -r rm -f && "
            f"cat > {tmp} && {self._bin}-quick strip {tmp} > {stripped} && "
            f"cp {tmp} {self._conf} && {self._bin} syncconf {self._iface} {stripped}", text)

    async def runtime(self) -> dict[str, PeerRuntime]:
        out = await self._exec(f"{self._bin} show {self._iface} dump")
        res = {}
        for line in out.strip().splitlines()[1:]:
            f = line.split("\t")
            if len(f) < 8:
                continue
            res[f[0]] = PeerRuntime(public_key=f[0], endpoint=None if f[2] == "(none)" else f[2],
                                    allowed_ips=f[3], latest_handshake=int(f[4]), rx=int(f[5]), tx=int(f[6]))
        return res

    async def server_public_key(self) -> str:
        return (await self._exec(f"cat {self._dir}/wireguard_server_public_key.key 2>/dev/null || "
                                 f"{self._bin} show {self._iface} public-key")).strip()

    async def preshared_key(self) -> str | None:
        return (await self._exec(f"cat {self._dir}/wireguard_psk.key 2>/dev/null || true")).strip() or None

    async def read_clients_table(self) -> list[dict]:
        raw = (await self._exec(f"cat {self._table} 2>/dev/null || echo []")).strip() or "[]"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    async def write_clients_table(self, table: list[dict]) -> None:
        await self._exec(f"cat > {self._table}.tmp && mv {self._table}.tmp {self._table}",
                         json.dumps(table, indent=4, ensure_ascii=False))

    async def list_backups(self) -> list[str]:
        out = await self._exec(f"ls -1t {self._dir}/backups/ 2>/dev/null || true")
        return [x for x in out.split() if x]

    async def read_backup(self, name: str) -> str:
        if not re.fullmatch(r"[\w.\-]+", name):
            raise ExternalServiceError("Invalid backup name")
        return await self._exec(f"cat {self._dir}/backups/{name}")

    async def versions(self) -> dict:
        out = await self._exec(f"{self._bin} --version 2>&1 | head -1; amneziawg-go --version 2>&1 | head -1")
        lines = out.strip().splitlines()
        return {"tools": lines[0] if lines else "", "go": lines[1] if len(lines) > 1 else ""}
