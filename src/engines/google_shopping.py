"""Google Shopping engine wrapper using SerpApi."""
import logging
from typing import List, Optional

import serpapi

from src.config import SERPAPI_KEY, RESULTS_PER_PAGE
from src.engines.normalizer import normalize_google_shopping
from src.models.product import Product

logger = logging.getLogger(__name__)


class GoogleShoppingEngine:
    name = "google_shopping"
    label = "Google Shopping"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or SERPAPI_KEY
        if not self.api_key:
            raise ValueError("SERPAPI_KEY is required")
        self.client = serpapi.Client(api_key=self.api_key)

    def search(self, query: str, max_results: int = RESULTS_PER_PAGE) -> List[Product]:
        params = {
            "engine": "google_shopping",
            "q": query,
            "gl": "in",
            "hl": "en",
            "num": min(max_results, 100),
        }
        logger.info(f"[{self.name}] Searching: {query}")
        results = self.client.search(params)
        products = normalize_google_shopping(results)
        logger.info(f"[{self.name}] Got {len(products)} products")
        return products