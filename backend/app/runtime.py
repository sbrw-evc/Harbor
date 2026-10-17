"""The configuration the panel runs with, read from the settings (services/settings.py) instead of the environment.

``RuntimeConfig`` holds the current values as attributes; services keep the object and read it at call time, so a
change on the settings page applies without a restart. Every process refreshes it every few seconds (the settings
cache lives 15 s) and rebuilds the adapters that are bound to an SSH target when the infrastructure changed.

Installations from before the setup wizard kept these values in .env: they are imported once, when the database has
none of them yet."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

from .services.settings import DEFAULTS, INFRA, SettingsService, validate

log = logging.getLogger(__name__)

REFRESH_SECONDS = 15

# Old .env names of the settings (imported once).
LEGACY_ENV = {k.upper(): k for k in INFRA}


class Rebuildable:
    """A stand-in for an adapter that is built from the settings: attribute access goes to the current instance,
    which ``rebuild`` replaces. Services keep the stand-in and never see the swap."""

    def __init__(self, factory: Callable[["RuntimeConfig"], Any]):
        self._factory = factory
        self._target: Any = None

    def rebuild(self, rc: "RuntimeConfig") -> None:
        self._target = self._factory(rc)

    def __getattr__(self, name: str):
        target = self.__dict__.get("_target")
        if target is None:
            raise AttributeError(name)
        return getattr(target, name)


class RuntimeConfig:
    def __init__(self, settings: SettingsService):
        self._settings = settings
        self._values: dict = {k: DEFAULTS[k] for k in INFRA}
        self._adapters: list[Rebuildable] = []
        self._listeners: list[Callable[["RuntimeConfig"], None]] = []
        self._task: asyncio.Task | None = None
        # Context dicts handed to services that read them by key; updated in place.
        self.routing_ctx: dict = {}
        self.device_ctx: dict = {}
        self.topology_ctx: dict = {}
        self.public_urls: list[str] = []
        self._fill_contexts()

    def __getattr__(self, name: str):
        values = self.__dict__.get("_values", {})
        if name in values:
            return values[name]
        raise AttributeError(name)

    def adapter(self, factory: Callable[["RuntimeConfig"], Any]) -> Rebuildable:
        a = Rebuildable(factory)
        a.rebuild(self)
        self._adapters.append(a)
        return a

    def on_change(self, listener: Callable[["RuntimeConfig"], None]) -> None:
        self._listeners.append(listener)

    def snapshot(self) -> dict:
        return dict(self._values)

    def _fill_contexts(self) -> None:
        v = self._values
        self.routing_ctx.clear()
        self.routing_ctx.update(gateway_ip=v["gateway_ip"], lan_cidr=v["lan_cidr"], vpn_host=v["awg_host"])
        self.device_ctx.clear()
        self.device_ctx.update(router_host=v["router_host"], router_ssh_user=v["router_ssh_user"],
                               collector_host=v["gateway_ip"])
        self.topology_ctx.clear()
        self.topology_ctx.update(vpn_host=v["awg_host"], router_host=v["router_host"])
        self.public_urls[:] = [v["spa_public_url"], v["public_base_url"]]

    async def load(self) -> bool:
        """Reads the settings; rebuilds the adapters and tells the listeners when something changed."""
        values = {k: await self._settings.get(k) for k in INFRA}
        if values == self._values:
            return False
        self._values = values
        self._fill_contexts()
        for a in self._adapters:
            a.rebuild(self)
        for listener in self._listeners:
            try:
                listener(self)
            except Exception:
                log.exception("runtime listener failed")
        return True

    async def import_legacy_env(self, environ: dict[str, str] | None = None) -> list[str]:
        """Copies the old .env values into the settings once: only names the database does not have yet."""
        environ = os.environ if environ is None else environ
        stored = await self._settings.stored()
        found = {key: environ[name] for name, key in LEGACY_ENV.items()
                 if environ.get(name, "").strip() and key not in stored}
        if not found:
            return []
        if "cookie_secure" in found:
            found["cookie_secure"] = found["cookie_secure"].strip().lower() in ("1", "true", "yes", "on")
        good = {}
        for k, v in found.items():
            try:
                good.update(validate({k: v}))
            except ValueError as e:
                log.warning("old .env value of %s is not imported: %s", k, e)
        if good:
            await self._settings.update(good, internal=True)
            log.warning("imported settings from .env (once): %s — they are now changed in the panel; remove them "
                        "from .env", ", ".join(sorted(good)))
        return sorted(good)

    def start(self) -> None:
        async def loop():
            while True:
                await asyncio.sleep(REFRESH_SECONDS)
                try:
                    if await self.load():
                        log.info("settings changed: adapters rebuilt")
                except Exception:
                    log.exception("settings refresh failed")
        self._task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def refresh_now(self) -> bool:
        self._settings.invalidate()
        return await self.load()
