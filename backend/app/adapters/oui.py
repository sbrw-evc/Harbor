"""MAC address vendor lookup (IEEE OUI registry downloaded at run time, with a small built-in fallback)."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)
URL = "https://standards-oui.ieee.org/oui/oui.csv"
BUILTIN = {
    "00:0c:42": "MikroTik", "48:8f:5a": "MikroTik", "64:d1:54": "MikroTik", "b8:69:f4": "MikroTik", "dc:2c:6e": "MikroTik",
    "18:fd:74": "MikroTik", "2c:c8:1b": "MikroTik", "c4:ad:34": "MikroTik", "74:4d:28": "MikroTik", "d4:01:c3": "MikroTik",
    "00:1b:21": "Intel", "3c:22:fb": "Apple", "f0:18:98": "Apple", "a4:83:e7": "Apple", "bc:d0:74": "Apple", "8c:85:90": "Apple",
    "f4:f5:d8": "Google", "3c:5a:b4": "Google", "00:1a:11": "Google", "e4:5f:01": "Raspberry Pi", "dc:a6:32": "Raspberry Pi",
    "b8:27:eb": "Raspberry Pi", "28:6c:07": "Xiaomi", "64:09:80": "Xiaomi", "78:11:dc": "Xiaomi", "50:ec:50": "Xiaomi",
    "8c:79:f5": "Samsung", "5c:49:7d": "Samsung", "f4:7b:09": "Samsung", "00:15:5d": "Microsoft Hyper-V", "00:50:56": "VMware",
    "52:54:00": "QEMU / KVM", "08:00:27": "VirtualBox", "02:42:ac": "Docker", "ac:84:c6": "TP-Link", "50:c7:bf": "TP-Link",
    "98:da:c4": "TP-Link", "60:32:b1": "TP-Link", "f4:f2:6d": "TP-Link", "24:5a:4c": "Ubiquiti", "74:ac:b9": "Ubiquiti",
    "f0:9f:c2": "Ubiquiti", "78:8a:20": "Ubiquiti", "00:1d:aa": "DrayTek", "50:ff:20": "Keenetic", "e8:37:7a": "Zyxel",
    "00:13:49": "Zyxel", "00:1e:13": "Cisco", "00:26:0b": "Cisco", "70:10:5c": "Cisco", "00:0b:86": "Aruba", "94:b4:0f": "Aruba",
    "3c:a8:2a": "HP", "00:25:b3": "HP", "14:18:77": "Dell", "f8:bc:12": "Dell", "00:1f:12": "Juniper", "28:8a:1c": "Juniper",
    "00:e0:fc": "Huawei", "48:46:fb": "Huawei", "00:09:0f": "Fortinet", "90:6c:ac": "Fortinet", "a8:f9:4b": "Eltex",
    "18:31:bf": "ASUS", "04:d9:f5": "ASUS", "00:11:32": "Synology", "24:5e:be": "QNAP", "9c:8e:cd": "Amcrest",
    "c0:56:27": "Belkin", "44:65:0d": "Amazon", "fc:65:de": "Amazon", "a0:02:dc": "Amazon", "b0:be:76": "TP-Link",
    "d8:3a:dd": "Raspberry Pi", "00:17:88": "Philips Hue", "ec:fa:bc": "Espressif", "24:0a:c4": "Espressif",
    "84:f3:eb": "Espressif", "bc:ff:4d": "Espressif", "70:ee:50": "Netatmo", "18:b4:30": "Nest", "64:16:66": "Nest",
    "00:04:20": "Logitech (Harmony)", "c8:3a:6b": "Roku", "b0:a7:37": "Roku", "40:b0:76": "ASUS", "00:24:e4": "Withings",
    "a4:77:33": "Google", "54:60:09": "Google", "ac:63:be": "Amazon", "00:fc:8b": "Amazon", "70:3a:cb": "Google",
    "f0:b4:29": "Xiaomi", "7c:49:eb": "Xiaomi", "98:fa:9b": "LG", "a8:23:fe": "LG", "00:e0:4c": "Realtek",
    "00:1c:42": "Parallels", "0c:47:c9": "Amazon", "e0:d5:5e": "Gigabyte", "1c:69:7a": "Elitegroup", "60:a4:4c": "ASUS",
    "d0:50:99": "ASRock", "00:23:24": "Apple", "40:a6:b7": "Hikvision", "c0:51:7e": "Hikvision", "4c:bd:8f": "Hikvision",
    "e0:50:8b": "Dahua", "3c:ef:8c": "Dahua", "00:12:fb": "Samsung", "cc:2d:e0": "Routerboard (MikroTik)",
}


def is_random(mac: str) -> bool:
    """Locally administered (randomised / private Wi-Fi) address: the second bit of the first octet is set."""
    try:
        return bool(int(mac[:2], 16) & 0x02)
    except ValueError:
        return False


class OuiDatabase:
    def __init__(self, directory: str):
        self._path = os.path.join(directory, "oui.tsv")
        self._map: dict[str, str] = dict(BUILTIN)

    def load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.rstrip("\n").partition("\t")
                    if v:
                        self._map[k] = v

    async def refresh(self) -> None:
        if os.path.exists(self._path) and time.time() - os.path.getmtime(self._path) < 30 * 86400:
            self.load()
            return
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True,
                                         headers={"User-Agent": "NetworkControl/1.0"}) as http:
                r = await http.get(URL)
                r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("OUI download failed: %s", e)
            self.load()
            return
        rows = await asyncio.to_thread(self._parse, r.text)
        await asyncio.to_thread(self._save, rows)
        self._map.update(rows)
        log.info("OUI database: %d vendors", len(rows))

    def _save(self, rows: dict[str, str]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(f"{k}\t{v}\n" for k, v in rows.items())
        os.replace(self._path + ".tmp", self._path)

    @staticmethod
    def _parse(text: str) -> dict[str, str]:
        out = {}
        for row in csv.reader(io.StringIO(text)):
            if len(row) >= 3 and len(row[1]) == 6:
                h = row[1].lower()
                out[f"{h[0:2]}:{h[2:4]}:{h[4:6]}"] = row[2].strip()[:120]
        return out

    def vendor(self, mac: str) -> str:
        if not mac:
            return ""
        v = self._map.get(mac[:8].lower(), "")
        if not v and is_random(mac):
            return "Random MAC"
        return v
