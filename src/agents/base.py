"""Foundation layer for PriceSensei agents.

Every agent (Scout, Analyst, Sensei) inherits from ``BaseAgent`` and reports
progress by pushing structured events onto a shared ``asyncio.Queue``. A
consumer task in the same event loop (later: the SSE endpoint) drains it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

# An event is a flat, JSON-serialisable dict: {"agent", "type", "ts", ...payload}
Event = dict[str, Any]


class BaseAgent:
    """Base class providing non-blocking event emission and step timing."""

    def __init__(self, name: str, event_queue: asyncio.Queue[Event]) -> None:
        """Create an agent.

        Args:
            name: Stable machine name, e.g. ``"search_agent"``. The UI maps it
                to a friendly label via ``config.AGENT_LABELS``.
            event_queue: Shared queue the SSE consumer drains.
        """
        self.name = name
        self._queue = event_queue

    async def emit(self, event_type: str, **payload: Any) -> None:
        """Push a structured event onto the queue without ever blocking.

        Event shape: ``{"agent": name, "type": event_type, "ts": unix_float,
        **payload}``. Reserved keys (``agent``, ``type``, ``ts``) always win
        over same-named payload keys, so a stray kwarg can't spoof them.

        If the queue is full the event is dropped and a warning is logged.
        Progress telemetry must never stall the pipeline doing real work.
        """
        event: Event = {
            **payload,
            "agent": self.name,
            "type": event_type,
            "ts": time.time(),
        }
        try:
            # put_nowait never suspends, so emit can't hang on a slow consumer.
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Event queue full; dropped %r event from %s", event_type, self.name
            )

    @asynccontextmanager
    async def timed_step(self, label: str) -> AsyncIterator[None]:
        """Time a block of work and report its lifecycle as events.

        Emits ``step_started`` on entry. If the body raises, emits
        ``step_failed`` (with error type and message) and re-raises the
        original exception untouched. ``step_complete`` is always emitted
        last, carrying ``elapsed`` seconds and ``ok`` (False on failure), so
        consumers can rely on exactly one terminal event per step.

        Usage::

            async with self.timed_step("Querying Google Shopping"):
                products = await engine.search(query, max_results=10)
        """
        start = time.perf_counter()  # monotonic: immune to wall-clock jumps
        ok = True
        await self.emit("step_started", step=label)
        try:
            yield
        except BaseException as exc:
            # BaseException on purpose: task cancellation (CancelledError)
            # should also surface in the UI. emit() is non-blocking, so it is
            # safe to call here even while the task is being cancelled.
            ok = False
            await self.emit(
                "step_failed",
                step=label,
                error=type(exc).__name__,
                message=str(exc),
            )
            raise
        finally:
            await self.emit(
                "step_complete",
                step=label,
                elapsed=round(time.perf_counter() - start, 3),
                ok=ok,
            )