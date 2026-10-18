import asyncio
import os

import asyncssh

from ..domain.errors import ExternalServiceError
from ..ports import CommandRunner, SecretStore

KEY_SECRET = "infra/ssh"


class SshKeyProvider:
    def __init__(self, secrets: SecretStore, path: str = "/run/panel/ssh_key"):
        self._secrets = secrets
        self._path = path
        self._ready = False

    async def ensure(self) -> str:
        """The public key of the panel (OpenSSH format); the key pair is created and kept in OpenBao when missing."""
        data = await self._secrets.get(KEY_SECRET)
        if not data or "private_key" not in data:
            key = asyncssh.generate_private_key("ssh-ed25519", comment="network-control")
            data = {"private_key": key.export_private_key().decode(),
                    "public_key": key.export_public_key().decode().strip()}
            await self._secrets.put(KEY_SECRET, data)
            self._ready = False
        if not data.get("public_key"):
            key = asyncssh.import_private_key(data["private_key"])
            data["public_key"] = key.export_public_key().decode().strip()
            await self._secrets.put(KEY_SECRET, data)
        return data["public_key"]

    async def path(self) -> str:
        if not self._ready:
            data = await self._secrets.get(KEY_SECRET)
            if not data or "private_key" not in data:
                raise ExternalServiceError("SSH key not found in OpenBao (infra/ssh)")
            await asyncio.to_thread(self._write, data["private_key"])
            self._ready = True
        return self._path

    def _write(self, private_key: str) -> None:
        """The key file for ssh: readable by the panel only, replaced in one step."""
        os.makedirs(os.path.dirname(self._path), mode=0o700, exist_ok=True)
        tmp = f"{self._path}.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(private_key.rstrip("\n") + "\n")
        os.replace(tmp, self._path)


class SshRunner(CommandRunner):
    def __init__(self, host: str, user: str, keys: SshKeyProvider, known_hosts: str):
        self._target = f"{user}@{host}"
        self._keys = keys
        self._known_hosts = known_hosts
        self._host = host

    async def run(self, command: str, stdin: str | None = None, timeout: float = 30) -> str:
        if not self._host:
            raise ExternalServiceError("The address is not set: fill it in on the settings page")
        key = await self._keys.path()
        args = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={self._known_hosts}", "-o", "ConnectTimeout=10",
                "-o", "ControlMaster=auto", "-o", f"ControlPath=/run/panel/cm-%r@%h",
                "-o", "ControlPersist=300", "-o", "ServerAliveInterval=15", self._target, command]
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin.encode() if stdin is not None else None), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ExternalServiceError(f"{self._host}: SSH timed out")
        if proc.returncode != 0:
            msg = (err or out).decode(errors="replace").strip()
            raise ExternalServiceError(f"{self._host}: {msg or 'ssh exit ' + str(proc.returncode)}")
        return out.decode(errors="replace")
