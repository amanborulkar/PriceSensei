"""Day 1 smoke test: fetch from both engines with cache."""
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

from src.cache.cache_manager import CacheManager
from src.engines.google_shopping import GoogleShoppingEngine
from src.engines.bing_shopping import BingShoppingEngine


def fetch(engine_obj, cache: CacheManager, query: str):
    cached = cache.get(engine_obj.name, query)
    if cached:
        print(f"  (cache hit — {len(cached)} products)")
        return cached
    products = engine_obj.search(query)
    cache.set(engine_obj.name, query, products)
    return products


def main():
    cache = CacheManager()
    print("=== Credit status BEFORE ===")
    print(cache.get_credit_status())

    query = "iPhone 15 128GB"

    print(f"\n=== Google Shopping: {query} ===")
    google = GoogleShoppingEngine()
    for p in fetch(google, cache, query)[:5]:
        print(f"  [{p.engine}] {p.title[:60]} | Rs.{p.price} | {p.seller}")

    print(f"\n=== Bing Shopping: {query} ===")
    bing = BingShoppingEngine()
    for p in fetch(bing, cache, query)[:5]:
        print(f"  [{p.engine}] {p.title[:60]} | Rs.{p.price} | {p.seller}")

    print("\n=== Credit status AFTER ===")
    print(cache.get_credit_status())


if __name__ == "__main__":
    main()