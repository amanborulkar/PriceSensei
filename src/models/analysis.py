"""Data models produced by the AnalysisAgent."""

from __future__ import annotations
from dataclasses import dataclass, field, fields
from typing import Any, Optional

from src.models.product import Product


@dataclass
class PriceCluster:
    """A group of listings that refer to the same product."""

    representative_title: str
    products: list[Product]
    min_price: float
    max_price: float
    median_price: float
    mean_price: float
    savings_vs_median: float
    listings_count: int
    engines: list[str]
    best_deal: Product
    flag: str  # "BEST_DEAL" | "FAIR_PRICE" | "OVERPRICED"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict (nested products included)."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["products"] = [p.to_dict() for p in self.products]
        data["best_deal"] = self.best_deal.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceCluster:
        """Rebuild a cluster from the output of ``to_dict``."""
        kwargs = {
            **data,
            "products": [Product.from_dict(p) for p in data["products"]],
            "best_deal": Product.from_dict(data["best_deal"]),
        }
        return cls(**kwargs)


@dataclass
class AnalysisResult:
    """Result of running the AnalysisAgent on a product list."""

    query: str
    total_products: int
    total_clusters: int
    clusters: list[PriceCluster]
    global_min_price: float
    global_median_price: float
    global_max_price: float
    best_overall_deal: Optional[Product]
    reference_price: Optional[float]
    vs_reference: Optional[str]  # "under" | "near" | "over" | None
    outliers_removed: int
    filtered_products: list[Product] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict (nested clusters and products included)."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["clusters"] = [c.to_dict() for c in self.clusters]
        data["best_overall_deal"] = (
            self.best_overall_deal.to_dict() if self.best_overall_deal else None
        )
        data["filtered_products"] = [p.to_dict() for p in self.filtered_products]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisResult:
        """Rebuild a result from the output of ``to_dict``."""
        best = data.get("best_overall_deal")
        kwargs = {
            **data,
            "clusters": [PriceCluster.from_dict(c) for c in data["clusters"]],
            "best_overall_deal": Product.from_dict(best) if best else None,
            "filtered_products": [
                Product.from_dict(p) for p in data.get("filtered_products", [])
            ],
        }
        return cls(**kwargs)