"""
Thread-safe Async Event Bus with In-Memory Ring Buffer.
Enables real-time pub/sub event broadcasting across pipeline stages,
background workers, and connected SSE web dashboard clients.
"""

import asyncio
import logging
import threading
from collections import deque
from typing import Set, Optional, List
from shorts_engine.services.events.models import PlatformEvent

logger = logging.getLogger(__name__)


class EventBus:
    """
    Central event distributor with support for asynchronous and synchronous publishers,
    per-client SSE subscribers, and ring-buffered historical event retention.
    """

    def __init__(self, history_size: int = 500):
        self._lock = threading.Lock()
        self._subscribers: Set[asyncio.Queue] = set()
        self._history: deque[PlatformEvent] = deque(maxlen=history_size)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    async def publish(self, event: PlatformEvent) -> None:
        """
        Asynchronously publishes an event to history and broadcasts to all active subscribers.
        Automatically cleans up disconnected or overflowed queues.
        """
        dead_queues = set()
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)

        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.add(q)
            except Exception as e:
                logger.debug("Failed to deliver event to subscriber queue: %s", e)
                dead_queues.add(q)

        if dead_queues:
            with self._lock:
                self._subscribers.difference_update(dead_queues)

    def publish_sync(self, event: PlatformEvent) -> None:
        """
        Synchronously publishes an event from background worker threads or non-async code.
        Directly updates ring buffer history and dispatches to subscriber queues.
        """
        dead_queues = set()
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)

        for q in subs:
            try:
                q.put_nowait(event)
            except (asyncio.QueueFull, Exception):
                dead_queues.add(q)

        if dead_queues:
            with self._lock:
                self._subscribers.difference_update(dead_queues)

    def subscribe(self, max_queue_size: int = 100) -> asyncio.Queue:
        """
        Subscribes a new consumer and returns an asyncio.Queue receiving live PlatformEvents.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """
        Unsubscribes a consumer queue.
        """
        with self._lock:
            self._subscribers.discard(q)

    def get_history(
        self,
        limit: int = 50,
        level: Optional[str] = None,
        category: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> List[PlatformEvent]:
        """
        Retrieves a filtered snapshot of historical events from the ring buffer.
        """
        with self._lock:
            events = list(self._history)

        if level:
            level_upper = level.upper()
            events = [e for e in events if e.level == level_upper]

        if category:
            cat_lower = category.lower()
            events = [e for e in events if e.category == cat_lower]

        if entity_id:
            events = [e for e in events if e.entity_id == entity_id]

        return events[-limit:]

    def clear(self) -> None:
        """Clears all subscribers and history (useful for test isolation)."""
        with self._lock:
            self._subscribers.clear()
            self._history.clear()


# Global singleton event bus
global_event_bus = EventBus()
