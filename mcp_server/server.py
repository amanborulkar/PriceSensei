"""PriceSensei MCP server: exposes the price-monitoring pipeline as MCP tools.

Claude Desktop setup (claude_desktop_config.json):

    {
      "mcpServers": {
        "pricesensei": {
          "command": "python",
          "args": ["/absolute/path/to/PriceSensei/mcp_server/server.py"],
          "env": {"SERPAPI_KEY": "...", "GEMINI_API_KEY": "..."}
        }
      }
    }

Restart Claude Desktop. Requires: pip install mcp
Never print() to stdout in this process: stdio is the MCP transport.
"""
import asyncio
import concurrent.futures
import functools
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TypeVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from src.agents.analysis_agent import AnalysisAgent  # noqa: E402
from src.agents.search_agent import SearchAgent  # noqa: E402
from src.cache.cache_manager import CacheManager  # noqa: E402
from src.orchestrator import run_pipeline  # noqa: E402

T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    """Run a coroutine to completion via asyncio.run().

    FastMCP may call sync tools from inside its own event loop, where a bare
    asyncio.run() raises. In that case, run it on a worker thread instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()  # type: ignore[arg-type]


def _safe(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Turn any exception into an error dict instead of crashing the server."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "error_type": type(e).__name__}

    return wrapper


@_safe
def search_products(query: str, engines: Optional[list[str]] = None) -> dict[str, Any]:
    """Search shopping engines (Google Shopping, Bing Shopping) for a product.

    Use when the user wants raw listings: titles, prices, sellers, links.
    `engines` optionally restricts the engines, e.g. ["google_shopping"].
    Returns {"query", "count", "products": [...]}.
    """

    async def go() -> list[Any]:
        return await SearchAgent(asyncio.Queue()).run(query, engines=engines)

    products = _run(go())
    return {
        "query": query,
        "count": len(products),
        "products": [p.to_dict() for p in products],
    }


@_safe
def analyze_prices(query: str, reference_price: Optional[float] = None) -> dict[str, Any]:
    """Search, then group identical products and compute price statistics.

    Use when the user wants to understand the market: how many distinct
    products match, the global min/median price, and the best deal per
    cluster. `reference_price` (e.g. the user's budget) is compared against
    the market. Returns {"query", "total_clusters", "global_min_price",
    "global_median_price", "clusters": [...]}.
    """

    async def go() -> Any:
        queue: asyncio.Queue = asyncio.Queue()
        products = await SearchAgent(queue).run(query)
        return await AnalysisAgent(queue).run(
            query, products, reference_price=reference_price
        )

    result = {"query": query, **_run(go()).to_dict()}
    return result


@_safe
def get_verdict(query: str, user_budget: Optional[float] = None) -> dict[str, Any]:
    """Get a buy recommendation for a product (full pipeline: search, analysis, verdict).

    Use when the user asks "should I buy this?" or "what's the best price?".
    `user_budget` in INR sharpens the advice. Returns {"recommendation",
    "confidence", "reasoning": [...], "suggested_action", "best_product",
    "price_to_pay"}.
    """
    result = _run(run_pipeline(query, user_budget=user_budget))
    return result.verdict.to_dict()


@_safe
def get_credits() -> dict[str, Any]:
    """Report SerpApi credit usage: used, limit, remaining, percent, cache hits/misses.

    Use before running several searches, or when the user asks how many
    API credits are left. Makes no API calls and costs no credits.
    """
    return CacheManager().get_credit_status()


@_safe
def get_pipeline_result(query: str, user_budget: Optional[float] = None) -> dict[str, Any]:
    """Run the full PriceSensei pipeline and return everything.

    Returns products, price clusters, analysis, verdict, credit status,
    elapsed time and the complete agent event log. Prefer get_verdict or
    analyze_prices when only part of the output is needed.
    """
    result = _run(run_pipeline(query, user_budget=user_budget))
    return result.to_dict(include_events=True)


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    f.__name__: f
    for f in (search_products, analyze_prices, get_verdict, get_credits, get_pipeline_result)
}

try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("pricesensei")
    for _fn in TOOLS.values():
        mcp.tool()(_fn)

    def main() -> None:
        """Serve over stdio using FastMCP."""
        mcp.run()

except ImportError:  # older mcp SDK: low-level Server API
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    _PARAMS: dict[str, dict[str, Any]] = {
        "query": {"type": "string"},
        "engines": {"type": "array", "items": {"type": "string"}},
        "reference_price": {"type": "number"},
        "user_budget": {"type": "number"},
    }

    def _schema(fn: Callable[..., Any]) -> dict[str, Any]:
        sig = inspect.signature(fn).parameters
        return {
            "type": "object",
            "properties": {n: _PARAMS[n] for n in sig},
            "required": [n for n, p in sig.items() if p.default is inspect.Parameter.empty],
        }

    server: Server = Server("pricesensei")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=n, description=inspect.getdoc(f) or "", inputSchema=_schema(f))
            for n, f in TOOLS.items()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: Optional[dict[str, Any]]) -> list[types.TextContent]:
        if name not in TOOLS:
            out: dict[str, Any] = {"error": f"unknown tool: {name}", "error_type": "KeyError"}
        else:
            out = await asyncio.to_thread(TOOLS[name], **(arguments or {}))
        return [types.TextContent(type="text", text=json.dumps(out, default=str))]

    def main() -> None:
        """Serve over stdio using the low-level Server."""

        async def go() -> None:
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        asyncio.run(go())


if __name__ == "__main__":
    main()