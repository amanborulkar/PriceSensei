"""Streamlit rendering helpers for PriceSensei.

Every function takes plain dicts (the JSON shapes produced by the FastAPI
backend) and renders UI. Only streamlit and the standard library are used.

Styling notes:
  * ``render_header`` injects the global stylesheet once per script run, so
    call it before any other renderer (``app.py`` already does).
  * HTML blocks are flattened to a single line (see ``_html``) because
    Streamlit's Markdown parser turns indented lines or blank lines inside
    raw HTML into code blocks.
  * Every styled block also carries its information as plain text (status
    labels, badge text, numbers), so nothing is conveyed by colour alone.
"""

from __future__ import annotations

import html
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

# Flag -> short tone key used in CSS class / container-key names.
_FLAG_TONES: dict[str, str] = {
    "BEST_DEAL": "best",
    "FAIR_PRICE": "fair",
    "OVERPRICED": "over",
}

_CONFIDENCE_LABELS: dict[str, str] = {
    "high": "🟢 High confidence",
    "medium": "🟡 Medium confidence",
    "low": "🔴 Low confidence",
}

# (agent key, icon, fallback label) in pipeline order.
_AGENT_ORDER: tuple[tuple[str, str, str], ...] = (
    ("search_agent", "🔍", "Scout"),
    ("analysis_agent", "📊", "Analyst"),
    ("verdict_agent", "🥋", "Sensei"),
)

_STATE_LABELS: dict[str, str] = {
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
    "waiting": "Waiting",
}

_SKIPPED_EVENTS = {"step_started", "step_complete", "pipeline_done"}


