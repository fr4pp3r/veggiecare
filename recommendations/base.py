"""Abstract base class for NPK recommenders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RecommendationResult:
    """Result of an NPK recommendation request."""

    recommended: bool
    fertilizer_id: str | None = None
    fertilizer_name: str | None = None
    dosage_g_per_10L: float | None = None
    deficits: dict[str, float] | None = None  # nutrient -> % deficit (positive = below target)
    reasoning: str | None = None
    priority: str | None = None  # "high" | "medium" | "low"
    timestamp: datetime = field(default_factory=datetime.now)
    model: str = "base"

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "recommended": self.recommended,
            "fertilizer_id": self.fertilizer_id,
            "fertilizer_name": self.fertilizer_name,
            "dosage_g_per_10L": self.dosage_g_per_10L,
            "deficits": self.deficits,
            "reasoning": self.reasoning,
            "priority": self.priority,
            "timestamp": self.timestamp.isoformat(timespec="seconds") if self.timestamp else None,
            "model": self.model,
        }


class NPKRecommender(ABC):
    """Abstract base class for NPK-based fertilizer recommenders."""

    name: str = "base"

    def __init__(self, config: dict) -> None:
        self._config = config

    @abstractmethod
    def recommend(self, npk_values: dict[str, float]) -> RecommendationResult:
        """Return a recommendation based on current NPK reading.

        Args:
            npk_values: Dict with keys "nitrogen", "phosphorus", "potassium" (mg/kg)

        Returns:
            RecommendationResult with recommendation details
        """
        pass

    def status(self) -> dict[str, Any]:
        """Return status info for dashboard."""
        return {"configured": True, "model": self.name}

    def close(self) -> None:
        """Cleanup resources (model handles, connections, etc.)."""
        pass