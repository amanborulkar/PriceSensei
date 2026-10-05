"""Scout: queries each shopping engine in turn and merges the results."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from src.cache.cache_manager import CacheManager
from src.config import CREDIT_WARNING_THRESHOLD, MONTHLY_CREDIT_LIMIT
from src.engines.bing_shopping import BingShoppingEngine
from src.engines.google_shopping import GoogleShoppingEngine
from src.models.product import Product

from .base import BaseAgent, Event

logger = logging.getLogger(__name__)


class SearchAgent(BaseAgent):
    """Fetches products from every configured engine, cache-first."""

    def __init__(
        self,
        event_queue: asyncio.Queue[Event],
        cache: Optional[CacheManager] = None,
    ) -> None:
        """Create Scout.

        Args:
            event_queue: Shared queue drained by the SSE consumer.
            cache: Optional cache; a default ``CacheManager`` is created if None.
        """
        super().__init__("search_agent", event_queue)
        self._cache = cache if cache is not None else CacheManager()
        self._engines: dict[str, Any] = {
            e.name: e for e in (GoogleShoppingEngine(), BingShoppingEngine())
        }

    async def run(
        self, query: str, engines: Optional[list[str]] = None
    ) -> list[Product]:
        """Search the engines sequentially and return merged, deduped products.

        Engines run one after another (not gathered) so SSE event order is
        deterministic. A failing engine emits ``engine_failed`` and is skipped.

        Args:
            query: Product name, e.g. ``"iPhone 15 128GB"``.
            engines: Engine names to use; None means all.

        Raises:
            ValueError: If an unknown engine name is requested.
            RuntimeError: If the monthly SerpApi credit limit is exhausted.
        """
        names = self._resolve_engines(engines)
        await self.emit("search_started", query=query, engines=names)
        await self._check_credits()

        results: dict[str, list[Product]] = {}
        for name in names:
            engine = self._engines[name]
            try:
                # try sits outside the step so timed_step reports step_failed
                # before we convert the error into an engine_failed event.
                async with self.timed_step(f"Querying {engine.label}"):
                    results[name] = await self._fetch(engine, query)
            except Exception as e:  # Exception, not BaseException: let cancellation through
                logger.warning("Engine %s failed for %r: %s", name, query, e)
                results[name] = []
                await self.emit(
                    "engine_failed",
                    engine=name,
                    error=str(e),
                    error_type=type(e).__name__,
                )

        # Merge in engine order; drop invalid items, keep first of each duplicate.
        merged: list[Product] = []
        by_engine: dict[str, int] = {name: 0 for name in names}
        seen: set[tuple[str, float]] = set()
        for name in names:
            for p in results[name]:
                if not p.is_valid:
                    continue
                key = (p.title.lower().strip(), round(p.price, 2))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(p)
                by_engine[name] += 1

        await self.emit(
            "search_complete",
            query=query,
            total=len(merged),
            by_engine=by_engine,
            credits=self._cache.get_credit_status(),
        )
        return merged

    def _resolve_engines(self, engines: Optional[list[str]]) -> list[str]:
        """Validate requested engine names; None selects all, in config order."""
        if engines is None:
            return list(self._engines)
        unknown = [n for n in engines if n not in self._engines]
        if unknown:
            raise ValueError(f"Unknown engine(s): {', '.join(unknown)}")
        return list(dict.fromkeys(engines))  # de-dupe, keep requested order

    async def _check_credits(self) -> None:
        """Abort when credits are exhausted; warn when running low."""
        status = self._cache.get_credit_status()
        if status["used"] >= MONTHLY_CREDIT_LIMIT:
            await self.emit("credit_exhausted", **status)
            raise RuntimeError("SerpApi credits exhausted")
        if status["used"] >= CREDIT_WARNING_THRESHOLD:
            await self.emit("credit_warning", **status)

    async def _fetch(self, engine: Any, query: str) -> list[Product]:
        """Return products for one engine: cache first, then a live search."""
        cached = self._cache.get(engine.name, query)
        if cached is not None:
            await self.emit("cache_hit", engine=engine.name, count=len(cached))
            return cached

        await self.emit("cache_miss", engine=engine.name)
        # engine.search is blocking I/O: run it in a worker thread so the
        # event loop (and SSE streaming) stays responsive.
        products: list[Product] = await asyncio.to_thread(engine.search, query)

        prices = [p.price for p in products if p.is_valid]
        await self.emit(
            "engine_results",
            engine=engine.name,
            count=len(products),
            min_price=min(prices) if prices else None,
        )
        try:
            self._cache.set(engine.name, query, products)
        except Exception as e:  # a cache write failure must not discard fresh results
            logger.warning("Cache write failed for %s: %s", engine.name, e)
        return products