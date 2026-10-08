"""Analyst: filters irrelevant listings, removes price outliers, clusters equivalent listings, scores deals."""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
from typing import Optional

from thefuzz import fuzz

from src.models.analysis import AnalysisResult, PriceCluster
from src.models.product import Product

from .base import BaseAgent, Event

logger = logging.getLogger(__name__)

_STOPWORDS = frozenset({
    "apple", "samsung", "google", "oneplus", "xiaomi", "redmi", "realme",
    "new", "original", "brand", "sealed", "latest", "the", "a", "an", "for",
    "with", "and",
    "black", "white", "blue", "silver", "gold", "gray", "grey", "green",
    "red", "pink", "purple", "yellow", "titanium", "midnight", "starlight",
    "natural", "desert",
})

_MODEL_MARKERS = frozenset({
    "pro", "plus", "max", "ultra", "mini", "lite", "air", "fe",
})

_UNIT_SPLIT = re.compile(r"(\d+)\s*(gb|tb|mb|inch|mm|cm|kg|g)\b", re.IGNORECASE)

# Matched against text that has already been through _normalize_for_match
# (lowercase, punctuation -> spaces, units split as "128 gb").
# NOTE: "glass" and "tempered" are intentionally NOT standalone terms to avoid
# dropping listings that mention "Gorilla Glass". Only "tempered glass" and
# "screen glass" as phrases count as accessories.
_ACCESSORY_RE = re.compile(
    r"\b(case|cover|charger|cable|protector|screen guard|adapter|stand|holder|"
    r"strap|pouch|sleeve|skin|tempered glass|screen glass|glass protector|"
    r"earbud|headphone|watch band)s?\b"
)
_CONDITION_RE = re.compile(
    r"\b(renewed|refur\w*|pre ?owned|used|second ?hand|fair|good|open ?box)\b"
)
_STORAGE_UNITS = frozenset({"gb", "tb", "mb"})
_MAX_LOGGED_DROPS = 5


