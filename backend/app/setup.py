"""The life of the process before and after the setup wizard: it runs in setup mode until bootstrap.json exists, then
builds the container and starts it. Every process watches for the file, so the one that did not serve the storage step
follows within seconds, or at once when it is asked for the stage."""
from __future__ import annotations

import asyncio
import logging

from .bootstrap import Bootstrap, SetupGate
from .config import Settings, legacy_environ
from .container import Container
from .db.repositories import UserRepo
from .services.identity.spa import MODE_KEYSTONE, MODE_LOCAL

log = logging.getLogger(__name__)

WATCH_SECONDS = 3
STAGE_STORAGE = "storage"
STAGE_ACCOUNT = "account"
STAGE_DONE = "done"


class SetupState:
    def __init__(self, cfg: Settings, app_state):
        self.cfg = cfg
        self._app = app_state
        self.boot = Bootstrap(cfg.data_dir, cfg.bao_approle_dir)
        self.gate = SetupGate(cfg.data_dir)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._done = False
        self.error: str | None = None

    @property
    def container(self) -> Container | None:
        return getattr(self._app, "container", None)

    async def activate(self) -> bool:
        """Builds and starts the container when bootstrap.json is there. Returns whether it runs."""
        async with self._lock:
            if self.container is not None:
                return True
            if not self.boot.exists():
                try:
                    await self.boot.import_legacy(legacy_environ())
                except Exception as e:
                    log.warning("old storage settings are not taken over yet: %s", e)
            if not self.boot.exists():
                return False
            try:
                storage = await self.boot.resolve()
                c = Container(self.cfg, storage)
                await c.start()
            except Exception as e:  # OpenBao or PostgreSQL not up yet: the watcher tries again
                self.error = str(e) or type(e).__name__
                log.warning("storage is not reachable yet: %s", self.error)
                return False
            self.error = None
            self._app.container = c
            log.info("Harbor started")
            if await self.stage() != STAGE_DONE:
                self.gate.announce()
            return True

    async def stage(self) -> str:
        if self._done:  # a finished wizard never comes back
            return STAGE_DONE
        c = self.container
        if c is None and self.boot.exists():
            # Another process has just served the storage step: start on it now rather than on the next tick of the
            # watcher, so that no process answers "storage" after the wizard has moved on.
            await self.activate()
            c = self.container
        if c is None:
            return STAGE_STORAGE
        # The sign-in step may have been served by another process: its settings are read again, not from the cache.
        c.settings.invalidate()
        mode = await c.settings.get("auth_mode")
        if mode == MODE_LOCAL:
            async with c.db.session() as s:
                self._done = bool(await UserRepo(s).count_admins())
        else:
            self._done = mode == MODE_KEYSTONE
        return STAGE_DONE if self._done else STAGE_ACCOUNT

    async def start(self) -> None:
        if not await self.activate():
            self.gate.announce()

        async def watch():
            while self.container is None:
                await asyncio.sleep(WATCH_SECONDS)
                await self.activate()
        if self.container is None:
            self._task = asyncio.create_task(watch(), name="setup-watch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.container is not None:
            await self.container.stop()


__all__ = ["STAGE_ACCOUNT", "STAGE_DONE", "STAGE_STORAGE", "SetupState"]
