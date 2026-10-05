"""Analyst: removes price outliers, clusters equivalent listings, scores deals."""

from __future__ import annotations

import asyncio
import re
import statistics
from typing import Optional

from thefuzz import fuzz

from src.models.analysis import AnalysisResult, PriceCluster
from src.models.product import Product

from .base import BaseAgent, Event

# Words that don't affect product identity (brands, colors, filler).
_STOPWORDS = frozenset({
    "apple", "samsung", "google", "oneplus", "xiaomi", "redmi", "realme",
    "new", "original", "brand", "sealed", "latest", "the", "a", "an", "for",
    "with", "and",
    "black", "white", "blue", "silver", "gold", "gray", "grey", "green",
    "red", "pink", "purple", "yellow", "titanium", "midnight", "starlight",
    "natural", "desert", "onyx", "marble", "cobalt", "violet",
})

# Model markers that DO matter.
_MODEL_MARKERS = frozenset({
    "pro", "plus", "max", "ultra", "mini", "lite", "air", "fe",
})

# Connectivity/tech markers that don't distinguish products in practice.
_NOISE_MARKERS = frozenset({"5g", "4g", "3g", "lte", "wifi", "wlan", "nfc"})

# Split a number immediately followed by a unit suffix: "128gb" -> "128 gb".
# Note: bare "g" is intentionally excluded — it would match "5g" (fifth-gen)
# and incorrectly split it into "5 g".
_UNIT_SPLIT = re.compile(r"(\d+)\s*(gb|tb|mb|inch|mm|cm|kg)\b", re.IGNORECASE)

# Bracket characters normalized to spaces.
_BRACKETS = re.compile(r"[\(\)\[\]\{\}]")

# Connectivity markers that should be dropped from the title entirely.
_CONNECTIVITY = re.compile(r"\b(5g|4g|3g|lte|wifi|wlan|nfc)\b", re.IGNORECASE)


