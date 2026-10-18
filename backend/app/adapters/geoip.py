import asyncio
import datetime
import gzip
import ipaddress
import logging
import os
import shutil
import time

import httpx
import maxminddb

from ..domain.models import GeoPoint
from ..ports import GeoLocator

log = logging.getLogger(__name__)
URL = "https://download.db-ip.com/free/dbip-city-lite-{ym}.mmdb.gz"


class DbIpGeoLocator(GeoLocator):
    def __init__(self, directory: str):
        self._dir = directory
        self._path = os.path.join(directory, "dbip-city-lite.mmdb")
        self._reader: maxminddb.Reader | None = None
        self._cache: dict[str, GeoPoint | None] = {}
        self._mtime = 0.0
        self._checked = 0.0
        self._reload_if_changed()

    def _open(self):
        self._reader = maxminddb.open_database(self._path)
        self._mtime = os.path.getmtime(self._path)
        self._cache.clear()

    def _reload_if_changed(self) -> None:
        now = time.monotonic()
        if now - self._checked < 60:
            return
        self._checked = now
        try:
            if os.path.getmtime(self._path) != self._mtime:
                self._open()
        except OSError:
            pass

    def _install(self, compressed: bytes) -> None:
        """Unpacks a downloaded database next to the current one and puts it in its place."""
        tmp = f"{self._path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(gzip.decompress(compressed))
        shutil.move(tmp, self._path)

    async def refresh(self, max_age_days: int = 35) -> None:
        if os.path.exists(self._path):
            age = datetime.datetime.now().timestamp() - os.path.getmtime(self._path)
            if age < max_age_days * 86400:
                return
        os.makedirs(self._dir, exist_ok=True)
        today = datetime.date.today()
        months = [today, (today.replace(day=1) - datetime.timedelta(days=1))]
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
            for d in months:
                url = URL.format(ym=d.strftime("%Y-%m"))
                try:
                    r = await http.get(url)
                except httpx.HTTPError as e:
                    log.warning("geoip download failed: %s", e)
                    continue
                if r.status_code != 200:
                    continue
                await asyncio.to_thread(self._install, r.content)
                self._open()
                log.info("geoip database updated from %s", url)
                return
        log.warning("geoip database not available")

    def lookup(self, ip: str) -> GeoPoint | None:
        self._reload_if_changed()
        if not self._reader or not ip:
            return None
        if ip in self._cache:
            return self._cache[ip]
        try:
            if ipaddress.ip_address(ip).is_private:
                return None
            rec = self._reader.get(ip) or {}
        except ValueError:
            return None
        loc = rec.get("location") or {}
        country = rec.get("country") or {}
        city = rec.get("city") or {}
        point = GeoPoint(
            country_code=country.get("iso_code"),
            country=(country.get("names") or {}).get("ru") or (country.get("names") or {}).get("en"),
            city=(city.get("names") or {}).get("ru") or (city.get("names") or {}).get("en"),
            lat=loc.get("latitude"), lon=loc.get("longitude"))
        if len(self._cache) > 10000:
            self._cache.clear()
        self._cache[ip] = point
        return point
