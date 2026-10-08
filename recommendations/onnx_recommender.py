"""ONNX Runtime based NPK fertilizer recommender.

Loads an ONNX model exported from sklearn/PyTorch/TensorFlow.
Requires: onnxruntime
"""

from __future__ import annotations

import joblib
import numpy as np
from pathlib import Path
from typing import Any

from recommendations.base import NPKRecommender, RecommendationResult


class ONNXRecommender(NPKRecommender):
    """ONNX Runtime model based recommender."""

    name = "onnx"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        rec_cfg = config.get("npk_recommendations", {})

        self._enabled = rec_cfg.get("enabled", True)
        model_path = rec_cfg.get("model_path", "models/fertilizer_rf.onnx")

        project_root = Path(__file__).resolve().parent.parent
        model_path = project_root / model_path

        import onnxruntime as ort
        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name

        # Load encoders
        self._soil_encoder = joblib.load(project_root / "models" / "soil_encoder.joblib")
        self._crop_encoder = joblib.load(project_root / "models" / "crop_encoder.joblib")
        self._fert_encoder = joblib.load(project_root / "models" / "fert_encoder.joblib")

        self._default_soil = "Loamy Soil"
        self._default_crop = "tomato"

    def recommend(self, npk_values: dict[str, float]) -> RecommendationResult:
        if not self._enabled:
            return RecommendationResult(recommended=False, model=self.name)

        moisture = 50.0
        nitrogen = npk_values.get("nitrogen", 0.0)
        phosphorous = npk_values.get("phosphorus", 0.0)
        potassium = npk_values.get("potassium", 0.0)

        try:
            soil_encoded = self._soil_encoder.transform([self._default_soil])[0]
        except ValueError:
            soil_encoded = 0

        try:
            crop_encoded = self._crop_encoder.transform([self._default_crop])[0]
        except ValueError:
            crop_encoded = 0

        features = np.array([[moisture, nitrogen, phosphorous, potassium, soil_encoded, crop_encoded]], dtype=np.float32)

        outputs = self._session.run([self._output_name], {self._input_name: features})
        pred_encoded = int(outputs[0][0])

        # Some ONNX models output probabilities, others just class labels
        if len(outputs) > 1 and outputs[1].size > 0:
            # Probability output available
            proba = outputs[1][0]
            confidence = float(proba.max())
        else:
            confidence = 1.0

        fertilizer_id = self._fert_encoder.inverse_transform([pred_encoded])[0]
        fertilizer_name = fertilizer_id

        dosage_g_per_10L = 10.0 * (0.5 + confidence * 0.5)

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
            reasoning=f"ONNX model predicts {fertilizer_name} (confidence: {confidence:.1%})",
            priority=priority,
            model=self.name,
        )

    def status(self) -> dict[str, Any]:
        return {
            "configured": True,
            "model": self.name,
            "providers": self._session.get_providers(),
            "input_shape": self._session.get_inputs()[0].shape,
            "classes": list(self._fert_encoder.classes_),
        }

    def close(self) -> None:
        pass