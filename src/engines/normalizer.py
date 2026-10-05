"""Normalize SerpApi responses from different engines into Product objects."""
import logging
from typing import Any, Dict, List, Optional

from src.models.product import Product

logger = logging.getLogger(__name__)


def _parse_price(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = "".join(c for c in value if c.isdigit() or c == ".")
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0
    return 0.0


def _parse_rating(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.split()[0])
        except (ValueError, IndexError):
            return None
    return None


def normalize_google_shopping(results: Dict[str, Any]) -> List[Product]:
    products: List[Product] = []
    for item in results.get("shopping_results", []) or []:
        try:
            price = _parse_price(item.get("extracted_price", 0))
            product = Product(
                title=item.get("title", ""),
                price=price,
                seller=item.get("source", "Unknown"),
                link=item.get("product_link") or item.get("link", ""),
                engine="google_shopping",
                raw_price=item.get("price", ""),
                rating=_parse_rating(item.get("rating")),
                reviews=item.get("reviews"),
            )
            if product.is_valid:
                products.append(product)
        except Exception as e:
            logger.warning(f"Failed to normalize Google product: {e}")
    return products


def normalize_bing_shopping(results: Dict[str, Any]) -> List[Product]:
    products: List[Product] = []
    for item in results.get("shopping_results", []) or []:
        try:
            price = _parse_price(item.get("extracted_price", 0))
            product = Product(
                title=item.get("title", ""),
                price=price,
                seller=item.get("seller", "Unknown"),
                link=item.get("link", ""),
                engine="bing_shopping",
                raw_price=item.get("price", ""),
                rating=_parse_rating(item.get("rating")),
                reviews=item.get("reviews"),
            )
            if product.is_valid:
                products.append(product)
        except Exception as e:
            logger.warning(f"Failed to normalize Bing product: {e}")
    return products