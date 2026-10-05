"""Sensei: turns the Analyst's price summary into a purchase verdict."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from src.config import GEMINI_API_KEY, LLM_MODEL
from src.models.analysis import AnalysisResult, PriceCluster
from src.models.verdict import Verdict

from .base import BaseAgent, Event

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are Sensei, a decisive personal shopping advisor for Indian consumers.
You receive structured price-analysis data and return a purchase verdict.
You MUST reply with valid JSON only, matching this schema exactly:

{
  "recommendation": "2-3 sentences of actionable advice",
  "confidence": "high" | "medium" | "low",
  "reasoning": ["bullet 1", "bullet 2", "bullet 3"],
  "suggested_action": "one imperative sentence"
}

Rules:
- Use Indian Rupee (Rs.) for all prices. Never invent prices not in the data.
- Be decisive: name a specific seller and price in suggested_action.
- If user_budget is provided and the best deal is over budget, say so plainly.
- If confidence is low (e.g. few listings, big price spread), say why in reasoning.
- Do NOT include any text outside the JSON."""

RETRY_SUFFIX = "\n\nReturn ONLY valid JSON, no markdown."
GENERATION_CONFIG: dict[str, Any] = {
    "temperature": 0.3,
    "max_output_tokens": 1024,  # headroom for Gemini 2.5 thinking tokens
    "response_mime_type": "application/json",
}
MAX_CLUSTERS_IN_CONTEXT = 5
CONFIDENCE_LEVELS = {"high", "medium", "low"}
RULE_BASED = "rule-based"
PLACEHOLDER_MARKERS = ("placeholder", "your_", "xxx", "todo", "test")


def _rs(amount: float) -> str:
    """Format a price as Rs.X, keeping paise only when present."""
    return f"Rs.{amount:,.0f}" if float(amount).is_integer() else f"Rs.{amount:,.2f}"


