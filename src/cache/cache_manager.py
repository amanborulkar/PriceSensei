"""Aggressive caching layer to preserve SerpApi credits."""
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from src.config import CACHE_DIR, CACHE_TTL_HOURS, MONTHLY_CREDIT_LIMIT
from src.models.product import Product

logger = logging.getLogger(__name__)


class CacheManager:
    """File-based JSON cache with TTL and credit tracking.

    On cache hit, returns list[Product] directly so callers never
    deal with raw dicts.
    """

    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stats_file = self.cache_dir / "_credit_stats.json"
        self._init_stats()

    # ---------- stats ----------

    def _init_stats(self):
        if not self.stats_file.exists():
            self._write_stats({
                "monthly_credits_used": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "last_reset": time.strftime("%Y-%m"),
            })

    def _read_stats(self) -> dict:
        with open(self.stats_file, "r") as f:
            return json.load(f)

    def _write_stats(self, stats: dict):
        with open(self.stats_file, "w") as f:
            json.dump(stats, f, indent=2)

    def _increment_stat(self, field: str, by: int = 1):
        stats = self._read_stats()
        stats[field] = stats.get(field, 0) + by
        self._write_stats(stats)

    # ---------- keys ----------

    def _cache_key(self, engine: str, query: str) -> str:
        raw = f"{engine}:{query.lower().strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # ---------- public API ----------

    def get(self, engine: str, query: str) -> Optional[List[Product]]:
        """Return cached products if fresh, else None."""
        key = self._cache_key(engine, query)
        path = self._cache_path(key)

        if not path.exists():
            self._increment_stat("cache_misses")
            return None

        try:
            with open(path, "r") as f:
                cached = json.load(f)

            age_hours = (time.time() - cached.get("cached_at", 0)) / 3600
            if age_hours > CACHE_TTL_HOURS:
                path.unlink()
                self._increment_stat("cache_misses")
                return None

            self._increment_stat("cache_hits")
            logger.info(f"Cache hit: {engine}:{query} (age {age_hours:.1f}h)")

            products = [Product.from_dict(d) for d in cached.get("data", [])]
            return [p for p in products if p.is_valid]

        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Cache read error: {e}")
            self._increment_stat("cache_misses")
            return None

    def set(self, engine: str, query: str, products: List[Product]) -> None:
        """Store products and count 1 SerpApi credit."""
        key = self._cache_key(engine, query)
        path = self._cache_path(key)

        payload = {
            "engine": engine,
            "query": query,
            "cached_at": time.time(),
            "data": [p.to_dict() for p in products],
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)

        self._increment_stat("monthly_credits_used")
        logger.info(f"Cached: {engine}:{query} (1 credit used)")

    def get_credit_status(self) -> dict:
        stats = self._read_stats()
        used = stats.get("monthly_credits_used", 0)
        return {
            "used": used,
            "limit": MONTHLY_CREDIT_LIMIT,
            "remaining": max(0, MONTHLY_CREDIT_LIMIT - used),
            "percent": round(used / MONTHLY_CREDIT_LIMIT * 100, 1),
            "cache_hits": stats.get("cache_hits", 0),
            "cache_misses": stats.get("cache_misses", 0),
        }