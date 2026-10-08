"""End-to-end pipeline tests using mock search agents (no API keys needed).

Run:  python -m tests.test_pipeline [--verbose]

Tests use a placeholder verdict key, so VerdictAgent always takes the
rule-based path and never calls Gemini (no quota burn).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from typing import Any, Callable, Optional

warnings.filterwarnings("ignore", category=FutureWarning)

from src.agents.base import BaseAgent
from src.agents.verdict_agent import VerdictAgent
from src.models.product import Product
from src.orchestrator import (
    PipelineEvent,
    PipelineResult,
    run_pipeline,
    run_pipeline_streaming,
)

VERBOSE = False


def make_mock_search_agent(products: list[Product]) -> Callable[[asyncio.Queue], Any]:
    class MockSearchAgent(BaseAgent):
        def __init__(self, q: asyncio.Queue) -> None:
            super().__init__("search_agent", q)

        async def run(self, query: str, engines: Optional[list[str]] = None) -> list[Product]:
            await self.emit("search_started", query=query, engines=engines or ["google_shopping", "bing_shopping"])
            await self.emit("cache_miss", engine="google_shopping")
            await self.emit("engine_results", engine="google_shopping",
                           count=len(products),
                           min_price=min(p.price for p in products) if products else None)
            await self.emit("cache_miss", engine="bing_shopping")
            await self.emit("engine_results", engine="bing_shopping", count=0, min_price=None)
            await self.emit("search_complete", query=query, total=len(products),
                           by_engine={"google_shopping": len(products), "bing_shopping": 0},
                           credits={"used": 2, "limit": 250, "remaining": 248, "cache_hits": 0, "cache_misses": 2})
            return products

    return MockSearchAgent


def _mock_verdict_factory(q: asyncio.Queue) -> VerdictAgent:
    return VerdictAgent(q, api_key="placeholder_test_key")


def _p(title: str, price: float, seller: str, engine: str = "google_shopping") -> Product:
    slug = seller.lower().replace(" ", "")
    return Product(
        title=title, price=price, seller=seller,
        link=f"https://{slug}.example.com/item", engine=engine,
        raw_price=f"₹{price:,.0f}", rating=4.5, reviews=100,
    )


IPHONE_FIXTURE: list[Product] = [
    _p("Apple iPhone 15 128GB Black", 63000, "Amazon.in"),
    _p("iPhone 15 128 GB (Blue)", 65000, "Flipkart"),
    _p("Apple iPhone 15 (128GB) - Pink", 68000, "Croma"),
    _p("Apple iPhone 15 128GB Green", 66500, "Reliance Digital"),
    _p("APPLE iPhone 15 128 GB Yellow", 64000, "Vijay Sales"),
]

EMPTY_FIXTURE: list[Product] = []


def _run(
    products: list[Product],
    budget: Optional[float] = None,
    query: str = "iPhone 15 128GB",
) -> PipelineResult:
    return asyncio.run(
        run_pipeline(
            query,
            user_budget=budget,
            search_agent_factory=make_mock_search_agent(products),
            verdict_agent_factory=_mock_verdict_factory,
        )
    )


def _collect_stream(products: list[Product], query: str = "iPhone 15 128GB") -> list[PipelineEvent]:
    async def go() -> list[PipelineEvent]:
        return [
            ev async for ev in run_pipeline_streaming(
                query,
                search_agent_factory=make_mock_search_agent(products),
                verdict_agent_factory=_mock_verdict_factory,
            )
        ]
    return asyncio.run(go())


def _etype(event: PipelineEvent) -> Optional[str]:
    return event.get("type") or event.get("event_type")


def test_happy_path() -> None:
    """iPhone fixture yields 1 cluster (all 5 variants merged)."""
    r = _run(IPHONE_FIXTURE)
    assert r.analysis.total_clusters == 1, f"clusters={r.analysis.total_clusters}"
    assert r.verdict.confidence in {"low", "medium", "high"}, r.verdict.confidence
    assert r.verdict.best_product is not None, "best_product is None"
    assert r.verdict.price_to_pay == 63000, f"price_to_pay={r.verdict.price_to_pay}"
    assert len(r.events) > 0, "no events recorded"


def test_budget_comparison() -> None:
    """Budget of Rs.70,000 is above the Rs.63,000 minimum (under = good deal)."""
    r = _run(IPHONE_FIXTURE, budget=70000)
    assert r.analysis.vs_reference == "under", f"vs_reference={r.analysis.vs_reference}"
    assert r.analysis.reference_price == 70000, f"reference={r.analysis.reference_price}"


def test_empty_results() -> None:
    r = _run(EMPTY_FIXTURE)
    assert r.analysis.total_clusters == 0, f"clusters={r.analysis.total_clusters}"
    assert r.verdict.best_product is None, "best_product should be None"
    assert r.verdict.confidence == "low", f"confidence={r.verdict.confidence}"


def test_streaming_events() -> None:
    events = _collect_stream(IPHONE_FIXTURE)
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
    r = _run(IPHONE_FIXTURE)
    assert r.analysis.total_clusters == 1, f"clusters={r.analysis.total_clusters}"
    c = r.analysis.clusters[0]
    assert c.listings_count == 5, f"listings_count={c.listings_count}"
    assert c.best_deal.price == 63000, f"best_deal.price={c.best_deal.price}"


def test_relevance_filter_drops_accessories() -> None:
    """Accessory listings never reach analysis."""
    dirty = [
        _p("Apple iPhone 15 128GB Black", 63000, "Amazon.in"),
        _p("iPhone 15 128GB Silicone Case", 499, "Random"),
        _p("iPhone 15 128GB - Renewed", 34999, "RefurbStore"),
    ]
    r = _run(dirty)
    assert r.analysis.total_clusters == 1, f"clusters={r.analysis.total_clusters}"
    assert r.analysis.clusters[0].listings_count == 1, \
        f"only 1 clean product should survive, got {r.analysis.clusters[0].listings_count}"


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description="PriceSensei pipeline tests")
    parser.add_argument("--verbose", "-v", action="store_true")
    VERBOSE = parser.parse_args().verbose

    tests: list[Callable[[], None]] = [
        test_happy_path,
        test_budget_comparison,
        test_empty_results,
        test_streaming_events,
        test_deduplication_via_clustering,
        test_relevance_filter_drops_accessories,
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