"""Mock pest detector — used while the real PiCamera + AI model are absent.

Modes
~~~~~
* **none**    — always returns ``detected=False``.
* **random**  — returns a detection with configurable probability.
* **sequential** — cycles through a fixed list of ``(class, confidence, detected)``
  entries so that tests and the dashboard can be verified deterministically.
"""

from __future__ import annotations

import random as _random
from datetime import datetime
from typing import Any

from pest_detection.detector import DetectionResult, PestDetector


class MockDetector(PestDetector):
    """Simulated pest detector for development and testing."""

    name = "mock"

    def __init__(self, cfg: dict[str, Any]):
        mock_cfg = cfg.get("mock", {})
        self._mode = mock_cfg.get("mode", "none")

        # sequential mode state
        self._sequence: list[tuple[str, float, bool]] = []
        self._seq_idx = 0
        if self._mode == "sequential":
            for entry in mock_cfg.get("sequence", []):
                if isinstance(entry, (list, tuple)) and len(entry) == 3:
                    self._sequence.append((str(entry[0]), float(entry[1]), bool(entry[2])))
            if not self._sequence:
                self._sequence = [("none", 0.0, False)]

        # random mode settings
        random_cfg = mock_cfg.get("random", {})
        self._detection_prob = float(random_cfg.get("detection_probability", 0.25))
        self._conf_min = float(random_cfg.get("confidence_min", 0.50))
        self._conf_max = float(random_cfg.get("confidence_max", 0.98))

        # default pest class when random detects
        self._default_pest_class = cfg.get("pest_classes", ["aphid"])[0]

    def detect(self, image_path: str | None = None) -> DetectionResult:
        if self._mode == "none":
            return self._make_result(detected=False, pest_class=None, confidence=None)

        if self._mode == "sequential":
            pest_class, conf, detected = self._sequence[self._seq_idx % len(self._sequence)]
            self._seq_idx += 1
            return self._make_result(
                detected=detected,
                pest_class=pest_class if detected else None,
                confidence=conf if detected else None,
            )

        # random
        if _random.random() < self._detection_prob:
            conf = round(_random.uniform(self._conf_min, self._conf_max), 2)
            return self._make_result(detected=True, pest_class=self._default_pest_class, confidence=conf)

        return self._make_result(detected=False, pest_class=None, confidence=None)

    @staticmethod
    def _make_result(detected: bool, pest_class: str | None,
                     confidence: float | None) -> DetectionResult:
        return DetectionResult(
            detected=detected,
            pest_class=pest_class,
            confidence=confidence,
            model="mock",
            raw={"mode": "mock"},
        )

    def status(self) -> dict[str, Any]:
        return {"configured": True, "model": "mock", "mode": self._mode}