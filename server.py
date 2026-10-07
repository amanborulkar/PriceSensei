"""PriceSensei FastAPI server.

Exposes the multi-agent price-monitoring pipeline over HTTP:

* ``GET /api/health``             - liveness probe
* ``GET /api/credits``            - SerpApi credit / cache / rate-limit status
* ``GET /api/rate_limit_status``  - remaining rate-limit quota
* ``GET /api/stream``             - Server-Sent Events stream of pipeline events
* ``GET /api/pipeline``           - non-streaming JSON fallback (debugging)
* ``GET /``                       - minimal HTML landing page

Rate limiting is in-memory, per-IP (5 req / 30 min) and global (10 req / 30 min),
to protect the 250 SerpApi credits/month budget.

Run locally::

    python server.py
"""

from __future__ import annotations

import html
import json
import logging
import math
import threading
import time
from collections import deque
from typing import Any, AsyncIterator, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.cache.cache_manager import CacheManager
from src.config import APP_EMOJI, APP_NAME, APP_TAGLINE
from src.orchestrator import run_pipeline, run_pipeline_streaming

logger = logging.getLogger("pricesensei.server")
_rl_logger = logging.getLogger("pricesensei.ratelimit")

APP_VERSION = "0.1.0"
UI_ORIGIN = "http://localhost:8501"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000

SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# --------------------------------------------------------------------------- #
# Rate limiting (protects the 250 SerpApi credits/month)
# --------------------------------------------------------------------------- #
_WINDOW_SECONDS: int = 1800  # 30 minutes
_IP_MAX_REQUESTS: int = 5
_GLOBAL_MAX_REQUESTS: int = 10
_GLOBAL_KEY: str = "__global__"


class RateLimiter:
    """Sliding-window rate limiter with one timestamp deque per key.

    Timestamps come from ``time.monotonic()``. Old entries are pruned lazily
    on every check. All bucket access is guarded by a ``threading.Lock``.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, consume: bool = True) -> tuple[bool, float, int]:
        """Check (and by default record) a request for ``key``.

        Returns:
            ``(allowed, retry_after_seconds, remaining)``.
        """
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
            while bucket and now - bucket[0] >= self.window_seconds:
                bucket.popleft()

            if len(bucket) >= self.max_requests:
                self._buckets[key] = bucket
                retry_after = max(bucket[0] + self.window_seconds - now, 0.0)
                return False, retry_after, 0

            if consume:
                bucket.append(now)
                self._buckets[key] = bucket
            elif bucket:
                self._buckets[key] = bucket
            else:
                self._buckets.pop(key, None)
            return True, 0.0, self.max_requests - len(bucket)


_IP_LIMITER = RateLimiter(max_requests=_IP_MAX_REQUESTS, window_seconds=_WINDOW_SECONDS)
_GLOBAL_LIMITER = RateLimiter(max_requests=_GLOBAL_MAX_REQUESTS, window_seconds=_WINDOW_SECONDS)
_CHECK_LOCK = threading.Lock()  # atomic IP + global check


class RateLimitExceeded(Exception):
    """Internal signal; converted to a 429 by the exception handler."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.headers = {"Retry-After": str(body["retry_after_seconds"])}


def _client_ip(request: Request) -> str:
    """Return the caller's IP, or ``"unknown"``."""
    client = request.client
    return client.host if client and client.host else "unknown"


