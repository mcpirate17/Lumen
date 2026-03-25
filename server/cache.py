"""Background data cache for Lumen domain agents.

Caches finance, sports, and news snapshots in memory with periodic refresh.
Router reads from cache (instant) instead of fetching per-request (5-12s).

Refresh intervals:
  - Finance: every 10 minutes (crypto moves fast)
  - Sports:  every 2 minutes during live games, every 15 minutes otherwise
  - News:    every 15 minutes
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

log = logging.getLogger("lumen.cache")


class CacheState(str, Enum):
    IDLE = "idle"
    REFRESHING = "refreshing"
    ERROR = "error"


@dataclass
class CacheEntry:
    data: object = None
    text: str = ""
    last_updated: float = 0  # monotonic time
    last_updated_utc: str = ""
    state: CacheState = CacheState.IDLE
    error: str = ""
    refresh_count: int = 0
    last_duration_ms: int = 0


class DataCache:
    """In-memory cache for domain agent data with background refresh."""

    def __init__(self):
        self.finance = CacheEntry()
        self.sports = CacheEntry()
        self.news = CacheEntry()
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._game_live = False

        # Refresh intervals in seconds
        self.finance_interval = 600    # 10 minutes
        self.sports_interval = 900     # 15 minutes (2 min during live games)
        self.sports_live_interval = 120  # 2 minutes during live games
        self.news_interval = 900       # 15 minutes

        # Scanner intervals
        self.quick_scan_interval = 1800    # 30 minutes
        self.watchlist_scan_interval = 900  # 15 minutes
        self.full_scan_interval = 14400     # 4 hours

    def start(self):
        """Start background refresh tasks. Call during app startup."""
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._refresh_loop("finance", self._refresh_finance)),
            asyncio.create_task(self._refresh_loop("sports", self._refresh_sports)),
            asyncio.create_task(self._refresh_loop("news", self._refresh_news)),
            asyncio.create_task(self._scan_loop("quick_scan", self._run_quick_scan, self.quick_scan_interval)),
            asyncio.create_task(self._scan_loop("watchlist_scan", self._run_watchlist_scan, self.watchlist_scan_interval)),
            asyncio.create_task(self._scan_loop("full_scan", self._run_full_scan, self.full_scan_interval)),
        ]
        log.info("[CACHE] Background refresh + scanners started")

    async def stop(self):
        """Stop all background refresh tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("[CACHE] Background refresh stopped")

    def get_status(self) -> dict:
        """Get cache status for all domains."""
        now = time.monotonic()
        return {
            "finance": self._entry_status(self.finance, now),
            "sports": self._entry_status(self.sports, now),
            "news": self._entry_status(self.news, now),
            "game_live": self._game_live,
        }

    def _entry_status(self, entry: CacheEntry, now: float) -> dict:
        age_s = int(now - entry.last_updated) if entry.last_updated > 0 else -1
        return {
            "state": entry.state.value,
            "age_seconds": age_s,
            "last_updated": entry.last_updated_utc,
            "refresh_count": entry.refresh_count,
            "last_duration_ms": entry.last_duration_ms,
            "has_data": entry.data is not None,
            "error": entry.error,
        }

    # -- Finance --

    async def _refresh_finance(self):
        """Fetch fresh finance data."""
        from agents.finance.collector import collect_all, snapshot_to_text
        self.finance.state = CacheState.REFRESHING
        t0 = time.monotonic()
        try:
            snapshot = await asyncio.wait_for(collect_all(), timeout=30.0)
            self.finance.data = snapshot
            self.finance.text = snapshot_to_text(snapshot)
            self.finance.state = CacheState.IDLE
            self.finance.error = ""
            ms = int((time.monotonic() - t0) * 1000)
            self.finance.last_duration_ms = ms
            self.finance.last_updated = time.monotonic()
            self.finance.last_updated_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            self.finance.refresh_count += 1
            log.info("[CACHE] Finance refreshed (%dms, %d crypto)", ms, len(snapshot.crypto))
        except Exception as e:
            self.finance.state = CacheState.ERROR
            self.finance.error = str(e)
            log.warning("[CACHE] Finance refresh failed: %s", e)

    # -- Sports --

    async def _refresh_sports(self):
        """Fetch fresh sports data."""
        from agents.sports.scores import get_philly_snapshot, snapshot_to_text
        self.sports.state = CacheState.REFRESHING
        t0 = time.monotonic()
        try:
            snapshot = await asyncio.wait_for(get_philly_snapshot(), timeout=20.0)
            self.sports.data = snapshot
            self.sports.text = snapshot_to_text(snapshot)
            self.sports.state = CacheState.IDLE
            self.sports.error = ""
            ms = int((time.monotonic() - t0) * 1000)
            self.sports.last_duration_ms = ms
            self.sports.last_updated = time.monotonic()
            self.sports.last_updated_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            self.sports.refresh_count += 1

            # Check if any game is live — if so, refresh faster
            had_live = self._game_live
            self._game_live = any(g.status == "in" for g in snapshot.games_today)
            if self._game_live and not had_live:
                log.info("[CACHE] Live game detected — switching to fast refresh (2m)")
            elif had_live and not self._game_live:
                log.info("[CACHE] No more live games — switching to normal refresh (15m)")

            log.info("[CACHE] Sports refreshed (%dms, %d games, live=%s)",
                     ms, len(snapshot.games_today), self._game_live)
        except Exception as e:
            self.sports.state = CacheState.ERROR
            self.sports.error = str(e)
            log.warning("[CACHE] Sports refresh failed: %s", e)

    # -- News --

    async def _refresh_news(self):
        """Fetch fresh news data."""
        from agents.news.aggregator import get_all_news, news_to_text
        self.news.state = CacheState.REFRESHING
        t0 = time.monotonic()
        try:
            items = await asyncio.wait_for(get_all_news(hn_count=20), timeout=30.0)
            self.news.data = items
            self.news.text = news_to_text(items)
            self.news.state = CacheState.IDLE
            self.news.error = ""
            ms = int((time.monotonic() - t0) * 1000)
            self.news.last_duration_ms = ms
            self.news.last_updated = time.monotonic()
            self.news.last_updated_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            self.news.refresh_count += 1
            log.info("[CACHE] News refreshed (%dms, %d items)", ms, len(items))
        except Exception as e:
            self.news.state = CacheState.ERROR
            self.news.error = str(e)
            log.warning("[CACHE] News refresh failed: %s", e)

    # -- Scanners --

    async def _run_quick_scan(self):
        """Run a quick market scan (sector ETFs + watchlist + top crypto)."""
        from agents.finance.scanner import quick_scan
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(quick_scan(), timeout=120.0)
            ms = int((time.monotonic() - t0) * 1000)
            log.info("[SCANNER] Quick scan done (%dms, %d findings)",
                     ms, result.get("new_findings", 0))
        except Exception as e:
            log.warning("[SCANNER] Quick scan failed: %s", e)

    async def _run_watchlist_scan(self):
        """Scan just the watchlist."""
        from agents.finance.scanner import watchlist_scan
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(watchlist_scan(), timeout=60.0)
            ms = int((time.monotonic() - t0) * 1000)
            log.info("[SCANNER] Watchlist scan done (%dms, %d findings)",
                     ms, result.get("new_findings", 0))
        except Exception as e:
            log.warning("[SCANNER] Watchlist scan failed: %s", e)

    async def _run_full_scan(self):
        """Full market scan with deep analysis."""
        from agents.finance.scanner import full_scan
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(full_scan(), timeout=600.0)
            ms = int((time.monotonic() - t0) * 1000)
            log.info("[SCANNER] Full scan done (%dms, %d findings)",
                     ms, result.get("new_findings", 0))
        except Exception as e:
            log.warning("[SCANNER] Full scan failed: %s", e)

    async def _scan_loop(self, name: str, scan_fn, interval: int):
        """Run a scanner on a fixed interval. Delays first run to let caches warm up."""
        # Wait 60s after startup before first scan (let data caches fill first)
        await asyncio.sleep(60)
        while self._running:
            try:
                await scan_fn()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[SCANNER] %s loop error: %s", name, e)
                await asyncio.sleep(60)

    # -- Refresh loop --

    async def _refresh_loop(self, name: str, refresh_fn):
        """Run a refresh function on a loop with adaptive intervals."""
        # Initial fetch immediately on startup
        await refresh_fn()

        while self._running:
            try:
                # Determine sleep interval
                if name == "finance":
                    interval = self.finance_interval
                elif name == "sports":
                    interval = self.sports_live_interval if self._game_live else self.sports_interval
                elif name == "news":
                    interval = self.news_interval
                else:
                    interval = 600

                await asyncio.sleep(interval)

                if self._running:
                    await refresh_fn()

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[CACHE] %s loop error: %s", name, e)
                await asyncio.sleep(30)  # back off on error


# Singleton
cache = DataCache()
