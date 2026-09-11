"""Pest detector interface.

The rest of VeggieCare interacts only with this interface. When the real
PiCamera 3 + AI model arrive, they will implement :class:`PestDetector`
and plug into the same slot — dashboard, automation controller and
database schema are already expecting this contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DetectionResult:
    """One frame's worth of pest-detection output."""

    detected: bool
    pest_class: str | None = None
    confidence: float | None = None
    image_path: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    model: str = "mock"
    raw: dict[str, Any] | None = None


class PestDetector(ABC):
    """Abstract base all detectors implement."""

    name: str = "base"

    @abstractmethod
    def detect(self, image_path: str | None = None) -> DetectionResult:
        """Analyze *image_path* (or a freshly captured frame when None)."""

    def status(self) -> dict[str, Any]:
        """Return metadata for the system-status dashboard section."""
        return {"configured": True, "model": self.name}

    def close(self) -> None:
        """Release resources. Base implementation does nothing."""