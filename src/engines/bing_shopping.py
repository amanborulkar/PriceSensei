"""Bing Shopping engine wrapper using SerpApi."""
import logging
from typing import List, Optional

import serpapi

from src.config import SERPAPI_KEY, RESULTS_PER_PAGE
from src.engines.normalizer import normalize_bing_shopping
from src.models.product import Product

logger = logging.getLogger(__name__)


class BingShoppingEngine:
    name = "bing_shopping"
    label = "Bing Shopping"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or SERPAPI_KEY
        if not self.api_key:
            raise ValueError("SERPAPI_KEY is required")
        self.client = serpapi.Client(api_key=self.api_key)

    def search(self, query: str, max_results: int = RESULTS_PER_PAGE) -> List[Product]:
        params = {
            "engine": "bing_shopping",
            "q": query,
            "mkt": "en-IN",
        }
        logger.info(f"[{self.name}] Searching: {query}")
        results = self.client.search(params)
        products = normalize_bing_shopping(results)
        logger.info(f"[{self.name}] Got {len(products)} products")
        return products