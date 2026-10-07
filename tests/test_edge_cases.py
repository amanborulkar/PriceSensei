"""Edge-case tests for PriceSensei (no pytest required).

Run:  python -m tests.test_edge_cases [--verbose]

Wired to PriceSensei's real agent signatures. Every test runs with network
access blocked, so any accidental SerpApi or Gemini call is reported as a
failure.
"""

from __future__ import annotations

import asyncio
import dataclasses
import socket
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

VERBOSE = False


def log(message: str) -> None:
    """Print a detail line only in --verbose mode."""
    if VERBOSE:
        print(f"    · {message}")


# --------------------------------------------------------------------------- #
# Wiring to PriceSensei's actual signatures
# --------------------------------------------------------------------------- #
def make_product(price: float, title: str = "Test Phone 128GB", **overrides: Any) -> Any:
    """Build a minimal Product fixture."""
    from src.models.product import Product
    fields: dict[str, Any] = {
        "title": title,
        "price": price,
        "seller": "TestSeller",
        "link": f"https://example.com/{int(price)}",
        "engine": "google_shopping",
        "raw_price": f"₹{price:,.0f}",
        "rating": 4.5,
        "reviews": 100,
    }
    fields.update(overrides)
    return Product(**fields)


def _as_dict(obj: Any) -> dict[str, Any]:
    """Convert a result object (dataclass / to_dict / dict) into a dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return dict(vars(obj))


def _run_async(coro: Any) -> Any:
    """Run an awaitable to completion from sync test code."""
    async def _inner() -> Any:
        return await coro
    return asyncio.run(_inner())


def run_analysis(products: list[Any], query: str = "test phone") -> Any:
    """Run AnalysisAgent on ``products`` and return its raw result object."""
    from src.agents.analysis_agent import AnalysisAgent
    queue: asyncio.Queue = asyncio.Queue()
    agent = AnalysisAgent(queue)
    return _run_async(agent.run(query, products))


def run_verdict(analysis: Any, budget: float | None = None) -> Any:
    """Run VerdictAgent on an analysis result and return its raw result."""
    from src.agents.verdict_agent import VerdictAgent
    queue: asyncio.Queue = asyncio.Queue()
    # Placeholder key triggers the rule-based short-circuit.
    agent = VerdictAgent(queue, api_key="placeholder_for_tests")
    return _run_async(agent.run(analysis, budget))


def run_orchestrator_with_empty_search(query: str = "test phone") -> Any:
    """Run run_pipeline with a mock search agent that returns no products."""
    from src.agents.base import BaseAgent
    from src.orchestrator import run_pipeline

    class MockEmptySearch(BaseAgent):
        def __init__(self, event_queue: Any) -> None:
            super().__init__("search_agent", event_queue)

        async def run(self, q: str, engines: Any = None) -> list[Any]:
            return []

    return _run_async(
        run_pipeline(
            query,
            search_agent_factory=MockEmptySearch,
        )
    )


def make_cache_manager() -> Any:
    """Create a CacheManager using the default cache dir."""
    from src.cache.cache_manager import CacheManager
    return CacheManager()


# --------------------------------------------------------------------------- #
# Network guard
# --------------------------------------------------------------------------- #
@contextmanager
def no_network() -> Iterator[list[str]]:
    """Block DNS lookups and socket connects, recording any attempts."""
    attempts: list[str] = []
    real_connect = socket.socket.connect
    real_gai = socket.getaddrinfo

    def blocked_gai(host: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(f"DNS {host}")
        raise OSError("network blocked by tests")

    def blocked_connect(self: socket.socket, address: Any) -> Any:
        attempts.append(f"connect {address}")
        raise OSError("network blocked by tests")

    socket.getaddrinfo = blocked_gai  # type: ignore[assignment]
    socket.socket.connect = blocked_connect  # type: ignore[method-assign]
    try:
        yield attempts
    finally:
        socket.getaddrinfo = real_gai
        socket.socket.connect = real_connect  # type: ignore[method-assign]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_analysis_empty_products() -> None:
    """Empty product list yields a zeroed analysis without raising."""
    a = _as_dict(run_analysis([]))
    log(f"analysis keys: {sorted(a)}")
    assert a["total_products"] == 0, f"total_products={a['total_products']}"
    assert a["total_clusters"] == 0, f"total_clusters={a['total_clusters']}"
    assert not a["clusters"], "clusters should be empty"
    assert a.get("best_overall_deal") is None, "best_overall_deal should be None"


def test_analysis_single_product() -> None:
    """One product yields one cluster, one listing, FAIR_PRICE."""
    a = _as_dict(run_analysis([make_product(50000)]))
    assert a["total_clusters"] == 1, f"total_clusters={a['total_clusters']}"
    cluster = a["clusters"][0]
    log(f"cluster: {cluster.get('representative_title')} / {cluster.get('flag')}")
    assert cluster["listings_count"] == 1, f"listings_count={cluster['listings_count']}"
    assert cluster["flag"] == "FAIR_PRICE", f"flag={cluster['flag']}"


def test_analysis_removes_outlier() -> None:
    """Realistic prices + one absurd outlier: the outlier is dropped."""
    products = [
        make_product(45000),
        make_product(47000),
        make_product(48000),
        make_product(50000),
        make_product(52000),
        make_product(4_500_000, seller="Scammer"),
    ]
    a = _as_dict(run_analysis(products))
    log(f"outliers_removed={a.get('outliers_removed')} global_max={a.get('global_max_price')}")
    assert a["outliers_removed"] >= 1, f"outliers_removed={a['outliers_removed']}"
    assert a["global_max_price"] < 4_500_000, f"outlier survived: max={a['global_max_price']}"


def test_verdict_empty_analysis() -> None:
    """Empty analysis gives a low-confidence rule-based verdict, no LLM call."""
    v = _as_dict(run_verdict(run_analysis([])))
    log(f"verdict: {v.get('recommendation')}")
    assert v["confidence"] == "low", f"confidence={v['confidence']}"
    assert v["model_used"] == "rule-based", f"model_used={v['model_used']}"


def test_verdict_single_listing_cluster() -> None:
    """A single-listing cluster gets a rule-based verdict, no LLM call."""
    v = _as_dict(run_verdict(run_analysis([make_product(50000)])))
    log(f"verdict: {v.get('recommendation')} ({v.get('confidence')})")
    assert v["model_used"] == "rule-based", f"model_used={v['model_used']}"
    assert v["recommendation"], "recommendation should not be empty"


def test_orchestrator_empty_search() -> None:
    """Empty search results still complete the pipeline with low confidence."""
    r = run_orchestrator_with_empty_search()
    log(f"products={len(r.products)} verdict={r.verdict.recommendation}")
    assert r.products == [], "products should be empty"
    assert r.verdict is not None, "verdict missing"
    assert r.verdict.confidence == "low", f"confidence={r.verdict.confidence}"


def test_cache_credit_status_keys() -> None:
    """get_credit_status() returns the six documented keys."""
    status = make_cache_manager().get_credit_status()
    log(f"status: {status}")
    expected = {"used", "limit", "remaining", "percent", "cache_hits", "cache_misses"}
    assert isinstance(status, dict), f"expected dict, got {type(status).__name__}"
    missing = expected - set(status)
    assert not missing, f"missing keys: {sorted(missing)}"


def test_product_zero_price_invalid() -> None:
    """A zero-price product is not valid."""
    p = make_product(0)
    assert p.is_valid is False, f"is_valid={p.is_valid!r}"


TESTS: list[Callable[[], None]] = [
    test_analysis_empty_products,
    test_analysis_single_product,
    test_analysis_removes_outlier,
    test_verdict_empty_analysis,
    test_verdict_single_listing_cluster,
    test_orchestrator_empty_search,
    test_cache_credit_status_keys,
    test_product_zero_price_invalid,
]


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def run_all(tests: list[Callable[[], None]]) -> int:
    """Run all tests, print results, and return a process exit code."""
    failures = 0
    for test in tests:
        name = test.__name__
        try:
            with no_network() as attempts:
                test()
            if attempts:
                raise AssertionError(f"unexpected network access: {attempts}")
            print(f"✅ {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            if isinstance(exc, AssertionError):
                print(f"❌ {name}: {exc}")
            else:
                print(f"❌ {name}: {type(exc).__name__}: {exc}")
            if VERBOSE:
                traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


def main() -> int:
    """Parse flags and run the suite."""
    global VERBOSE
    VERBOSE = "--verbose" in sys.argv[1:]
    return run_all(TESTS)


if __name__ == "__main__":
    sys.exit(main())