def _rate_limit(request: Request) -> dict[str, int]:
    """FastAPI dependency enforcing per-IP and global limits. Fails open."""
    info: dict[str, int] = {
        "ip_remaining": _IP_MAX_REQUESTS,
        "global_remaining": _GLOBAL_MAX_REQUESTS,
    }
    denial: dict[str, Any] | None = None
    try:
        ip = _client_ip(request)
        with _CHECK_LOCK:
            ip_ok, ip_retry, ip_rem = _IP_LIMITER.check(ip, consume=False)
            g_ok, g_retry, g_rem = _GLOBAL_LIMITER.check(_GLOBAL_KEY, consume=False)
            if ip_ok and g_ok:
                _, _, ip_rem = _IP_LIMITER.check(ip)
                _, _, g_rem = _GLOBAL_LIMITER.check(_GLOBAL_KEY)
                info = {"ip_remaining": ip_rem, "global_remaining": g_rem}
            else:
                scope = "ip" if not ip_ok else "global"
                retry = ip_retry if not ip_ok else g_retry
                denial = {
                    "error": "rate_limit_exceeded",
                    "message": (
                        "PriceSensei limits queries to protect SerpApi credits. "
                        "Please wait before trying again."
                    ),
                    "retry_after_seconds": max(1, math.ceil(retry)),
                    "scope": scope,
                    "ip_remaining": ip_rem,
                    "global_remaining": g_rem,
                }
    except Exception:
        _rl_logger.exception("Rate limiter error; allowing request")
        denial = None

    if denial is not None:
        raise RateLimitExceeded(denial)
    request.state.rate_limit = info
    return info


def _rate_limit_snapshot(ip: str) -> dict[str, int]:
    """Return remaining quota without consuming."""
    try:
        with _CHECK_LOCK:
            _, _, ip_rem = _IP_LIMITER.check(ip, consume=False)
            _, _, g_rem = _GLOBAL_LIMITER.check(_GLOBAL_KEY, consume=False)
        return {
            "ip_remaining": ip_rem,
            "global_remaining": g_rem,
            "window_seconds": _WINDOW_SECONDS,
        }
    except Exception:
        _rl_logger.exception("Rate limit snapshot failed")
        return {
            "ip_remaining": _IP_MAX_REQUESTS,
            "global_remaining": _GLOBAL_MAX_REQUESTS,
            "window_seconds": _WINDOW_SECONDS,
        }


