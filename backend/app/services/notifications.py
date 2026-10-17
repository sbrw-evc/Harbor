import html
import logging

from ..domain.errors import DomainError
from ..domain.models import Severity
from ..ports import Notifier
from .settings import SettingsService

log = logging.getLogger(__name__)
ICONS = {Severity.CRITICAL: "🔴", Severity.WARNING: "🟠", Severity.INFO: "🔵"}
ORDER = [Severity.INFO, Severity.WARNING, Severity.CRITICAL]


class NotificationService:
    def __init__(self, notifier: Notifier, settings: SettingsService):
        self._notifier = notifier
        self._settings = settings

    async def _enabled_for(self, severity: Severity) -> bool:
        st = await self._settings.all()
        if not st["notify_enabled"]:
            return False
        minimum = Severity(st["notify_min_severity"] or "warning")
        return ORDER.index(severity) >= ORDER.index(minimum) and await self._notifier.configured()

    async def alert_raised(self, severity: Severity, title: str, message: str) -> None:
        if await self._enabled_for(severity):
            await self._safe_send(f"{ICONS[severity]} <b>{html.escape(title)}</b>\n{html.escape(message)}")

    async def alert_resolved(self, severity: Severity, title: str) -> None:
        if severity == Severity.CRITICAL and await self._enabled_for(severity):
            await self._safe_send(f"✅ <b>Resolved:</b> {html.escape(title)}")

    async def test(self) -> None:
        if not await self._notifier.configured():
            raise DomainError("Enter the bot token and chat ID")
        await self._notifier.send("✅ <b>Harbor</b>: test notification")

    async def _safe_send(self, text: str) -> None:
        try:
            await self._notifier.send(text)
        except DomainError as e:
            log.warning("notification failed: %s", e.message)
