"""Camera interface — stub until the PiCamera Module 3 is integrated.

The rest of VeggieCare should only call :meth:`Camera.capture`. If the
camera is not configured, :meth:`NotConfiguredCamera.capture` returns
``None`` and the automation controller skips the pest-detection cycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any


class Camera(ABC):
    """Abstract base all camera backends implement."""

    name: str = "base"

    @abstractmethod
    def capture(self, save_path: str | Path | None = None) -> str | None:
        """Capture an image and return the file path, or None on failure."""

    def status(self) -> dict[str, Any]:
        return {"configured": True, "name": self.name}

    def close(self) -> None:
        pass


class NotConfiguredCamera(Camera):
    """Returned when ``camera.enabled`` is false."""

    name = "not_configured"

    def capture(self, save_path: str | Path | None = None) -> str | None:
        return None

    def status(self) -> dict[str, Any]:
        return {"configured": False, "name": "not_configured", "message": "Camera not installed"}


class PiCamera(Camera):
    """Placeholder for the real PiCamera Module 3 implementation.

    Will be replaced when the camera hardware is available.
    """

    name = "pi_camera_v3"

    def capture(self, save_path: str | Path | None = None) -> str | None:
        raise NotImplementedError(
            "PiCamera Module 3 capture() will be implemented when the hardware is connected."
        )

    def status(self) -> dict[str, Any]:
        return {"configured": False, "name": "pi_camera_v3", "message": "Placeholder — not yet implemented"}