import asyncio
from src.agents.analysis_agent import AnalysisAgent
from src.models.product import Product

def p(title, price, seller="Store", engine="google_shopping"):
    return Product(title=title, price=price, seller=seller,
                   link=f"http://x/{price}", engine=engine)

async def main():
    agent = AnalysisAgent(asyncio.Queue())

    # Test 1: query "iPhone 15 128GB"
    query = "iPhone 15 128GB"
    products = [
        # Should KEEP
        p("Apple iPhone 15 128GB Black", 63000),
        p("iPhone 15 (128GB) - Blue", 64000),
        p("Apple iPhone 15 128 GB Smartphone", 62000),
        p("Apple iPhone 15 128GB with Gorilla Glass", 63000),  # RISK: contains "glass"
        p("iPhone 15 128GB - Good condition", 62000),          # RISK: contains "good"
        # Should DROP
        p("iPhone 15 Silicone Case", 499),
        p("Apple iPhone 15 128GB Blue Fair - Renewed", 34999),
        p("iPhone 15 Plus 256GB", 85000),
        p("Screen Protector for iPhone 15", 299),
        p("iPhone 15 128GB Tempered Glass Guard", 199),
    ]

    filtered = agent._filter_relevant(query, products)
    print(f"Query: {query!r}")
    print(f"Input: {len(products)}  ->  Kept: {len(filtered)}  Dropped: {len(products) - len(filtered)}")
    print()
    print("KEPT:")
    for prod in filtered:
        print(f"  KEEP  {prod.title[:60]}")
    print()
    print("DROPPED:")
    kept_titles = {p.title for p in filtered}
    for prod in products:
        if prod.title not in kept_titles:
            print(f"  DROP  {prod.title[:60]}")

    print()
    print("=" * 70)
    query2 = "renewed iPhone 15"
    products2 = [
        p("Apple iPhone 15 128GB Renewed", 34999),
        p("Apple iPhone 15 128GB", 63000),
    ]
    filtered2 = agent._filter_relevant(query2, products2)
    print(f"Query: {query2!r}")
    print(f"Input: {len(products2)}  ->  Kept: {len(filtered2)}")
    for prod in filtered2:
        print(f"  KEEP  {prod.title[:60]}")

asyncio.run(main())
