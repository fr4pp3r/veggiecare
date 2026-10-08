"""Mock NPK recommender for testing and development."""

from __future__ import annotations

import random
from typing import Any

from recommendations.base import NPKRecommender, RecommendationResult


class MockRecommender(NPKRecommender):
    """Mock recommender with configurable behavior for testing."""

    name = "mock"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        mock_cfg = config.get("npk_recommendations", {}).get("mock", {})
        self._mode = mock_cfg.get("mode", "random")  # "none" | "random" | "sequential"
        self._sequence = mock_cfg.get("sequence", [])
        self._seq_index = 0
        self._probability = mock_cfg.get("recommend_probability", 0.5)
        self._fertilizers = [
            {"id": "mock_balanced", "name": "Mock Balanced 20-20-20", "n_p_k": [20, 20, 20], "dosage_g_per_10L": 10},
            {"id": "mock_high_n", "name": "Mock High N 30-10-10", "n_p_k": [30, 10, 10], "dosage_g_per_10L": 5},
            {"id": "mock_high_k", "name": "Mock High K 10-10-30", "n_p_k": [10, 10, 30], "dosage_g_per_10L": 8},
        ]

    def recommend(self, npk_values: dict[str, float]) -> RecommendationResult:
        if self._mode == "none":
            return RecommendationResult(recommended=False, model=self.name)

        if self._mode == "sequential" and self._sequence:
            item = self._sequence[self._seq_index % len(self._sequence)]
            self._seq_index += 1
            recommended, fertilizer_id, dosage, priority = item
            if not recommended:
                return RecommendationResult(recommended=False, model=self.name)
            fert = next((f for f in self._fertilizers if f["id"] == fertilizer_id), self._fertilizers[0])
            return RecommendationResult(
                recommended=True,
                fertilizer_id=fert["id"],
                fertilizer_name=fert["name"],
                dosage_g_per_10L=dosage,
                deficits={"nitrogen": 25.0, "phosphorus": 10.0, "potassium": 0.0},
                reasoning="Mock sequential recommendation",
                priority=priority,
                model=self.name,
            )

        # Random mode
        if random.random() > self._probability:
            return RecommendationResult(recommended=False, model=self.name)

        fert = random.choice(self._fertilizers)
        priority = random.choice(["high", "medium", "low"])
        dosage = fert["dosage_g_per_10L"] * random.uniform(0.8, 1.2)

        return RecommendationResult(
            recommended=True,
            fertilizer_id=fert["id"],
            fertilizer_name=fert["name"],
            dosage_g_per_10L=round(dosage, 1),
            deficits={"nitrogen": round(random.uniform(10, 50), 1), "phosphorus": round(random.uniform(5, 30), 1)},
            reasoning="Mock random recommendation",
            priority=priority,
            model=self.name,
        )

    def status(self) -> dict[str, Any]:
        return {"configured": True, "model": self.name, "mode": self._mode}