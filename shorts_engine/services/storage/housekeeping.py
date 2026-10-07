"""
Automatic Temp Housekeeping & Storage Maintenance Daemon (Enhancement 10).
Periodically scrubs orphaned *.ass, *.wav, and temporary render files
as well as stale download fragments from YouTube CDN interruptions.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from shorts_engine.config.settings import settings
from shorts_engine.services.ingestion.downloader import IngestionService

logger = logging.getLogger(__name__)


class HousekeepingDaemon:
    """
    Async background worker that periodically runs cleanup of stale intermediate files
    and download fragments to guarantee low disk footprint and prevent storage exhaustion.
    """

    def __init__(self, interval_seconds: Optional[int] = None):
        self.interval = interval_seconds or getattr(settings, "HOUSEKEEPING_INTERVAL_SECONDS", 3600)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("HousekeepingDaemon started with %ds interval.", self.interval)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("HousekeepingDaemon stopped.")

    def run_cycle(self) -> Dict[str, Any]:
        """
        Executes one housekeeping pass:
        1. Purges stale temp files older than 24h (*.ass, *.wav, *.tmp).
        2. Purges stale download fragments older than 2h (*.part*, *.temp*).
        """
        temp_purged = IngestionService.purge_stale_temp_files(max_age_hours=24.0)
        frag_purged = IngestionService.purge_stale_fragments(max_age_hours=2.0)
        return {
            "stale_temp_purged": temp_purged,
            "stale_fragments_purged": frag_purged,
        }

    async def _run_loop(self) -> None:
        while self._running:
            try:
                results = self.run_cycle()
                if results["stale_temp_purged"] > 0 or results["stale_fragments_purged"] > 0:
                    logger.info("Housekeeping cycle completed: %s", results)
                else:
                    logger.debug("Housekeeping cycle completed: zero stale files found.")
            except Exception as e:
                logger.error("Error in HousekeepingDaemon run loop: %s", e)

            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
