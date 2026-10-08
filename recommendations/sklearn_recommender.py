"""scikit-learn based NPK fertilizer recommender.

Loads a trained RandomForest/sklearn model (joblib) and encoders.
Requires: scikit-learn, joblib
"""

from __future__ import annotations

import joblib
import numpy as np
from pathlib import Path
from typing import Any

from recommendations.base import NPKRecommender, RecommendationResult


class SklearnRecommender(NPKRecommender):
    """scikit-learn model based recommender (RandomForest, XGBoost, etc.)."""

    name = "sklearn"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        rec_cfg = config.get("npk_recommendations", {})

        self._enabled = rec_cfg.get("enabled", True)
        model_path = rec_cfg.get("model_path", "models/fertilizer_rf.joblib")

        # Resolve path relative to project root
        project_root = Path(__file__).resolve().parent.parent
        model_path = project_root / model_path

        self._model = joblib.load(model_path)
        self._soil_encoder = joblib.load(project_root / "models" / "soil_encoder.joblib")
        self._crop_encoder = joblib.load(project_root / "models" / "crop_encoder.joblib")
        self._fert_encoder = joblib.load(project_root / "models" / "fert_encoder.joblib")

        # Crop/soil mappings for inference (use most common from training data defaults)
        self._default_soil = "Loamy Soil"
        self._default_crop = "tomato"

    def recommend(self, npk_values: dict[str, float]) -> RecommendationResult:
        if not self._enabled:
            return RecommendationResult(recommended=False, model=self.name)

        # Prepare features: [Moisture, Nitrogen, Phosphorous, Potassium, Soil_encoded, Crop_encoded]
        # We don't have moisture from NPK sensor - use a reasonable default
        moisture = 50.0  # default mid-range
        nitrogen = npk_values.get("nitrogen", 0.0)
        phosphorous = npk_values.get("phosphorus", 0.0)
        potassium = npk_values.get("potassium", 0.0)

        # Encode soil and crop (use defaults if not configured)
        try:
            soil_encoded = self._soil_encoder.transform([self._default_soil])[0]
        except ValueError:
            soil_encoded = 0

        try:
            crop_encoded = self._crop_encoder.transform([self._default_crop])[0]
        except ValueError:
            crop_encoded = 0

        features = np.array([[moisture, nitrogen, phosphorous, potassium, soil_encoded, crop_encoded]], dtype=np.float32)

        # Predict
        pred_encoded = self._model.predict(features)[0]
        pred_proba = self._model.predict_proba(features)[0]

        fertilizer_id = self._fert_encoder.inverse_transform([pred_encoded])[0]
        confidence = float(pred_proba.max())

        # Get fertilizer name (same as id for this model)
        fertilizer_name = fertilizer_id

        # Dosage heuristic based on confidence and nutrient deficits
        # This would ideally come from training metadata
        dosage_g_per_10L = 10.0 * (0.5 + confidence * 0.5)

        # Calculate deficits based on typical targets (could be made configurable)
        targets = {"nitrogen": 100, "phosphorus": 50, "potassium": 100}
        deficits = {}
        for nutrient, target in targets.items():
            current = npk_values.get(nutrient, 0)
            if current < target:
                deficits[nutrient] = round((target - current) / target * 100, 1)

        priority = "high" if any(v > 50 for v in deficits.values()) else "medium"

        return RecommendationResult(
            recommended=True,
            fertilizer_id=fertilizer_id,
            fertilizer_name=fertilizer_name,
            dosage_g_per_10L=round(dosage_g_per_10L, 1),
            deficits=deficits if deficits else None,
            reasoning=f"ML model predicts {fertilizer_name} (confidence: {confidence:.1%})",
            priority=priority,
            model=self.name,
        )

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "model": self.name,
            "model_type": type(self._model).__name__,
            "classes": list(self._fert_encoder.classes_),
        }

    def close(self) -> None:
        pass