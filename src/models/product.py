"""Unified product schema across all engines."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Product:
    """A single product listing normalized from any shopping engine."""
    title: str
    price: float
    seller: str
    link: str
    engine: str
    raw_price: str = ""
    rating: Optional[float] = None
    reviews: Optional[int] = None

    @property
    def is_valid(self) -> bool:
        return bool(self.title and self.price and self.price > 0)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "price": self.price,
            "seller": self.seller,
            "link": self.link,
            "engine": self.engine,
            "raw_price": self.raw_price,
            "rating": self.rating,
            "reviews": self.reviews,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Product":
        return cls(
            title=data.get("title", ""),
            price=float(data.get("price", 0) or 0),
            seller=data.get("seller", "Unknown"),
            link=data.get("link", ""),
            engine=data.get("engine", ""),
            raw_price=data.get("raw_price", ""),
            rating=data.get("rating"),
            reviews=data.get("reviews"),
        )