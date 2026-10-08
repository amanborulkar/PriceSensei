"""PriceSensei Streamlit frontend.

Runs as a separate process and talks to the FastAPI backend over HTTP/SSE.
Start the backend first (``python server.py``), then ``streamlit run app.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import streamlit as st

from src.ui.components import (
    render_agent_activity,
    render_clusters,
    render_credit_meter,
    render_empty_state,
    render_header,
    render_price_summary,
    render_products_table,
    render_verdict_card,
)

BACKEND_URL = "http://localhost:8000"
STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0)

st.set_page_config(
    page_title="PriceSensei",
    page_icon="🥋",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------- #
# Backend helpers
# --------------------------------------------------------------------------- #
def fetch_credits() -> dict[str, Any] | None:
    """Fetch credit usage from the backend, or None if it is unreachable."""
    try:
        resp = httpx.get(f"{BACKEND_URL}/api/credits", timeout=3.0)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def stream_events(query: str, budget: float) -> Iterator[tuple[str, dict[str, Any]]]:
    """Consume /api/stream and yield ``(event_name, data)`` tuples."""
    params: dict[str, Any] = {"query": query}
    if budget > 0:
        params["budget"] = budget

    with httpx.stream("GET", f"{BACKEND_URL}/api/stream", params=params, timeout=STREAM_TIMEOUT) as resp:
        resp.raise_for_status()
        event_name = "message"
        for line in resp.iter_lines():
            if not line:
                event_name = "message"
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue
                yield event_name, data


def render_sidebar_credits(slot: Any, credits: dict[str, Any] | None) -> None:
    """Draw the credit meter (or backend error) inside a sidebar slot."""
    with slot.container():
        if credits is None:
            st.error("Backend not running. Start with: python server.py")
        else:
            render_credit_meter(credits)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def init_state() -> None:
    """Initialise session_state keys used by the app."""
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("events", [])


def main() -> None:
    """Render the PriceSensei page."""
    init_state()
    # Must run first: injects the global stylesheet every other renderer relies on.
    render_header()

    # Sidebar
    credits = fetch_credits()
    backend_ok = credits is not None
    with st.sidebar:
        credit_slot = st.empty()
        render_sidebar_credits(credit_slot, credits)
        st.divider()
        st.markdown(
            "**How it works**\n\n"
            "🔍 **Scout** searches shopping engines\n\n"
            "📊 **Analyst** clusters listings and flags outliers\n\n"
            "🥋 **Sensei** gives the final verdict"
        )
        st.caption("Built for SerpApi India Hackathon 2026.")

    # Input form
    with st.form("query_form"):
        product = st.text_input("Product", placeholder="e.g. iPhone 15 128GB", disabled=not backend_ok)
        budget = st.number_input("Budget (INR)", min_value=0, value=0, step=500, disabled=not backend_ok,
                                 help="Set to 0 for no budget limit.")
        submitted = st.form_submit_button("🔍 Ask Sensei", disabled=not backend_ok)

    if budget == 0:
        st.caption("💡 Tip: set a budget to see whether prices are under, near, or over your target.")

    if submitted:
        if not product.strip():
            st.warning("Please enter a product to search for.")
        else:
            st.session_state.result = None
            st.session_state.events = []

            st.subheader("Live Agent Activity")
            activity_slot = st.empty()
            events: list[dict[str, Any]] = st.session_state.events
            failed = False

            # Show the three agent cards in their "Waiting" state straight away.
            with activity_slot.container():
                render_agent_activity(events)

            try:
                for name, data in stream_events(product.strip(), float(budget)):
                    if name == "agent_event":
                        events.append(data)
                        with activity_slot.container():
                            render_agent_activity(events)
                        if data.get("type") == "pipeline_done":
                            st.session_state.result = data.get("result")
                    elif name == "stream_error":
                        st.error(f"{data.get('error_type', 'Error')}: {data.get('error', 'Unknown error')}")
                        failed = True
                        break
                    elif name == "stream_end":
                        break
            except httpx.HTTPError as exc:
                st.error(f"Lost connection to the backend: {exc}")
                failed = True

            # Refresh the credit meter now that the pipeline has run.
            render_sidebar_credits(credit_slot, fetch_credits())

            if failed:
                st.stop()
            if st.session_state.result is None:
                st.warning("Pipeline finished without a final result. Try again or check the backend logs.")
    elif st.session_state.events:
        with st.expander("Agent activity", expanded=False):
            render_agent_activity(st.session_state.events)

    # Results
    result: dict[str, Any] | None = st.session_state.result
    if not result:
        render_empty_state("Ask Sensei about a product to see the verdict here.")
        return

    verdict = result.get("verdict")
    analysis = result.get("analysis") or {}
    if verdict:
        if len(str(verdict.get("recommendation", ""))) > 500:
            st.warning("Verdict is unusually long — LLM output may be verbose.")
        render_verdict_card(verdict)
    if analysis:
        render_price_summary(analysis)
        render_clusters(analysis)
    st.subheader("All Listings")
    render_products_table(result.get("products") or [])


main()