# --------------------------------------------------------------------------- #
# Global stylesheet
# --------------------------------------------------------------------------- #
_CSS = """
.stApp { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.block-container { padding-top: 2.5rem; }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
@keyframes pulseGlow {
  0%, 100% { box-shadow: 0 0 20px rgba(34,197,94,0.3); }
  50% { box-shadow: 0 0 40px rgba(34,197,94,0.6); }
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Hero */
.hero {
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  padding: 2rem 2.5rem; border-radius: 16px; margin-bottom: 1.5rem;
  border-left: 4px solid #f59e0b; box-shadow: 0 10px 30px rgba(0,0,0,0.2);
  animation: fadeInUp 0.5s ease-out both;
}
.hero-title { color: #f8fafc; margin: 0; font-size: 2.4rem; font-weight: 700; line-height: 1.2; }
.hero-tagline { color: #94a3b8; margin: 0.5rem 0 0 0; font-size: 1.1rem; }
.hero-meta { color: #64748b; margin: 1rem 0 0 0; font-size: 0.85rem; }

/* Agent status cards */
.agent-card {
  background: #1e293b; border: 1px solid #334155; border-top: 3px solid #64748b;
  border-radius: 12px; padding: 0.9rem 1rem; min-height: 104px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.agent-running { border-top-color: #eab308; }
.agent-done { border-top-color: #22c55e; }
.agent-failed { border-top-color: #ef4444; }
.agent-title { color: #f8fafc; font-weight: 600; font-size: 1rem; }
.agent-status { color: #cbd5e1; font-size: 0.85rem; margin-top: 0.45rem; display: flex; align-items: center; }
.agent-line { color: #94a3b8; font-size: 0.8rem; margin-top: 0.3rem; min-height: 1.1em; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; flex: none; }
.dot-waiting { background: #64748b; }
.dot-running { background: #eab308; animation: pulse 1.2s ease-in-out infinite; }
.dot-done { background: #22c55e; }
.dot-failed { background: #ef4444; }

/* Verdict card */
.verdict {
  border-radius: 16px; padding: 1.5rem 1.75rem; margin: 1rem 0 1.25rem 0;
  overflow-wrap: anywhere; animation: fadeInUp 0.6s ease-out both;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.verdict-high {
  background: linear-gradient(135deg, #064e3b 0%, #065f46 100%);
  border: 2px solid #22c55e; color: #f0fdf4; box-shadow: 0 0 20px rgba(34,197,94,0.3);
  animation: fadeInUp 0.6s ease-out both, pulseGlow 3s ease-in-out 0.6s infinite;
}
.verdict-medium {
  background: linear-gradient(135deg, #78350f 0%, #92400e 100%);
  border: 2px solid #eab308; color: #fffbeb; box-shadow: 0 0 24px rgba(234,179,8,0.25);
}
.verdict-low {
  background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
  border: 2px solid #64748b; color: #f8fafc; box-shadow: 0 4px 14px rgba(0,0,0,0.25);
}
.verdict-kicker { font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.8; }
.verdict-rec { font-size: 1.6rem; font-weight: 700; line-height: 1.3; margin: 0.4rem 0 0.8rem 0; }
.verdict-pill {
  display: inline-block; padding: 0.2rem 0.75rem; border-radius: 999px;
  background: rgba(255,255,255,0.14); font-size: 0.85rem; font-weight: 600;
}
.verdict-action { margin-top: 1rem; font-size: 1.05rem; font-weight: 600; }
.verdict-price { margin-top: 0.4rem; opacity: 0.95; }
.verdict-reasons { margin: 0.9rem 0 0 0; padding-left: 1.2rem; }
.verdict-reasons li { margin: 0.3rem 0; }
.verdict-model { margin-top: 1rem; font-size: 0.75rem; opacity: 0.65; }

/* Cluster cards: st.container(key="cluster-<tone>-<n>") exposes the class st-key-cluster-<tone>-<n> */
[class*="st-key-cluster-"] {
  background: rgba(148,163,184,0.10); border-left: 5px solid #94a3b8;
  border-radius: 8px; padding: 1rem 1rem 0.75rem 1rem; margin-bottom: 0.75rem; gap: 0.6rem;
}
[class*="st-key-cluster-best-"] { border-left-color: #22c55e; }
[class*="st-key-cluster-fair-"] { border-left-color: #94a3b8; }
[class*="st-key-cluster-over-"] { border-left-color: #ef4444; }
.cluster-title { font-weight: 700; }
.cluster-stats { color: #64748b; font-size: 0.85rem; margin-top: 0.25rem; }
.flag-badge {
  display: inline-block; margin-left: 0.5rem; padding: 0.1rem 0.6rem; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; background: rgba(148,163,184,0.18); color: #64748b;
}
.flag-best { background: rgba(34,197,94,0.16); color: #16a34a; }
.flag-over { background: rgba(239,68,68,0.14); color: #dc2626; }

/* Dataframes: soft border that matches the theme */
[data-testid="stDataFrame"] {
  border: 1px solid rgba(148,163,184,0.25); border-radius: 8px; overflow: hidden;
}

/* Primary form button in the brand amber */
[data-testid="stFormSubmitButton"] button {
  background: #f59e0b; color: #0f172a; border: none; font-weight: 600;
}
[data-testid="stFormSubmitButton"] button:hover { background: #d97706; color: #0f172a; }

@media (max-width: 640px) {
  .hero { padding: 1.25rem 1.25rem; }
  .hero-title { font-size: 1.8rem; }
  .verdict { padding: 1.1rem 1.15rem; }
  .verdict-rec { font-size: 1.3rem; }
}
@media (prefers-reduced-motion: reduce) {
  .hero, .verdict, .dot-running { animation: none !important; }
}
"""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _fmt_price(value: Any) -> str:
    """Format a number as INR, or an em dash when missing."""
    if isinstance(value, (int, float)):
        return f"₹{value:,.0f}"
    return "—"


def _fmt_signed(value: float) -> str:
    """Format a possibly-negative INR amount, e.g. '−₹1,200'."""
    return f"−₹{abs(value):,.0f}" if value < 0 else f"₹{value:,.0f}"


def _esc(text: Any) -> str:
    """Escape characters Streamlit markdown would treat as LaTeX."""
    return str(text).replace("$", "\\$")


def _h(text: Any) -> str:
    """Escape text for raw HTML (also neutralises '$' so it is never read as LaTeX)."""
    return html.escape(str(text)).replace("$", "&#36;")


def _html(markup: str) -> str:
    """Flatten markup to one line so Markdown never sees indentation or blank lines."""
    return "".join(line.strip() for line in markup.strip().splitlines())


