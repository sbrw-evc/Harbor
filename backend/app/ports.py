from __future__ import annotations

from abc import ABC, abstractmethod

from .domain.models import ClientBundle, GeoPoint, NewPeer, PeerEntry, PeerRuntime


class SecretStore(ABC):
    @abstractmethod
    async def get(self, path: str) -> dict | None: ...

    @abstractmethod
    async def put(self, path: str, data: dict) -> None: ...

    @abstractmethod
    async def delete(self, path: str) -> None: ...

    @abstractmethod
    async def healthy(self) -> bool: ...

    async def keys(self, prefix: str) -> list[str]:
        """The names right under a folder (``users/`` → ``["1/", "2/"]``); stores that cannot list return none."""
        return []


class CommandRunner(ABC):
    @abstractmethod
    async def run(self, command: str, stdin: str | None = None, timeout: float = 30) -> str: ...


class VpnServerGateway(ABC):
    @abstractmethod
    async def read_config(self) -> str: ...

    @abstractmethod
    async def apply_config(self, text: str) -> None:
        ...

    @abstractmethod
    async def runtime(self) -> dict[str, PeerRuntime]: ...

    @abstractmethod
    async def server_public_key(self) -> str: ...

    @abstractmethod
    async def preshared_key(self) -> str | None: ...

    @abstractmethod
    async def read_clients_table(self) -> list[dict]: ...

    @abstractmethod
    async def write_clients_table(self, table: list[dict]) -> None: ...

    @abstractmethod
    async def list_backups(self) -> list[str]: ...

    @abstractmethod
    async def read_backup(self, name: str) -> str: ...

    @abstractmethod
    async def versions(self) -> dict: ...


class GeoLocator(ABC):
    @abstractmethod
    def lookup(self, ip: str) -> GeoPoint | None: ...


class RouterGateway(ABC):
    @abstractmethod
    async def status(self) -> dict: ...

    @abstractmethod
    async def replace_address_list(self, cidrs: list[str]) -> int: ...

    @abstractmethod
    async def list_addresses(self) -> list[str]: ...

    @abstractmethod
    async def add_address(self, cidr: str) -> None: ...

    @abstractmethod
    async def remove_address(self, cidr: str) -> None: ...


class Notifier(ABC):
    @abstractmethod
    async def send(self, text: str) -> None: ...

    @abstractmethod
    async def configured(self) -> bool: ...


class ConfigCodec(ABC):
    @abstractmethod
    def parse(self, text: str) -> tuple[dict, list[str], list[PeerEntry]]: ...

    @abstractmethod
    def render(self, interface_lines: list[str], peers: list[PeerEntry]) -> str: ...


class VpnBackend(ABC):
    kind: str
    title: str
    alert_key: str

    async def configured(self) -> bool:
        """Whether the server is set in the settings at all (cheap: no connection)."""
        return True

    @abstractmethod
    async def ready(self) -> bool: ...

    @abstractmethod
    async def runtime(self) -> dict[str, PeerRuntime]: ...

    @abstractmethod
    async def peers(self) -> list[PeerEntry]: ...

    @abstractmethod
    async def names(self) -> dict[str, str]: ...

    @abstractmethod
    async def add_peer(self, public_key: str, name: str) -> NewPeer: ...

    @abstractmethod
    async def rename_peer(self, public_key: str, name: str, address: str) -> None: ...

    @abstractmethod
    async def set_disabled(self, public_key: str, disabled: bool) -> None: ...

    @abstractmethod
    async def rekey_peer(self, old_key: str, new_key: str) -> None: ...

    @abstractmethod
    async def remove_peer(self, public_key: str) -> None: ...

    @abstractmethod
    async def bundle(self, *, name: str, private_key: str, public_key: str, address: str,
                     preshared_key: str | None) -> ClientBundle: ...
