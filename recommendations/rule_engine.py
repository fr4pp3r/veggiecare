"""Rule-based NPK fertilizer recommendation engine.

Zero-ML, config-driven, fully explainable. Targets and fertilizers are
defined in config.yaml under the `npk_recommendations` section.
"""

from __future__ import annotations

from typing import Any

from recommendations.base import NPKRecommender, RecommendationResult


class RuleBasedRecommender(NPKRecommender):
    """Rule-based fertilizer recommender using target ranges and fertilizer recipes."""

    name = "rule_engine"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        rec_cfg = config.get("npk_recommendations", {})

        self._enabled = rec_cfg.get("enabled", True)
        self._crop = rec_cfg.get("crop_type", "tomato")
        self._stage = rec_cfg.get("growth_stage", "vegetative")

        # Target ranges: {crop: {stage: {nutrient: [min, max]}}}
        self._targets = rec_cfg.get("targets", {})

        # Fertilizer recipes: list of dicts with id, name, n_p_k, dosage_g_per_10L, suitable_for
        self._fertilizers = rec_cfg.get("fertilizers", [])

        # Build stage-specific targets for quick lookup
        crop_targets = self._targets.get(self._crop, {})
        self._stage_targets = crop_targets.get(self._stage, {})

    def recommend(self, npk_values: dict[str, float]) -> RecommendationResult:
        """Generate recommendation based on current NPK values."""
        if not self._enabled:
            return RecommendationResult(recommended=False, model=self.name)

        # Calculate deficits for each nutrient
        deficits = {}
        any_below = False
        max_deficit_pct = 0.0

        for nutrient in ("nitrogen", "phosphorus", "potassium"):
            target_range = self._stage_targets.get(nutrient)
            if not target_range or not isinstance(target_range, (list, tuple)) or len(target_range) != 2:
                # No target defined for this nutrient - skip
                deficits[nutrient] = 0.0
                continue

            low, high = target_range
            current = npk_values.get(nutrient, 0.0)

            if current < low:
                # Below target - calculate fractional deficit
                deficit_pct = (low - current) / low if low > 0 else 0.0
                deficits[nutrient] = round(deficit_pct * 100, 1)  # as percentage
                any_below = True
                if deficit_pct > max_deficit_pct:
                    max_deficit_pct = deficit_pct
            elif current > high:
                # Above target - excess (negative deficit)
                excess_pct = (current - high) / high if high > 0 else 0.0
                deficits[nutrient] = round(-excess_pct * 100, 1)
            else:
                # Within range
                deficits[nutrient] = 0.0

        if not any_below:
            return RecommendationResult(recommended=False, model=self.name)

        # Score fertilizers by how well they address the deficits
        best_fert = None
        best_score = -1.0

        for fert in self._fertilizers:
            if self._stage not in fert.get("suitable_for", []):
                continue

            n_p_k = fert.get("n_p_k", [0, 0, 0])
            if sum(n_p_k) == 0:
                continue

            # Score: weighted sum of (deficit_pct * fertilizer_ratio)
            # Only positive deficits (below target) contribute to score
            score = 0.0
            for i, nutrient in enumerate(["nitrogen", "phosphorus", "potassium"]):
                deficit = deficits.get(nutrient, 0.0)
                if deficit > 0:  # only reward addressing actual deficits
                    ratio = n_p_k[i] / 100.0  # normalize to 0-1
                    score += (deficit / 100.0) * ratio

            if score > best_score:
                best_score = score
                best_fert = fert

        if not best_fert:
            # No suitable fertilizer found for this stage
            return RecommendationResult(
                recommended=False,
                model=self.name,
                reasoning=f"No suitable fertilizer configured for {self._stage} stage",
            )

        # Scale dosage by worst deficit (more deficit = slightly higher dosage)
        base_dosage = best_fert.get("dosage_g_per_10L", 10.0)
        dosage_multiplier = 1.0 + min(max_deficit_pct, 1.0)  # cap at 2x
        dosage = round(base_dosage * dosage_multiplier, 1)

        # Build list of nutrients that are below target
        low_nutrients = [n for n, v in deficits.items() if v > 0]

        priority = "high" if max_deficit_pct > 0.5 else "medium"

        return RecommendationResult(
            recommended=True,
            fertilizer_id=best_fert.get("id"),
            fertilizer_name=best_fert.get("name"),
            dosage_g_per_10L=dosage,
            deficits={k: v for k, v in deficits.items() if v != 0},
            reasoning=f"Addresses {', '.join(low_nutrients)} deficit(s) for {self._crop} ({self._stage})",
            priority=priority,
            model=self.name,
        )

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "model": self.name,
            "crop": self._crop,
            "growth_stage": self._stage,
            "fertilizers_configured": len(self._fertilizers),
        }