def _clip(text: Any, limit: int = 48) -> str:
    """Shorten text for one-line status labels."""
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


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
    if etype == "relevance_filtered":
        return "🧹", f"Filtered out {event.get('dropped', 0)} irrelevant listings ({event.get('kept', 0)} kept)"
    if etype == "relevance_filter_skipped":
        return "⚠️", "Relevance filter skipped (would remove everything)"
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
    if etype == "rate_limit_warning":
        return "⚡", f"Rate limit nearly reached — {event.get('ip_remaining', '?')} left in window"
    if etype == "pipeline_complete":
        return "✅", (
            f"Done in {event.get('elapsed', 0):.1f}s "
            f"({event.get('product_count', 0)} products, confidence: {event.get('confidence', '—')})"
        )
    if etype == "pipeline_failed":
        return "❌", f"Pipeline failed: {event.get('error', 'unknown error')}"

    if "fail" in etype or "error" in etype:
        return "❌", etype.replace("_", " ")
    if "fallback" in etype:
        return "⚠️", etype.replace("_", " ")
    if "complete" in etype or "ready" in etype:
        return "✅", etype.replace("_", " ")
    if "thinking" in etype:
        return "🧠", etype.replace("_", " ")
    return "🔍", etype.replace("_", " ")


def _inject_css() -> None:
    """Emit the global stylesheet (fonts, component classes, keyframes)."""
    st.markdown(f"<style>{_html(_CSS)}</style>", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Agent status derivation
# --------------------------------------------------------------------------- #
def _agent_statuses(events: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """Scan events and return ``{agent_key: (state, one_line_status)}``.

    ``state`` is one of ``waiting``, ``running``, ``done`` or ``failed``.
    """
    last: dict[str, dict[str, Any]] = {}
    failures = 0
    querying = False
    for ev in events:
        etype = str(ev.get("type", ""))
        last[etype] = ev
        if etype == "engine_failed":
            failures += 1
        elif etype == "step_started" and any(
            isinstance(v, str) and v.startswith("Querying") for v in ev.values()
        ):
            querying = True

    pipeline_failed = "pipeline_failed" in last
    ended = bool(last.keys() & {"pipeline_done", "pipeline_complete"})

    # ---- Scout ---------------------------------------------------------
    expected: int | None = None
    started = last.get("search_started")
    if started:
        engines = started.get("engines")
        if isinstance(engines, (list, tuple)) and engines:
            expected = len(engines)
    engines_text = (
        f"{expected} engine{'s' if expected != 1 else ''}" if expected else "shopping engines"
    )

    if "search_complete" in last:
        total = last["search_complete"].get("total", 0)
        line = f"Found {total} products"
        if failures:
            line += f" · {failures} engine failed" if failures == 1 else f" · {failures} engines failed"
        scout = ("done", line)
    elif failures and (pipeline_failed or ended or expected is None or failures >= expected):
        err = last["engine_failed"].get("error", "unknown error")
        scout = ("failed", _clip(f"Engine failed: {err}"))
    elif started or querying:
        line = f"Searching {engines_text}"
        if failures:
            line += f" · {failures} failed"
        scout = ("running", line)
    else:
        scout = ("waiting", "")

    # ---- Analyst -------------------------------------------------------
    if "analysis_complete" in last:
        ev = last["analysis_complete"]
        analyst = (
            "done",
            f"{ev.get('clusters', 0)} clusters · median {_fmt_price(ev.get('global_median'))}",
        )
    elif "analysis_empty" in last:
        analyst = ("failed", "No products to analyse")
    elif "clusters_formed" in last:
        analyst = ("running", f"Scoring {last['clusters_formed'].get('count', 0)} clusters")
    elif "analysis_started" in last:
        count = last["analysis_started"].get("product_count", 0)
        for key in ("relevance_filtered", "outliers_flagged"):
            if key in last and isinstance(last[key].get("kept"), int):
                count = last[key]["kept"]
        analyst = ("running", f"Clustering {count} listings")
    else:
        analyst = ("waiting", "")

    # ---- Sensei --------------------------------------------------------
    if "verdict_ready" in last:
        sensei = ("done", "Verdict ready" + (" · rule-based" if "verdict_fallback" in last else ""))
    elif "verdict_fallback" in last:
        sensei = ("done", "Rule-based verdict")
    elif "verdict_thinking" in last:
        sensei = ("running", "Weighing the evidence")
    else:
        sensei = ("waiting", "")

    statuses = {
        "search_agent": scout,
        "analysis_agent": analyst,
        "verdict_agent": sensei,
    }

    # A failed pipeline fails whichever agent was mid-flight (or the next one in line).
    if pipeline_failed:
        error = _clip(last["pipeline_failed"].get("error", "Pipeline failed"))
        keys = [k for k, _, _ in _AGENT_ORDER]
        running = [k for k in keys if statuses[k][0] == "running"]
        target = running[0] if running else next(
            (k for k in keys if statuses[k][0] == "waiting"), None
        )
        if target:
            statuses[target] = ("failed", error)
    return statuses


def _agent_card_html(icon: str, label: str, state: str, line: str) -> str:
    """Build the HTML for a single agent status card."""
    return _html(
        f"""
        <div class="agent-card agent-{state}">
        <div class="agent-title">{_h(icon)} {_h(label)}</div>
        <div class="agent-status"><span class="dot dot-{state}"></span>{_STATE_LABELS[state]}</div>
        <div class="agent-line">{_h(line) if line else "&nbsp;"}</div>
        </div>
        """
    )


# --------------------------------------------------------------------------- #
# Public renderers
# --------------------------------------------------------------------------- #
def render_header() -> None:
    """Inject the global CSS and render the dark hero header."""
    _inject_css()
    st.markdown(
        _html(
            f"""
            <div class="hero">
            <div class="hero-title">{_h(APP_EMOJI)} {_h(APP_NAME)}</div>
            <div class="hero-tagline">{_h(APP_TAGLINE)}</div>
            <div class="hero-meta">3 AI agents · 2 shopping engines · verdict in ~15s</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def render_rate_limit_meter(rate_limit: dict[str, Any] | None) -> None:
    """Render a compact rate-limit indicator, or nothing if info is missing."""
    if not rate_limit:
        return
    ip_left = rate_limit.get("ip_remaining")
    global_left = rate_limit.get("global_remaining")
    window_min = int((rate_limit.get("window_seconds", 1800)) // 60)

    if not isinstance(ip_left, int):
        return

    if ip_left <= 1:
        st.warning(f"⚡ Only {ip_left} query left in this {window_min} min window")
    else:
        st.caption(
            f"⚡ Queries left: {ip_left} (of {window_min} min window"
            + (f", {global_left} global" if isinstance(global_left, int) else "")
            + ")"
        )


def render_credit_meter(credits: dict[str, Any]) -> None:
    """Render the SerpApi credit usage as a dark card with a coloured bar."""
    used = int(credits.get("used", 0) or 0)
    limit = int(credits.get("limit", DEFAULT_CREDIT_LIMIT) or DEFAULT_CREDIT_LIMIT)
    percent = min(100.0, used / limit * 100) if limit else 0.0
    if percent < 50:
        bar_color = "#22c55e"
    elif percent <= 80:
        bar_color = "#eab308"
    else:
        bar_color = "#ef4444"

    remaining = credits.get("remaining", max(limit - used, 0))
    hits = credits.get("cache_hits", 0)
    misses = credits.get("cache_misses", 0)

    # Inline styles on purpose: the meter must look right even without the global CSS.
    st.markdown(
        _html(
            f"""
            <div style="background: #1e293b; padding: 0.9rem; border-radius: 10px; margin-bottom: 0.75rem;">
            <div style="color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em;">SerpApi credits</div>
            <div style="color: #f8fafc; font-size: 1.8rem; font-weight: 700; line-height: 1.2; margin-top: 4px;">{used}<span style="color: #64748b; font-size: 1rem; font-weight: 400;"> / {limit}</span></div>
            <div style="background: #334155; height: 6px; border-radius: 3px; margin-top: 8px; overflow: hidden;">
            <div style="background: {bar_color}; height: 6px; width: {percent:.1f}%; border-radius: 3px; transition: width 0.4s ease;"></div>
            </div>
            <div style="color: #64748b; font-size: 0.75rem; margin-top: 6px;">{_h(remaining)} remaining · {_h(hits)} hits / {_h(misses)} misses</div>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )
    render_rate_limit_meter(credits.get("rate_limit"))


def render_agent_activity(events: list[dict[str, Any]]) -> None:
    """Render three agent status cards, then the chronological event log."""
    statuses = _agent_statuses(events)
    for col, (key, icon, default_label) in zip(st.columns(3), _AGENT_ORDER):
        state, line = statuses[key]
        with col:
            st.markdown(
                _agent_card_html(icon, AGENT_LABELS.get(key, default_label), state, line),
                unsafe_allow_html=True,
            )

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
    """Render the prominent Sensei's Verdict card, colour-coded by confidence."""
    raw_confidence = verdict.get("confidence")
    confidence = str(raw_confidence).strip().lower()
    if confidence in _CONFIDENCE_LABELS:
        tone = confidence
        pill = _CONFIDENCE_LABELS[confidence]
    else:
        tone = "low"
        pill = str(raw_confidence) if raw_confidence not in (None, "") else "Confidence unknown"

    parts: list[str] = [
        '<div class="verdict-kicker">🥋 Sensei\'s Verdict</div>',
        f'<div class="verdict-rec">{_h(verdict.get("recommendation", "No recommendation"))}</div>',
        f'<span class="verdict-pill">{_h(pill)}</span>',
    ]

    action = verdict.get("suggested_action")
    if action:
        parts.append(f'<div class="verdict-action">💡 {_h(action)}</div>')

    price = verdict.get("price_to_pay")
    savings = verdict.get("savings_estimate")
    if price is not None:
        extra = ""
        if isinstance(savings, (int, float)) and savings:
            extra = f" · Est. savings {_h(_fmt_signed(savings))}"
        parts.append(f'<div class="verdict-price">Price to pay: <b>{_h(_fmt_price(price))}</b>{extra}</div>')

    reasoning = verdict.get("reasoning")
    if isinstance(reasoning, list) and reasoning:
        items = "".join(f"<li>{_h(r)}</li>" for r in reasoning)
        parts.append(f'<ul class="verdict-reasons">{items}</ul>')

    model = verdict.get("model_used")
    model_text = "rule-based fallback" if model in (None, "rule-based") else model
    parts.append(f'<div class="verdict-model">Model: {_h(model_text)}</div>')

    st.markdown(
        _html(f'<div class="verdict verdict-{tone}">{"".join(parts)}</div>'),
        unsafe_allow_html=True,
    )


def render_price_summary(analysis: dict[str, Any]) -> None:
    """Render Min / Median / Max / Clusters metric cards."""
    cols = st.columns(4)
    cols[0].metric("Min", _fmt_price(analysis.get("global_min_price")))
    cols[1].metric("Median", _fmt_price(analysis.get("global_median_price")))
    cols[2].metric("Max", _fmt_price(analysis.get("global_max_price")))
    cols[3].metric("Clusters", analysis.get("total_clusters", len(analysis.get("clusters", []))))


def _cluster_container(index: int, tone: str) -> Any:
    """Return a container the global CSS can colour by deal flag.

    ``st.container(key=...)`` (Streamlit >= 1.39) adds an ``st-key-<key>``
    class we style; older versions fall back to a plain bordered container.
    """
    try:
        return st.container(key=f"cluster-{tone}-{index}")
    except TypeError:
        return st.container(border=True)


def _savings_text(savings: Any) -> str:
    """Describe the min-vs-median gap in words (negative means cheaper)."""
    if not isinstance(savings, (int, float)) or savings == 0:
        return "at median"
    if savings < 0:
        return f"saves {_fmt_price(-savings)} vs median"
    return f"{_fmt_price(savings)} above median"


def render_clusters(analysis: dict[str, Any]) -> None:
    """Render each cluster (sorted by min price) as a colour-banded card with its table."""
    clusters = sorted(
        analysis.get("clusters") or [],
        key=lambda c: c.get("min_price") if isinstance(c.get("min_price"), (int, float)) else float("inf"),
    )
    if not clusters:
        render_empty_state("No product clusters to show.", "🗂️")
        return

    st.subheader("Product Clusters")
    for index, cluster in enumerate(clusters):
        try:
            flag = str(cluster.get("flag", ""))
            tone = _FLAG_TONES.get(flag, "fair")
            title = _h(cluster.get("representative_title", "Untitled cluster"))
            badge = _h(render_deal_flag_badge(flag))
            stats = (
                f"{_h(cluster.get('listings_count', 0))} listings · "
                f"{_h(_fmt_price(cluster.get('min_price')))} – {_h(_fmt_price(cluster.get('max_price')))} · "
                f"median {_h(_fmt_price(cluster.get('median_price')))} · "
                f"{_h(_savings_text(cluster.get('savings_vs_median')))}"
            )
            with _cluster_container(index, tone):
                st.markdown(
                    _html(
                        f"""
                        <div><span class="cluster-title">{title}</span><span class="flag-badge flag-{tone}">{badge}</span></div>
                        <div class="cluster-stats">{stats}</div>
                        """
                    ),
                    unsafe_allow_html=True,
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
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not render cluster: {e}")
            continue


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