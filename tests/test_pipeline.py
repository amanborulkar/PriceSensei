"""End-to-end pipeline tests using mock search agents (no API keys needed).

Run:  python -m tests.test_pipeline [--verbose]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Callable, Optional

from src.agents.base import BaseAgent
from src.models.product import Product
from src.orchestrator import (
    PipelineEvent,
    PipelineResult,
    run_pipeline,
    run_pipeline_streaming,
)

VERBOSE = False


# --------------------------------------------------------------------------- #
# Mock search agent
# --------------------------------------------------------------------------- #
def make_mock_search_agent(products: list[Product]) -> Callable[[asyncio.Queue], Any]:
    """Return a factory that builds a mock SearchAgent emitting realistic events."""

    class MockSearchAgent(BaseAgent):
        """Search agent stand-in that returns canned products."""

        def __init__(self, q: asyncio.Queue) -> None:
            super().__init__("search_agent", q)

        async def run(
            self, query: str, engines: Optional[list[str]] = None
        ) -> list[Product]:
            await self.emit(
                "search_started",
                query=query,
                engines=engines or ["google_shopping", "bing_shopping"],
            )
            await self.emit("cache_miss", engine="google_shopping")
            await self.emit(
                "engine_results",
                engine="google_shopping",
                count=len(products),
                min_price=min(p.price for p in products) if products else None,
            )
            await self.emit("cache_miss", engine="bing_shopping")
            await self.emit("engine_results", engine="bing_shopping", count=0, min_price=None)
            await self.emit(
                "search_complete",
                query=query,
                total=len(products),
                by_engine={"google_shopping": len(products), "bing_shopping": 0},
                credits={
                    "used": 2, "limit": 250, "remaining": 248,
                    "cache_hits": 0, "cache_misses": 2,
                },
            )
            return products

    return MockSearchAgent


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _p(title: str, price: float, seller: str, engine: str = "google_shopping") -> Product:
    """Build a Product with a fake link and raw price string."""
    slug = seller.lower().replace(" ", "")
    return Product(
        title=title,
        price=price,
        seller=seller,
        link=f"https://{slug}.example.com/item",
        engine=engine,
        raw_price=f"₹{price:,.0f}",
        rating=4.5,
        reviews=100,
    )


IPHONE_FIXTURE: list[Product] = [
    _p("Apple iPhone 15 128GB Black", 63000, "Amazon.in"),
    _p("iPhone 15 128 GB (Blue)", 65000, "Flipkart"),
    _p("Apple iPhone 15 (128GB) - Pink", 68000, "Croma"),
    _p("Apple iPhone 15 128GB Green", 66500, "Reliance Digital"),
    _p("APPLE iPhone 15 128 GB Yellow", 64000, "Vijay Sales"),
]

SAMSUNG_FIXTURE: list[Product] = [
    _p("Samsung Galaxy S24 5G 256GB Onyx Black", 55000, "Amazon.in"),
    _p("Samsung Galaxy S24 256 GB Marble Gray", 57000, "Flipkart"),
    _p("Samsung Galaxy S24 (256GB) Cobalt Violet", 56000, "Croma"),
]

MIXED_FIXTURE: list[Product] = IPHONE_FIXTURE + SAMSUNG_FIXTURE
EMPTY_FIXTURE: list[Product] = []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _run(products: list[Product], budget: Optional[float] = None) -> PipelineResult:
    """Run the full pipeline synchronously with a mock search agent."""
    return asyncio.run(
        run_pipeline(
            "test query",
            user_budget=budget,
            search_agent_factory=make_mock_search_agent(products),
        )
    )


def _collect_stream(products: list[Product]) -> list[PipelineEvent]:
    """Run the streaming pipeline and return every yielded event."""

    async def go() -> list[PipelineEvent]:
        return [
            ev
            async for ev in run_pipeline_streaming(
                "test query",
                search_agent_factory=make_mock_search_agent(products),
            )
        ]

    return asyncio.run(go())


def _etype(event: PipelineEvent) -> Optional[str]:
    """Extract an event's type (tolerates 'type' or 'event_type' keys)."""
    return event.get("type") or event.get("event_type")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_happy_path() -> None:
    """Mixed fixture yields 2 clusters and a cheapest-product verdict."""
    r = _run(MIXED_FIXTURE)
    assert r.analysis.total_clusters == 2, f"clusters={r.analysis.total_clusters}"
    assert r.verdict.confidence in {"low", "medium", "high"}, r.verdict.confidence
    assert r.verdict.best_product is not None, "best_product is None"
    assert r.verdict.price_to_pay == 55000, f"price_to_pay={r.verdict.price_to_pay}"
    assert len(r.events) > 0, "no events recorded"


def test_budget_comparison() -> None:
    """Budget below all-cluster minimum is flagged correctly."""
    r = _run(MIXED_FIXTURE, budget=60000)
    assert r.analysis.vs_reference == "under", f"vs_reference={r.analysis.vs_reference}"
    assert r.analysis.reference_price == 60000, f"reference={r.analysis.reference_price}"


def test_empty_results() -> None:
    """No products -> graceful low-confidence verdict, no exception."""
    r = _run(EMPTY_FIXTURE)
    assert r.analysis.total_clusters == 0, f"clusters={r.analysis.total_clusters}"
    assert r.verdict.best_product is None, "best_product should be None"
    assert r.verdict.confidence == "low", f"confidence={r.verdict.confidence}"


def test_streaming_events() -> None:
    """Stream starts with pipeline_started, ends with pipeline_done, covers all stages."""
    events = _collect_stream(MIXED_FIXTURE)
    if VERBOSE:
        print()
        for i, ev in enumerate(events):
            print(f"    [{i:02d}] {json.dumps(ev, default=str)[:200]}")
    assert events, "stream yielded no events"
    types = [_etype(e) for e in events]
    assert types[0] == "pipeline_started", f"first={types[0]}"
    assert types[-1] == "pipeline_done", f"last={types[-1]}"
    required = {"search_started", "analysis_started", "verdict_thinking", "pipeline_complete"}
    missing = required - set(types)
    assert not missing, f"missing event types: {sorted(missing)}"


def test_deduplication_via_clustering() -> None:
    """Five title variants of one product collapse into a single cluster."""
    r = _run(IPHONE_FIXTURE)
    assert r.analysis.total_clusters == 1, f"clusters={r.analysis.total_clusters}"
    c = r.analysis.clusters[0]
    assert c.listings_count == 5, f"listings_count={c.listings_count}"
    assert c.best_deal.price == 63000, f"best_deal.price={c.best_deal.price}"


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def main() -> int:
    """Run all tests; return process exit code (0 = all passed)."""
    global VERBOSE
    parser = argparse.ArgumentParser(description="PriceSensei pipeline tests")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print the full event stream in test_streaming_events")
    VERBOSE = parser.parse_args().verbose

    tests: list[Callable[[], None]] = [
        test_happy_path,
        test_budget_comparison,
        test_empty_results,
        test_streaming_events,
        test_deduplication_via_clustering,
    ]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())