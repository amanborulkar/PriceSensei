"""Streamlit rendering helpers for PriceSensei.

Every function takes plain dicts (the JSON shapes produced by the FastAPI
backend) and renders UI. Only streamlit and the standard library are used.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

try:  # Prefer the shared config when available.
    from src.config import AGENT_LABELS, APP_EMOJI, APP_NAME, APP_TAGLINE
except ImportError:  # Keeps this module usable standalone.
    APP_NAME = "PriceSensei"
    APP_TAGLINE = "The wisdom to buy right."
    APP_EMOJI = "🥋"
    AGENT_LABELS = {
        "search_agent": "Scout",
        "analysis_agent": "Analyst",
        "verdict_agent": "Sensei",
    }

DEFAULT_CREDIT_LIMIT = 250

_FLAG_BADGES: dict[str, str] = {
    "BEST_DEAL": "🟢 Best Deal",
    "FAIR_PRICE": "⚪ Fair Price",
    "OVERPRICED": "🔴 Overpriced",
}

_CONFIDENCE_BADGES: dict[str, str] = {
    "high": ":green-background[🟢 High confidence]",
    "medium": ":orange-background[🟡 Medium confidence]",
    "low": ":red-background[🔴 Low confidence]",
}

# Events that only wrap other events or carry the full result payload.
_SKIPPED_EVENTS = {"step_started", "step_complete", "pipeline_done"}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _fmt_price(value: Any) -> str:
    """Format a number as INR, or an em dash when missing."""
    if isinstance(value, (int, float)):
        return f"₹{value:,.0f}"
    return "—"


def _esc(text: Any) -> str:
    """Escape characters Streamlit markdown would treat as LaTeX."""
    return str(text).replace("$", "\\$")


def _pretty_engine(engine: Any) -> str:
    """Turn 'google_shopping' into 'Google Shopping'."""
    return str(engine or "unknown").replace("_", " ").title()


def _fmt_rating(product: dict[str, Any]) -> str:
    """Format rating and review count for table display."""
    rating = product.get("rating")
    if not isinstance(rating, (int, float)):
        return "—"
    reviews = product.get("reviews")
    suffix = f" ({reviews:,})" if isinstance(reviews, (int, float)) else ""
    return f"{rating:.1f} ⭐{suffix}"


def _product_row(product: dict[str, Any], is_best: bool = False) -> dict[str, Any]:
    """Convert a product dict into a table row."""
    return {
        "": "🏆" if is_best else "",
        "Seller": product.get("seller") or "—",
        "Price (₹)": product.get("price"),
        "Engine": _pretty_engine(product.get("engine")),
        "Rating": _fmt_rating(product),
    }


def _show_table(rows: list[dict[str, Any]]) -> None:
    """Render rows as a dataframe with a formatted price column."""
    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "": st.column_config.TextColumn("", width="small"),
            "Price (₹)": st.column_config.NumberColumn("Price (₹)", format="₹%.0f"),
        },
    )


def _describe_event(event: dict[str, Any]) -> tuple[str, str] | None:
    """Return (emoji, description) for an event, or None to skip it."""
    etype = str(event.get("type", ""))
    if etype in _SKIPPED_EVENTS:
        return None

    engine = _pretty_engine(event.get("engine"))
    engines = event.get("engines")
    if isinstance(engines, (list, tuple)):
        engines_text = ", ".join(_pretty_engine(e) for e in engines)
    else:
        engines_text = str(engines) if engines else "default engines"

    if etype == "pipeline_started":
        budget = event.get("user_budget")
        budget_text = f" with budget {_fmt_price(budget)}" if budget else ""
        return "🔍", f"Starting for “{event.get('query', '')}”{budget_text}"
    if etype == "search_started":
        return "🔍", f"Searching {engines_text}"
    if etype == "engine_results":
        return "✅", f"{engine}: {event.get('count', 0)} results, from {_fmt_price(event.get('min_price'))}"
    if etype == "cache_hit":
        return "⚡", f"{engine}: served from cache ({event.get('count', 0)} results)"
    if etype == "cache_miss":
        return "🔍", f"{engine}: not cached, querying live"
    if etype == "engine_failed":
        return "❌", f"{engine} failed: {event.get('error', 'unknown error')}"
    if etype == "search_complete":
        return "✅", f"Search complete: {event.get('total', 0)} products found"
    if etype == "analysis_started":
        return "⏳", f"Analysing {event.get('product_count', 0)} products"
    if etype == "analysis_empty":
        return "⚠️", "Nothing to analyse: no products found"
    if etype == "outliers_flagged":
        return "✅", f"Removed {event.get('removed', 0)} outliers, kept {event.get('kept', 0)}"
    if etype == "clusters_formed":
        return "✅", f"Grouped products into {event.get('count', 0)} clusters"
    if etype == "reference_comparison":
        return "✅", (
            f"Lowest {_fmt_price(event.get('min_price'))} vs reference "
            f"{_fmt_price(event.get('reference'))}: {event.get('verdict', '—')}"
        )
    if etype == "analysis_complete":
        return "✅", (
            f"Analysis complete: {event.get('clusters', 0)} clusters, "
            f"median {_fmt_price(event.get('global_median'))}"
        )
    if etype == "verdict_thinking":
        return "🧠", "Weighing the evidence…"
    if etype == "verdict_ready":
        return "✅", "Verdict ready"
    if etype == "verdict_fallback":
        return "⚠️", f"Using rule-based fallback: {event.get('reason', 'LLM unavailable')}"
    if etype == "pipeline_complete":
        return "✅", (
            f"Done in {event.get('elapsed', 0):.1f}s "
            f"({event.get('product_count', 0)} products, confidence: {event.get('confidence', '—')})"
        )
    if etype == "pipeline_failed":
        return "❌", f"Pipeline failed: {event.get('error', 'unknown error')}"

    # Unknown event: pick an emoji from the name.
    if "fail" in etype or "error" in etype:
        return "❌", etype.replace("_", " ")
    if "fallback" in etype:
        return "⚠️", etype.replace("_", " ")
    if "complete" in etype or "ready" in etype:
        return "✅", etype.replace("_", " ")
    if "thinking" in etype:
        return "🧠", etype.replace("_", " ")
    return "🔍", etype.replace("_", " ")


# --------------------------------------------------------------------------- #
# Public renderers
# --------------------------------------------------------------------------- #
def render_header() -> None:
    """Render the app title, emoji and tagline."""
    st.title(f"{APP_EMOJI} {APP_NAME}")
    st.caption(APP_TAGLINE)


def render_credit_meter(credits: dict[str, Any]) -> None:
    """Render a 'X / 250 credits used' badge with a progress bar."""
    used = int(credits.get("used", 0) or 0)
    limit = int(credits.get("limit", DEFAULT_CREDIT_LIMIT) or DEFAULT_CREDIT_LIMIT)
    st.markdown(f"**🪙 {used} / {limit} credits used**")
    st.progress(min(1.0, used / limit) if limit else 0.0)
    hits = credits.get("cache_hits", 0)
    misses = credits.get("cache_misses", 0)
    st.caption(f"{credits.get('remaining', max(limit - used, 0))} remaining · cache {hits} hits / {misses} misses")


def render_agent_activity(events: list[dict[str, Any]]) -> None:
    """Render a chronological one-line-per-event activity log."""
    lines: list[str] = []
    for event in events:
        described = _describe_event(event)
        if described is None:
            continue
        emoji, text = described
        agent = str(event.get("agent", ""))
        label = AGENT_LABELS.get(agent, agent or "System")
        lines.append(f"{emoji} **{_esc(label)}** — {_esc(text)}")

    if not lines:
        st.caption("⏳ Waiting for the first event…")
        return
    st.markdown("\n\n".join(lines))


def render_verdict_card(verdict: dict[str, Any]) -> None:
    """Render the prominent Sensei's Verdict card."""
    with st.container(border=True):
        st.subheader("🥋 Sensei's Verdict")
        st.markdown(f"## {_esc(verdict.get('recommendation', 'No recommendation'))}")

        confidence = str(verdict.get("confidence", "")).lower()
        st.markdown(_CONFIDENCE_BADGES.get(confidence, f":gray-background[{confidence or 'unknown'} confidence]"))

        action = verdict.get("suggested_action")
        if action:
            st.markdown(f"💡 **{_esc(action)}**")

        price = verdict.get("price_to_pay")
        savings = verdict.get("savings_estimate")
        if price is not None:
            extra = f" · Est. savings {_fmt_price(savings)}" if savings else ""
            st.markdown(f"Price to pay: **{_fmt_price(price)}**{extra}")

        for reason in verdict.get("reasoning") or []:
            st.markdown(f"- {_esc(reason)}")

        model = verdict.get("model_used")
        model_text = "rule-based fallback" if model in (None, "rule-based") else model
        st.caption(f"Model: {model_text}")


