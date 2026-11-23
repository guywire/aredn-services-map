"""
The MeshPhone directory, fetched over the mesh: the WT0F MeshPhone offices
(names, codes, number patterns, trunks) and the newest WhitePages copy that
answers. Refreshed once a day and cached on disk so a restart has data at once.

Config (meshphone section, all optional; see meshphone_directory.settings):
  offices_url, whitepages_urls, dns_server
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from urllib.parse import urlsplit

from aredn_services_map import meshphone_directory as mp

log = logging.getLogger(__name__)

REFRESH_S = 24 * 3600
RETRY_S = 600


class Directory:
    def __init__(self) -> None:
        self.mp_offices: list[dict] = []
        self.mp_people: list[dict] = []
        self.mp_people_created = ""
        self.mp_people_source = ""
        self.mp_offices_refreshed_at: float | None = None
        self.mp_people_refreshed_at: float | None = None
        self.errors: list[str] = []
        self._config: dict = {}
        self._task: asyncio.Task | None = None
        self.cache_path: str | None = None

    def configure(self, config: dict) -> None:
        self._config = config or {}
        db_path = (self._config.get("database") or {}).get("path", "aredn-services-map.db")
        base = os.path.dirname(os.path.abspath(db_path)) if db_path != ":memory:" else os.getcwd()
        self.cache_path = os.path.join(base, "meshphone_directory.json")
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                d = json.load(f)
            self.mp_offices = d.get("offices") or []
            self.mp_people = d.get("people") or []
            self.mp_people_created = d.get("people_created", "")
            self.mp_offices_refreshed_at = d.get("offices_refreshed_at")
        except (OSError, ValueError, AttributeError):
            pass

    async def start(self, config: dict) -> None:
        self.configure(config)
        if mp.settings(self._config)["enabled"] and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        # a cached copy from less than a day ago: wait out the rest of the day first
        age = time.time() - (self.mp_offices_refreshed_at or 0)
        if self.mp_offices and age < REFRESH_S:
            await asyncio.sleep(REFRESH_S - age)
        while True:
            try:
                self.errors = await self.refresh()
            except Exception as exc:
                self.errors = [f"{exc.__class__.__name__}: {exc}"]
            if self.errors:
                log.info("MeshPhone directory: %s", "; ".join(self.errors))
            await asyncio.sleep(RETRY_S if self.errors and not self.mp_offices else REFRESH_S)

    async def refresh(self) -> list[str]:
        """Offices + white pages over the mesh. Returns errors (empty when all is well)."""
        import httpx
        cfg = mp.settings(self._config)
        errors = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                     headers={"User-Agent": "aredn-services-map"}) as client:
            try:
                url, hdrs = await asyncio.to_thread(mp.resolve_url, cfg["offices_url"], cfg["dns_server"])
                r = await client.get(url, headers=hdrs)
                r.raise_for_status()
                offices = mp.parse_offices(r.json())
                if not offices:
                    raise ValueError("no offices in the list")
                self.mp_offices, self.mp_offices_refreshed_at = offices, time.time()
            except Exception as exc:
                errors.append(f"MeshPhone offices: {exc}")
            best, wp_errors = None, []
            for wp_url in cfg["whitepages_urls"]:
                try:
                    url, hdrs = await asyncio.to_thread(mp.resolve_url, wp_url, cfg["dns_server"])
                    r = await client.get(url, headers=hdrs)
                    r.raise_for_status()
                    people, created = mp.parse_whitepages(r.text)
                    if len(people) < 10:
                        raise ValueError(f"only {len(people)} listings")
                    if best is None or created > best[0]:
                        best = (created, people, wp_url)
                except Exception as exc:
                    wp_errors.append(f"{urlsplit(wp_url).hostname}: {exc}")
            if best:
                self.mp_people_created, self.mp_people = best[0], best[1]
                self.mp_people_source = urlsplit(best[2]).hostname or ""
                self.mp_people_refreshed_at = time.time()
            elif wp_errors:
                errors.append("MeshPhone white pages: " + "; ".join(wp_errors))
        if self.cache_path and (self.mp_offices or self.mp_people):
            try:
                tmp = self.cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"offices": self.mp_offices, "people": self.mp_people,
                               "people_created": self.mp_people_created,
                               "offices_refreshed_at": self.mp_offices_refreshed_at}, f)
                os.replace(tmp, self.cache_path)
            except OSError as exc:
                log.debug("MeshPhone directory: cache write failed: %s", exc)
        return errors

    def meshphone(self) -> dict:
        return {"offices": self.mp_offices, "people": self.mp_people, "people_created": self.mp_people_created}


status = Directory()
