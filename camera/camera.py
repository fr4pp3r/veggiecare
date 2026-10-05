"""Camera interface.

The rest of VeggieCare should only call :meth:`Camera.capture`. If the
camera is not configured, :meth:`NotConfiguredCamera.capture` returns
``None`` and the automation controller skips the pest-detection cycle.

:meth:`Camera.status` is the dashboard's only view of the hardware, so it
must always carry enough detail to explain *why* a camera is unusable —
not just a boolean. A missing ``error``/``message`` is what previously
made a perfectly good USB webcam report "Not installed".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


class Camera(ABC):
    """Abstract base all camera backends implement."""

    name: str = "base"

    @abstractmethod
    def capture(self, save_path: str | Path | None = None) -> str | None:
        """Capture an image and return the file path, or None on failure."""

    def status(self) -> dict[str, Any]:
        """Describe the camera for the dashboard.

        Subclasses must keep these keys:
            configured  -- True only when a real, working device is present
            name        -- backend identifier
            error       -- actionable reason, or None when healthy
            message     -- short human-readable status line
        Optional: device_path, device_index, width, height, fps, simulated.
        """
        return {
            "configured": True,
            "name": self.name,
            "error": None,
            "message": "Connected",
        }

    def stream(self, fps: float | None = None) -> Iterator[bytes]:
        """Yield JPEG-encoded frames for the dashboard live view.

        The default implementation yields nothing, which makes the MJPEG
        endpoint return 503 rather than hanging.
        """
        return iter(())

    def close(self) -> None:
        pass


class NotConfiguredCamera(Camera):
    """Returned when ``camera.enabled`` is false."""

    name = "not_configured"

    def capture(self, save_path: str | Path | None = None) -> str | None:
        return None

    def status(self) -> dict[str, Any]:
        return {
            "configured": False,
            "name": self.name,
            "simulated": False,
            "device_path": None,
            "error": None,
            "message": "Camera not installed",
        }


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
        return {
            "configured": False,
            "name": self.name,
            "simulated": False,
            "device_path": None,
            "error": None,
            "message": "Placeholder — not yet implemented",
        }
