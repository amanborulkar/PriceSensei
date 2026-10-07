"""PriceSensei FastAPI server.

Exposes the multi-agent price-monitoring pipeline over HTTP:

* ``GET /api/health``   - liveness probe
* ``GET /api/credits``  - SerpApi credit / cache status
* ``GET /api/stream``   - Server-Sent Events stream of pipeline events
* ``GET /api/pipeline`` - non-streaming JSON fallback (debugging)
* ``GET /``             - minimal HTML landing page

Run locally::

    python server.py
"""

from __future__ import annotations

import html
import json
import logging
from typing import Any, AsyncIterator, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from src.cache.cache_manager import CacheManager
from src.config import APP_EMOJI, APP_NAME, APP_TAGLINE
from src.orchestrator import run_pipeline, run_pipeline_streaming

logger = logging.getLogger("pricesensei.server")

APP_VERSION = "0.1.0"
UI_ORIGIN = "http://localhost:8501"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000

SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

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


def _parse_engines(engines: Optional[str]) -> Optional[list[str]]:
    """Convert a comma-separated engine string into a clean list.

    Args:
        engines: Raw query value such as ``"google_shopping,bing_shopping"``.

    Returns:
        A list of stripped, non-empty engine names, or ``None`` if nothing
        usable was supplied (so the pipeline falls back to its defaults).
    """
    if not engines:
        return None
    parsed = [name.strip() for name in engines.split(",") if name.strip()]
    return parsed or None


@app.get("/api/health", summary="Health check")
async def health() -> dict[str, str]:
    """Return a static liveness payload."""
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


@app.get("/api/credits", summary="SerpApi credit and cache status")
def credits() -> dict[str, Any]:
    """Return the current credit usage and cache hit/miss counters.

    Declared as a sync endpoint so FastAPI runs the (possibly file-backed)
    cache lookup in its threadpool instead of blocking the event loop.
    """
    return CacheManager().get_credit_status()


@app.get("/api/stream", summary="Stream pipeline events via SSE")
async def stream(
    query: str = Query(..., min_length=1, description="Product name to research"),
    budget: Optional[float] = Query(
        None, ge=0, description="Optional user budget in INR"
    ),
    engines: Optional[str] = Query(
        None, description="Optional comma-separated engine names"
    ),
) -> EventSourceResponse:
    """Run the pipeline and stream each agent event as it happens.

    SSE event types:
        * ``agent_event`` - one per pipeline event (JSON payload)
        * ``stream_end``  - sent once after a successful run
        * ``stream_error`` - sent once if the pipeline raises, then the
          stream closes
    """
    engine_list = _parse_engines(engines)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        logger.info(
            "Stream started: query=%r budget=%s engines=%s",
            query,
            budget,
            engine_list,
        )
        try:
            async for event in run_pipeline_streaming(query, budget, engine_list):
                yield {"event": "agent_event", "data": json.dumps(event, default=str)}
            yield {"event": "stream_end", "data": json.dumps({"ok": True})}
            logger.info("Stream finished: query=%r", query)
        except Exception as exc:  # noqa: BLE001 - surfaced to client as an event
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
    except Exception as exc:  # noqa: BLE001 - converted to HTTP error
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
    <li><a href="/docs">/docs</a> (interactive API docs)</li>
  </ul>
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