class AnalysisAgent(BaseAgent):
    """Groups listings of the same product and flags the good deals."""

    def __init__(
        self,
        event_queue: asyncio.Queue[Event],
        fuzzy_threshold: int = 85,
    ) -> None:
        super().__init__("analysis_agent", event_queue)
        self._fuzzy_threshold = fuzzy_threshold
        # Set by _filter_relevant when it fell back to the unfiltered list.
        self._relevance_skipped: bool = False

    async def run(
        self,
        query: str,
        products: list[Product],
        reference_price: Optional[float] = None,
    ) -> AnalysisResult:
        await self.emit("analysis_started", query=query, product_count=len(products))

        if not products:
            await self.emit("analysis_empty", query=query)
            return AnalysisResult(
                query=query, total_products=0, total_clusters=0, clusters=[],
                global_min_price=0.0, global_median_price=0.0, global_max_price=0.0,
                best_overall_deal=None, reference_price=reference_price,
                vs_reference=None, outliers_removed=0,
            )

        filtered = self._filter_relevant(query, products)
        if self._relevance_skipped:
            await self.emit("relevance_filter_skipped", reason="no_products_left")
        elif len(filtered) < len(products):
            kept_ids = {id(p) for p in filtered}
            await self.emit(
                "relevance_filtered",
                kept=len(filtered),
                dropped=len(products) - len(filtered),
                example_dropped=[p.title for p in products if id(p) not in kept_ids][:3],
            )

        kept = self._remove_outliers(filtered)
        removed = len(filtered) - len(kept)
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
                reference=reference_price, min_price=g_min, verdict=vs_reference,
            )

        result = AnalysisResult(
            query=query, total_products=len(kept), total_clusters=len(clusters),
            clusters=clusters, global_min_price=g_min, global_median_price=g_median,
            global_max_price=g_max, best_overall_deal=best_overall,
            reference_price=reference_price, vs_reference=vs_reference,
            outliers_removed=removed,
            filtered_products=kept,
        )
        await self.emit(
            "analysis_complete", query=query, clusters=len(clusters),
            global_min=g_min, global_median=g_median, best_deal_price=best_overall.price,
        )
        return result

    # ------------------------------------------------------------------
    # Relevance filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_match(text: str) -> str:
        """Lowercase, split units ("128GB" -> "128 gb"), strip punctuation."""
        text = text.lower()
        text = _UNIT_SPLIT.sub(r"\1 \2", text)
        text = re.sub(r"[^\w\s]", " ", text)
        return " ".join(text.split())

    @staticmethod
    def _canonical_term(term: str) -> str:
        """Canonical key for an accessory/condition term (spacing/variants folded)."""
        key = term.replace(" ", "")
        return "refurbished" if key.startswith("refur") else key

    @classmethod
    def _flag_terms(cls, normalized: str) -> tuple[frozenset[str], frozenset[str]]:
        """Return (accessory_terms, condition_terms) found in normalized text."""
        accessories = frozenset(
            cls._canonical_term(m.group(1)) for m in _ACCESSORY_RE.finditer(normalized)
        )
        conditions = frozenset(
            cls._canonical_term(m.group(1)) for m in _CONDITION_RE.finditer(normalized)
        )
        return accessories, conditions

    @classmethod
    def _query_tokens(cls, normalized_query: str) -> list[tuple[str, ...]]:
        """Extract meaningful query tokens, each as a tuple of accepted variants.

        Accessory/condition words and stopwords are ignored. A token is kept
        if it is longer than 2 chars or contains a digit (so "15" survives).
        "128GB" yields the variants ("128gb", "128 gb", "128").
        """
        text = _ACCESSORY_RE.sub(" ", normalized_query)
        text = _CONDITION_RE.sub(" ", text)
        text = _UNIT_SPLIT.sub(r"\1\2", text)  # "128 gb" -> "128gb"

        tokens: list[tuple[str, ...]] = []
        seen: set[str] = set()
        for tok in text.split():
            if tok in _STOPWORDS or tok in seen:
                continue
            if len(tok) <= 2 and not any(c.isdigit() for c in tok):
                continue
            seen.add(tok)
            variants = [tok]
            m = re.fullmatch(r"(\d+)(gb|tb|mb|inch|mm|cm|kg|g)", tok)
            if m:
                number, unit = m.groups()
                variants.append(f"{number} {unit}")
                if unit in _STORAGE_UNITS:
                    variants.append(number)
            tokens.append(tuple(variants))
        return tokens

    @staticmethod
    def _contains_term(haystack: str, term: str) -> bool:
        """Substring match for words; boundary-aware match for numeric terms."""
        if any(c.isdigit() for c in term):
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            return re.search(pattern, haystack) is not None
        return term in haystack

    def _relevance_drop_reason(
        self,
        title: str,
        allowed_terms: frozenset[str],
        token_variants: list[tuple[str, ...]],
        required_matches: int,
    ) -> Optional[str]:
        """Return why a title should be dropped, or None if it is relevant."""
        normalized = self._normalize_for_match(title)
        accessories, conditions = self._flag_terms(normalized)

        bad_accessories = accessories - allowed_terms
        if bad_accessories:
            return f"accessory term '{sorted(bad_accessories)[0]}'"

        bad_conditions = conditions - allowed_terms
        if bad_conditions:
            return f"condition term '{sorted(bad_conditions)[0]}'"

        if token_variants:
            matched = sum(
                1
                for variants in token_variants
                if any(self._contains_term(normalized, v) for v in variants)
            )
            if matched < required_matches:
                return f"query tokens matched {matched}/{len(token_variants)}"
        return None

    def _filter_relevant(self, query: str, products: list[Product]) -> list[Product]:
        """Keep only products whose title plausibly matches the query.

        Rules (all must pass to keep a product):
          1. No accessory terms in the title, unless the query contains them.
          2. No condition terms in the title, unless the query contains them.
          3. Every meaningful query token must appear in the title. With more
             than 3 meaningful tokens, 80% must match. Skipped when the query
             has no meaningful tokens.

        If every product would be dropped, the original list is returned
        unchanged and ``self._relevance_skipped`` is set so run() can emit
        ``relevance_filter_skipped``.
        """
        self._relevance_skipped = False
        if not products:
            return []

        normalized_query = self._normalize_for_match(query)
        q_accessories, q_conditions = self._flag_terms(normalized_query)
        allowed_terms = q_accessories | q_conditions

        token_variants = self._query_tokens(normalized_query)
        n = len(token_variants)
        required = n if n <= 3 else (4 * n + 4) // 5  # ceil(0.8 * n)

        kept: list[Product] = []
        dropped: list[tuple[str, str]] = []
        for product in products:
            reason = self._relevance_drop_reason(
                product.title, allowed_terms, token_variants, required
            )
            if reason is None:
                kept.append(product)
            else:
                dropped.append((product.title, reason))

        if not kept:
            self._relevance_skipped = True
            logger.info(
                "Relevance filter would drop all %d products for query %r; skipping",
                len(products), query,
            )
            return list(products)

        for title, reason in dropped[:_MAX_LOGGED_DROPS]:
            logger.info("Relevance filter dropped %r (%s)", title, reason)
        if len(dropped) > _MAX_LOGGED_DROPS:
            logger.info(
                "Relevance filter dropped %d more products",
                len(dropped) - _MAX_LOGGED_DROPS,
            )
        return kept

    # ------------------------------------------------------------------
    # Outliers, clustering, scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_outliers(products: list[Product]) -> list[Product]:
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
        text = title.lower()
        text = _UNIT_SPLIT.sub(r"\1 \2", text)
        text = re.sub(r"[^\w\s]", " ", text)
        words = [w for w in text.split() if w not in _STOPWORDS]
        return " ".join(words)

    @staticmethod
    def _signature(title: str) -> frozenset[str]:
        normalized = AnalysisAgent._normalize_title(title)
        sig: set[str] = set()
        for tok in normalized.split():
            if any(c.isdigit() for c in tok) or tok in _MODEL_MARKERS:
                sig.add(tok)
        return frozenset(sig)

    def _cluster(self, products: list[Product]) -> list[list[Product]]:
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