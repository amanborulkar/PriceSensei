"""Data model for Sensei's final purchase recommendation."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Optional

from src.models.product import Product


@dataclass
class Verdict:
    """The final purchase recommendation produced by Sensei."""

    recommendation: str
    confidence: str
    reasoning: list[str]
    suggested_action: str
    best_product: Optional[Product]
    best_cluster_title: Optional[str]
    price_to_pay: Optional[float]
    savings_estimate: Optional[float]
    vs_reference: Optional[str]
    model_used: str
    generated_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict (best_product becomes a nested dict or None)."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["best_product"] = self.best_product.to_dict() if self.best_product else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verdict:
        """Rebuild a verdict from the output of ``to_dict`` (best_product may be None)."""
        best = data.get("best_product")
        return cls(**{**data, "best_product": Product.from_dict(best) if best else None})