class AnalysisAgent(BaseAgent):
    """Groups listings of the same product and flags the good deals."""

    def __init__(
        self,
        event_queue: asyncio.Queue[Event],
        fuzzy_threshold: int = 85,
    ) -> None:
        """Create Analyst.

        Args:
            event_queue: Shared queue drained by the SSE consumer.
            fuzzy_threshold: Minimum token_set_ratio (0-100) for two titles
                to be treated as the same product, given matching numeric
                tokens.
        """
        super().__init__("analysis_agent", event_queue)
        self._fuzzy_threshold = fuzzy_threshold

    async def run(
        self,
        query: str,
        products: list[Product],
        reference_price: Optional[float] = None,
    ) -> AnalysisResult:
        """Analyse a product list and return clustered price statistics.

        Never raises on empty input: emits ``analysis_empty`` and returns a
        zeroed result instead.
        """
        await self.emit("analysis_started", query=query, product_count=len(products))

        if not products:
            await self.emit("analysis_empty", query=query)
            return AnalysisResult(
                query=query,
                total_products=0,
                total_clusters=0,
                clusters=[],
                global_min_price=0.0,
                global_median_price=0.0,
                global_max_price=0.0,
                best_overall_deal=None,
                reference_price=reference_price,
                vs_reference=None,
                outliers_removed=0,
            )

        kept = self._remove_outliers(products)
        removed = len(products) - len(kept)
        if removed:
            await self.emit("outliers_flagged", removed=removed, kept=len(kept))

        async with self.timed_step("Clustering listings"):
            groups = self._cluster(kept)
        await self.emit("clusters_formed", count=len(groups))

        clusters = sorted(
            (self._build_cluster(g) for g in groups), key=lambda c: c.min_price
        )
        prices = [p.price for p in kept]
        best_overall = min(kept, key=lambda p: p.price)
        g_min, g_max = min(prices), max(prices)
        g_median = round(statistics.median(prices), 2)

        vs_reference: Optional[str] = None
        if reference_price is not None and reference_price > 0:
            if g_min < reference_price * 0.95:
                vs_reference = "under"
            elif g_min > reference_price * 1.05:
                vs_reference = "over"
            else:
                vs_reference = "near"
            await self.emit(
                "reference_comparison",
                reference=reference_price,
                min_price=g_min,
                verdict=vs_reference,
            )

        result = AnalysisResult(
            query=query,
            total_products=len(kept),
            total_clusters=len(clusters),
            clusters=clusters,
            global_min_price=g_min,
            global_median_price=g_median,
            global_max_price=g_max,
            best_overall_deal=best_overall,
            reference_price=reference_price,
            vs_reference=vs_reference,
            outliers_removed=removed,
        )
        await self.emit(
            "analysis_complete",
            query=query,
            clusters=len(clusters),
            global_min=g_min,
            global_median=g_median,
            best_deal_price=best_overall.price,
        )
        return result

    @staticmethod
    def _remove_outliers(products: list[Product]) -> list[Product]:
        """Drop listings outside the 1.5*IQR fences (Tukey's rule)."""
        if len(products) < 4:
            return list(products)
        q1, _, q3 = statistics.quantiles(
            [p.price for p in products], n=4, method="inclusive"
        )
        iqr = q3 - q1
        if iqr == 0:
            return list(products)
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return [p for p in products if low <= p.price <= high]

    @classmethod
    def _normalize_title(cls, title: str) -> str:
        """Lowercase, split units, drop filler words, and strip punctuation.

        Examples:
          "Apple iPhone 15 128GB Black"     -> "iphone 15 128 gb"
          "iPhone 15 128 GB (Blue)"         -> "iphone 15 128 gb"
          "Apple iPhone 15 (128GB) - Pink"  -> "iphone 15 128 gb"
          "Samsung Galaxy S24 5G 256GB"     -> "galaxy s24 256 gb"
        """
        text = title.lower()
        # Drop connectivity markers BEFORE unit splitting, so "5g" doesn't
        # become "5 g" (the unit regex would otherwise treat trailing "g" as grams).
        text = _CONNECTIVITY.sub(" ", text)
        text = _BRACKETS.sub(" ", text)          # "(128GB)" -> " 128GB "
        text = _UNIT_SPLIT.sub(r"\1 \2", text)   # "128gb" -> "128 gb"
        text = re.sub(r"[^\w\s]", " ", text)     # strip remaining punctuation
        words = [w for w in text.split() if w not in _STOPWORDS]
        return " ".join(words)

    @classmethod
    def _signature(cls, title: str) -> frozenset[str]:
        """Return the numeric + model-marker tokens that must match exactly.

        Computed on the *normalized* title, so units are already split.
        Connectivity markers like 5G are dropped.
        """
        normalized = cls._normalize_title(title)
        sig: set[str] = set()
        for tok in normalized.split():
            if tok in _NOISE_MARKERS:
                continue
            if any(c.isdigit() for c in tok) or tok in _MODEL_MARKERS:
                sig.add(tok)
        return frozenset(sig)

    def _cluster(self, products: list[Product]) -> list[list[Product]]:
        """Greedy clustering on normalized titles + signature guard.

        Two titles cluster only if:
          1. Their signatures (numeric + model markers) match exactly
          2. AND token_set_ratio on normalized titles >= self._fuzzy_threshold
        """
        reps: list[str] = []
        sigs: list[frozenset[str]] = []
        groups: list[list[Product]] = []

        for product in products:
            normalized = self._normalize_title(product.title)
            sig = self._signature(product.title)

            for i, rep in enumerate(reps):
                if sig != sigs[i]:
                    continue
                if fuzz.token_set_ratio(normalized, rep) >= self._fuzzy_threshold:
                    groups[i].append(product)
                    break
            else:
                reps.append(normalized)
                sigs.append(sig)
                groups.append([product])
        return groups

    @staticmethod
    def _build_cluster(group: list[Product]) -> PriceCluster:
        """Compute price statistics and the deal flag for one cluster."""
        prices = [p.price for p in group]
        low, high = min(prices), max(prices)
        median = statistics.median(prices)
        savings = low - median

        if len(group) >= 3 and savings < -0.05 * median:
            flag = "BEST_DEAL"
        elif low > 1.15 * median:
            flag = "OVERPRICED"
        else:
            flag = "FAIR_PRICE"

        return PriceCluster(
            representative_title=group[0].title,
            products=group,
            min_price=low,
            max_price=high,
            median_price=round(median, 2),
            mean_price=round(statistics.mean(prices), 2),
            savings_vs_median=round(savings, 2),
            listings_count=len(group),
            engines=sorted({p.engine for p in group}),
            best_deal=min(group, key=lambda p: p.price),
            flag=flag,
        )