class VerdictAgent(BaseAgent):
    """Asks Gemini for a verdict, with rule-based answers for trivial or failed cases."""

    def __init__(
        self,
        event_queue: asyncio.Queue[Event],
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        """Create Sensei. The Gemini client is built lazily in ``_generate``,
        so importing this module never requires a real key.
        """
        super().__init__("verdict_agent", event_queue)
        self._api_key = api_key or GEMINI_API_KEY
        self._model_name = model or LLM_MODEL
        self._model: Any = None

    async def run(
        self, analysis: AnalysisResult, user_budget: Optional[float] = None
    ) -> Verdict:
        """Produce a purchase verdict. Never raises on LLM failure."""
        await self.emit(
            "verdict_thinking", query=analysis.query, clusters=analysis.total_clusters
        )
        model_used = RULE_BASED

        if analysis.total_clusters == 0 or not analysis.clusters:
            text = {
                "recommendation": "No listings found for this query.",
                "confidence": "low",
                "reasoning": ["Search returned no results"],
                "suggested_action": "Try a different search term.",
            }
        elif len(analysis.clusters) == 1 and analysis.clusters[0].listings_count == 1:
            p = analysis.clusters[0].best_deal
            text = {
                "recommendation": (
                    f"Only one listing found at {_rs(p.price)}. Not enough data to compare."
                ),
                "confidence": "low",
                "reasoning": [
                    "Only one listing was found",
                    "There is nothing to compare its price against",
                ],
                "suggested_action": f"Check the {p.seller} listing at {_rs(p.price)} before deciding.",
            }
        else:
            text, model_used = await self._llm_or_fallback(analysis, user_budget)

        verdict = self._build(analysis, text, model_used)
        await self.emit("verdict_ready", verdict=verdict.to_dict())
        return verdict

    def _key_looks_real(self) -> bool:
        """Reject obvious placeholders so tests skip the LLM instantly."""
        if not self._api_key:
            return False
        return not any(m in self._api_key.lower() for m in PLACEHOLDER_MARKERS)

    async def _llm_or_fallback(
        self, analysis: AnalysisResult, user_budget: Optional[float]
    ) -> tuple[dict[str, Any], str]:
        """Return (verdict text, model_used): Gemini's answer, else rule-based fallback."""
        if not self._key_looks_real():
            await self.emit("verdict_fallback", reason="no_api_key")
            return self._rule_based_text(analysis), RULE_BASED

        context = self._build_context(analysis, user_budget)
        reason = "unparseable_response"
        parsed: Optional[dict[str, Any]] = None
        try:
            async with self.timed_step("Consulting Sensei (LLM)"):
                parsed = await self._consult(context)
        except Exception as exc:
            logger.error("Gemini call failed: %s: %s", type(exc).__name__, exc)
            reason = type(exc).__name__

        if parsed is not None:
            return parsed, self._model_name

        await self.emit("verdict_fallback", reason=reason)
        return self._rule_based_text(analysis), RULE_BASED

    def _rule_based_text(self, analysis: AnalysisResult) -> dict[str, Any]:
        """Build the standard rule-based verdict text."""
        best = self._pick_cluster(analysis.clusters).best_deal
        return {
            "recommendation": (
                f"The lowest price found is {_rs(best.price)} from {best.seller}. "
                "The AI summary was unavailable, so compare the listing details before buying."
            ),
            "confidence": "low",
            "reasoning": [
                f"Cheapest listing: {_rs(best.price)} on {best.seller}",
                f"Median price across listings: {_rs(analysis.global_median_price)}",
                "AI advice was unavailable, so this is a rule-based summary",
            ],
            "suggested_action": f"Review the {best.seller} listing at {_rs(best.price)}.",
        }

    async def _consult(self, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Ask Gemini; retry once with a stricter prompt if the JSON is unusable."""
        base_prompt = json.dumps(context, indent=2)
        prompt = base_prompt
        for attempt in (1, 2):
            text = await asyncio.to_thread(self._generate, prompt)
            parsed = self._parse(text)
            if parsed is not None:
                return parsed
            logger.warning("Sensei output unparseable (attempt %d): %r", attempt, text)
            prompt = base_prompt + RETRY_SUFFIX
        return None

    def _generate(self, prompt: str) -> str:
        """Blocking: lazily build the client, call Gemini, return the response text."""
        if self._model is None:
            import google.generativeai as genai

            genai.configure(api_key=self._api_key)
            self._model = genai.GenerativeModel(
                self._model_name, system_instruction=SYSTEM_INSTRUCTION
            )
        response = self._model.generate_content(prompt, generation_config=GENERATION_CONFIG)
        try:
            return response.text
        except ValueError:
            return ""

    @staticmethod
    def _parse(text: str) -> Optional[dict[str, Any]]:
        """Parse and validate the model's JSON; None if unusable."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").removeprefix("json").strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        rec, action = data.get("recommendation"), data.get("suggested_action")
        if not (isinstance(rec, str) and rec.strip() and isinstance(action, str) and action.strip()):
            return None
        reasoning = data.get("reasoning")
        confidence = str(data.get("confidence", "")).lower()
        return {
            "recommendation": rec.strip(),
            "confidence": confidence if confidence in CONFIDENCE_LEVELS else "low",
            "reasoning": [str(r) for r in reasoning][:5] if isinstance(reasoning, list) else [],
            "suggested_action": action.strip(),
        }

    @staticmethod
    def _build_context(analysis: AnalysisResult, user_budget: Optional[float]) -> dict[str, Any]:
        """Compact summary for the LLM: no raw products."""
        return {
            "query": analysis.query,
            "reference_price": analysis.reference_price,
            "vs_reference": analysis.vs_reference,
            "global_min": analysis.global_min_price,
            "global_median": analysis.global_median_price,
            "global_max": analysis.global_max_price,
            "outliers_removed": analysis.outliers_removed,
            "clusters": [
                {
                    "title": c.representative_title,
                    "flag": c.flag,
                    "min": c.min_price,
                    "median": c.median_price,
                    "max": c.max_price,
                    "listings": c.listings_count,
                    "engines": c.engines,
                    "best_seller": c.best_deal.seller,
                    "best_price": c.best_deal.price,
                }
                for c in analysis.clusters[:MAX_CLUSTERS_IN_CONTEXT]
            ],
            "user_budget": user_budget,
        }

    @staticmethod
    def _pick_cluster(clusters: list[PriceCluster]) -> PriceCluster:
        """Prefer BEST_DEAL clusters, else all; take the lowest min_price."""
        pool = [c for c in clusters if c.flag == "BEST_DEAL"] or clusters
        return min(pool, key=lambda c: c.min_price)

    def _build(self, analysis: AnalysisResult, text: dict[str, Any], model_used: str) -> Verdict:
        """Combine verdict text with the rule-picked best product and savings."""
        cluster = self._pick_cluster(analysis.clusters) if analysis.clusters else None
        best = cluster.best_deal if cluster else None
        return Verdict(
            recommendation=text["recommendation"],
            confidence=text["confidence"],
            reasoning=text["reasoning"],
            suggested_action=text["suggested_action"],
            best_product=best,
            best_cluster_title=cluster.representative_title if cluster else None,
            price_to_pay=best.price if best else None,
            savings_estimate=(
                round(analysis.global_median_price - best.price, 2) if best else None
            ),
            vs_reference=analysis.vs_reference,
            model_used=model_used,
            generated_at=time.time(),
        )