def render_price_summary(analysis: dict[str, Any]) -> None:
    """Render Min / Median / Max / Clusters metric cards."""
    cols = st.columns(4)
    cols[0].metric("Min", _fmt_price(analysis.get("global_min_price")))
    cols[1].metric("Median", _fmt_price(analysis.get("global_median_price")))
    cols[2].metric("Max", _fmt_price(analysis.get("global_max_price")))
    cols[3].metric("Clusters", analysis.get("total_clusters", len(analysis.get("clusters", []))))


def render_clusters(analysis: dict[str, Any]) -> None:
    """Render each cluster (sorted by min price) with its product table.

    The backend's cluster dict only guarantees ``best_deal``. If a cluster
    also carries a ``products`` list, all of them are shown; otherwise only
    the best deal row is shown alongside the cluster's price stats.
    """
    clusters = sorted(
        analysis.get("clusters") or [],
        key=lambda c: c.get("min_price") if isinstance(c.get("min_price"), (int, float)) else float("inf"),
    )
    if not clusters:
        render_empty_state("No product clusters to show.", "🗂️")
        return

    st.subheader("Product Clusters")
    for cluster in clusters:
        title = _esc(cluster.get("representative_title", "Untitled cluster"))
        badge = render_deal_flag_badge(str(cluster.get("flag", "")))
        with st.container(border=True):
            st.markdown(f"**{title}** &nbsp; {badge}")
            st.caption(
                f"{cluster.get('listings_count', 0)} listings · "
                f"{_fmt_price(cluster.get('min_price'))} – {_fmt_price(cluster.get('max_price'))} · "
                f"median {_fmt_price(cluster.get('median_price'))} · "
                f"saves {_fmt_price(cluster.get('savings_vs_median'))} vs median"
            )

            best = cluster.get("best_deal") or {}
            products = cluster.get("products") or ([best] if best else [])
            rows = [
                _product_row(
                    p,
                    is_best=bool(best) and p.get("link") == best.get("link") and p.get("price") == best.get("price"),
                )
                for p in sorted(products, key=lambda p: p.get("price") if isinstance(p.get("price"), (int, float)) else float("inf"))
            ]
            if rows:
                _show_table(rows)
            if best.get("link"):
                st.link_button("🏆 View best deal", best["link"])


def render_products_table(products: list[dict[str, Any]]) -> None:
    """Render all products sorted by price ascending."""
    if not products:
        render_empty_state("No products found.", "🛒")
        return
    ordered = sorted(
        products,
        key=lambda p: p.get("price") if isinstance(p.get("price"), (int, float)) else float("inf"),
    )
    rows = []
    for p in ordered:
        row = _product_row(p)
        row.pop("")
        row = {"Title": p.get("title") or "—", **row, "Link": p.get("link")}
        rows.append(row)
    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Price (₹)": st.column_config.NumberColumn("Price (₹)", format="₹%.0f"),
            "Link": st.column_config.LinkColumn("Link", display_text="Open"),
        },
    )


def render_empty_state(message: str, icon: str = "🔍") -> None:
    """Render a centered placeholder."""
    st.markdown(
        f"""
        <div style="text-align:center; padding:3rem 1rem; opacity:0.75;">
            <div style="font-size:3rem;">{icon}</div>
            <div style="font-size:1.1rem; margin-top:0.5rem;">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_deal_flag_badge(flag: str) -> str:
    """Return the badge text for a cluster flag."""
    return _FLAG_BADGES.get(flag, flag.replace("_", " ").title() if flag else "—")