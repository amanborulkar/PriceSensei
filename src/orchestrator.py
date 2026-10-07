"""Pipeline orchestrator: wires Scout -> Analyst -> Sensei over one event queue."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from src.agents.analysis_agent import AnalysisAgent
from src.agents.base import BaseAgent, Event
from src.agents.search_agent import SearchAgent
from src.agents.verdict_agent import VerdictAgent
from src.models.analysis import AnalysisResult
from src.models.product import Product
from src.models.verdict import Verdict

PipelineEvent = Event

IDLE_TIMEOUT_S = 120.0  # Gemini 3.8 thinking can take 60+ seconds


def _dump(obj: Any) -> dict[str, Any]:
    """Serialize a model via to_dict() if present, else fall back to __dict__."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj.__dict__)


def _make_event(event_type: str, **payload: Any) -> PipelineEvent:
    """Build an orchestrator event with the same shape BaseAgent.emit produces."""
    return {
        **payload,
        "agent": "orchestrator",
        "type": event_type,
        "ts": time.time(),
    }


def _credits(search: Any) -> dict[str, Any]:
    """Best-effort credit status from the search agent's cache."""
    for attr in ("cache", "_cache"):
        cache = getattr(search, attr, None)
        if cache is not None and hasattr(cache, "get_credit_status"):
            try:
                return cache.get_credit_status()
            except Exception:
                return {}
    return {}


@dataclass
class PipelineResult:
    """Full output of one pipeline run."""

    query: str
    products: list[Product]
    analysis: AnalysisResult
    verdict: Verdict
    credits: dict[str, Any]
    elapsed_seconds: float
    events: list[PipelineEvent] = field(default_factory=list)

    def to_dict(self, include_events: bool = True) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "query": self.query,
            "products": [_dump(p) for p in self.products],
            "analysis": _dump(self.analysis),
            "verdict": _dump(self.verdict),
            "credits": self.credits,
            "elapsed_seconds": self.elapsed_seconds,
            "events": list(self.events) if include_events else [],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PipelineResult":
        """Rebuild a PipelineResult from to_dict() output."""
        return cls(
            query=data["query"],
            products=[Product.from_dict(p) for p in data["products"]],
            analysis=AnalysisResult.from_dict(data["analysis"]),
            verdict=Verdict.from_dict(data["verdict"]),
            credits=data.get("credits", {}),
            elapsed_seconds=data["elapsed_seconds"],
            events=list(data.get("events", [])),
        )


async def run_pipeline(
    query: str,
    user_budget: Optional[float] = None,
    engines: Optional[list[str]] = None,
    event_queue: Optional[asyncio.Queue[PipelineEvent]] = None,
    *,
    search_agent_factory: Callable[[asyncio.Queue[PipelineEvent]], Any] = SearchAgent,
    analysis_agent_factory: Callable[[asyncio.Queue[PipelineEvent]], Any] = AnalysisAgent,
    verdict_agent_factory: Callable[[asyncio.Queue[PipelineEvent]], Any] = VerdictAgent,
) -> PipelineResult:
    """Run Scout -> Analyst -> Sensei sequentially and return a PipelineResult.

    Agents are built via the factories so tests/UI can inject mocks. Emits
    pipeline_started / pipeline_complete / pipeline_failed. Exceptions are
    re-raised after the failure event is emitted.
    """
    owns_queue = event_queue is None
    queue: asyncio.Queue[PipelineEvent] = (
        event_queue if event_queue is not None else asyncio.Queue(maxsize=1000)
    )
    orchestrator = BaseAgent("orchestrator", queue)
    start = time.perf_counter()

    try:
        await orchestrator.emit(
            "pipeline_started", query=query, user_budget=user_budget, engines=engines
        )
        search = search_agent_factory(queue)
        analyst = analysis_agent_factory(queue)
        sensei = verdict_agent_factory(queue)

        products = await search.run(query, engines=engines)
        analysis = await analyst.run(query, products, reference_price=user_budget)
        verdict = await sensei.run(analysis, user_budget=user_budget)

        elapsed = time.perf_counter() - start
        await orchestrator.emit(
            "pipeline_complete",
            elapsed=elapsed,
            product_count=len(products),
            cluster_count=len(analysis.clusters),
            confidence=verdict.confidence,
        )
    except Exception as e:
        await orchestrator.emit(
            "pipeline_failed", error=str(e), error_type=type(e).__name__
        )
        raise

    events: list[PipelineEvent] = []
    if owns_queue:
        while True:
            try:
                events.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

    return PipelineResult(
        query=query,
        products=products,
        analysis=analysis,
        verdict=verdict,
        credits=_credits(search),
        elapsed_seconds=elapsed,
        events=events,
    )


async def run_pipeline_streaming(
    query: str,
    user_budget: Optional[float] = None,
    engines: Optional[list[str]] = None,
    **agent_factory_kwargs: Any,
) -> AsyncIterator[PipelineEvent]:
    """Yield pipeline events live (for SSE), ending with pipeline_done or pipeline_failed.

    Runs run_pipeline as a background task. If no event arrives for
    IDLE_TIMEOUT_S seconds, the task is cancelled and a pipeline_failed event
    is yielded before returning.
    """
    queue: asyncio.Queue[PipelineEvent] = asyncio.Queue(maxsize=1000)
    task = asyncio.create_task(
        run_pipeline(query, user_budget, engines, queue, **agent_factory_kwargs)
    )

    failure_seen = False
    try:
        # Drain until the task finishes AND the queue is empty.
        while not task.done() or not queue.empty():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                yield _make_event(
                    "pipeline_failed",
                    error=f"no pipeline activity for {IDLE_TIMEOUT_S:.0f}s",
                    error_type="TimeoutError",
                )
                return
            if item.get("type") == "pipeline_failed":
                failure_seen = True
            yield item

        # Task is done and queue is drained — report final outcome.
        if task.cancelled():
            yield _make_event(
                "pipeline_failed", error="pipeline cancelled", error_type="CancelledError"
            )
            return

        exc = task.exception()
        if exc is not None:
            if not failure_seen:
                yield _make_event(
                    "pipeline_failed", error=str(exc), error_type=type(exc).__name__
                )
            return

        result: PipelineResult = task.result()
        yield _make_event(
            "pipeline_done", result=result.to_dict(include_events=False)
        )
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task