def _rate_limit_warning(request: Request) -> dict[str, Any] | None:
    """Return a rate_limit_warning SSE payload when quota is nearly out."""
    info = getattr(request.state, "rate_limit", None)
    if not info:
        return None
    if info["ip_remaining"] <= 1 or info["global_remaining"] <= 1:
        return {
            "type": "rate_limit_warning",
            "ip_remaining": info["ip_remaining"],
            "global_remaining": info["global_remaining"],
            "window_seconds": _WINDOW_SECONDS,
            "ts": time.time(),
        }
    return None


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
app = FastAPI(
    title=f"{APP_NAME} API",
    description=APP_TAGLINE,
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[UI_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(RateLimitExceeded)
async def _handle_rate_limit(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a flat JSON 429 body with a Retry-After header."""
    return JSONResponse(status_code=429, content=exc.body, headers=exc.headers)


def _parse_engines(engines: Optional[str]) -> Optional[list[str]]:
    """Convert a comma-separated engine string into a clean list."""
    if not engines:
        return None
    parsed = [name.strip() for name in engines.split(",") if name.strip()]
    return parsed or None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/health", summary="Health check")
async def health() -> dict[str, str]:
    """Return a static liveness payload."""
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


@app.get("/api/credits", summary="SerpApi credit and cache status")
def credits(request: Request) -> dict[str, Any]:
    """Return credit usage, cache counters, and current rate limit quota."""
    status = CacheManager().get_credit_status()
    snap = _rate_limit_snapshot(_client_ip(request))
    status["rate_limit"] = {
        "ip_remaining": snap["ip_remaining"],
        "global_remaining": snap["global_remaining"],
        "window_seconds": snap["window_seconds"],
    }
    return status


@app.get("/api/rate_limit_status", summary="Rate limit status")
def rate_limit_status(request: Request, ip_hint: Optional[str] = None) -> dict[str, int]:
    """Report remaining quota for ``ip_hint`` (default: the caller)."""
    snap = _rate_limit_snapshot(ip_hint or _client_ip(request))
    return {
        "ip_remaining": snap["ip_remaining"],
        "global_remaining": snap["global_remaining"],
        "window_seconds": _WINDOW_SECONDS,
        "ip_limit": _IP_MAX_REQUESTS,
        "global_limit": _GLOBAL_MAX_REQUESTS,
    }


@app.get("/api/stream", summary="Stream pipeline events via SSE")
async def stream(
    request: Request,
    _rl: dict = Depends(_rate_limit),
    query: str = Query(..., min_length=1, description="Product name to research"),
    budget: Optional[float] = Query(
        None, ge=0, description="Optional user budget in INR"
    ),
    engines: Optional[str] = Query(
        None, description="Optional comma-separated engine names"
    ),
) -> EventSourceResponse:
    """Run the pipeline and stream each agent event as it happens."""
    engine_list = _parse_engines(engines)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        logger.info(
            "Stream started: query=%r budget=%s engines=%s",
            query,
            budget,
            engine_list,
        )
        try:
            warning = _rate_limit_warning(request)
            if warning is not None:
                yield {
                    "event": "agent_event",
                    "data": json.dumps(warning, default=str),
                }
            async for event in run_pipeline_streaming(query, budget, engine_list):
                yield {"event": "agent_event", "data": json.dumps(event, default=str)}
            yield {"event": "stream_end", "data": json.dumps({"ok": True})}
            logger.info("Stream finished: query=%r", query)
        except Exception as exc:
            logger.exception("Stream failed: query=%r", query)
            yield {
                "event": "stream_error",
                "data": json.dumps(
                    {"error": str(exc), "error_type": type(exc).__name__}
                ),
            }
            return

    return EventSourceResponse(event_generator(), headers=SSE_HEADERS)


@app.get("/api/pipeline", summary="Run pipeline without streaming (debug)")
async def pipeline(
    request: Request,
    _rl: dict = Depends(_rate_limit),
    query: str = Query(..., min_length=1, description="Product name to research"),
    budget: Optional[float] = Query(
        None, ge=0, description="Optional user budget in INR"
    ),
    engines: Optional[str] = Query(
        None, description="Optional comma-separated engine names"
    ),
) -> dict[str, Any]:
    """Run the full pipeline and return the final result as JSON."""
    engine_list = _parse_engines(engines)
    logger.info(
        "Pipeline started: query=%r budget=%s engines=%s", query, budget, engine_list
    )
    try:
        result = await run_pipeline(query, budget, engine_list)
    except Exception as exc:
        logger.exception("Pipeline failed: query=%r", query)
        raise HTTPException(
            status_code=500,
            detail={"error": str(exc), "error_type": type(exc).__name__},
        ) from exc
    return result.to_dict()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    """Serve a minimal landing page pointing at the API and the UI."""
    name = html.escape(APP_NAME)
    tagline = html.escape(APP_TAGLINE)
    emoji = html.escape(APP_EMOJI)
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name} API</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 560px;
           margin: 12vh auto; padding: 0 1rem; line-height: 1.6; }}
    h1 {{ margin-bottom: 0; }}
    .tag {{ color: #666; margin-top: 0.25rem; }}
    code {{ background: #f2f2f2; padding: 0.1rem 0.35rem; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{emoji} {name}</h1>
  <p class="tag">{tagline}</p>
  <ul>
    <li><a href="/api/health">/api/health</a></li>
    <li><a href="/api/credits">/api/credits</a></li>
    <li><a href="/api/rate_limit_status">/api/rate_limit_status</a></li>
    <li><a href="/docs">/docs</a> (interactive API docs)</li>
  </ul>
  <p><small>Queries are limited to 5 per IP and 10 globally per 30 minutes
     to protect the SerpApi credit budget.</small></p>
  <p>The UI runs separately at
     <a href="{UI_ORIGIN}">localhost:8501</a>.</p>
</body>
</html>"""
    return HTMLResponse(page)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, log_level="info")