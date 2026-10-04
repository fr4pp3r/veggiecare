from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from camera.camera import Camera

logger = logging.getLogger(__name__)


class UsbCamera(Camera):
    """USB webcam implementation using OpenCV."""

    name = "usb_camera"

    def __init__(
        self,
        device_index: int = 0,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
        image_dir: str | Path = "data/images",
        simulate: bool = False,
    ) -> None:
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.image_dir = Path(image_dir)
        self.simulate = simulate
        self._cap = None

        self.image_dir.mkdir(parents=True, exist_ok=True)

        if not simulate:
            try:
                import cv2  # type: ignore

                self._cap = cv2.VideoCapture(device_index)
                if width:
                    self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                if height:
                    self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                if fps:
                    self._cap.set(cv2.CAP_PROP_FPS, fps)
                if not self._cap.isOpened():
                    logger.warning(
                        "Failed to open USB camera at index %s; falling back to simulate",
                        device_index,
                    )
                    self._cap.release()
                    self._cap = None
                    self.simulate = True
                else:
                    logger.info("USB camera initialized on device index %s", device_index)
            except Exception as exc:
                logger.warning("Error initializing USB camera: %s; falling back to simulate", exc)
                self.simulate = True
                self._cap = None
        else:
            logger.info("USB camera in SIMULATED mode")

    def capture(self, save_path: str | Path | None = None) -> str | None:
        if save_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            save_path = self.image_dir / f"capture_{ts}.jpg"

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        if self.simulate or self._cap is None:
            # Create a dummy/placeholder image file
            try:
                from PIL import Image, ImageDraw  # type: ignore

                img = Image.new("RGB", (640, 480), color=(0, 100, 0))
                draw = ImageDraw.Draw(img)
                draw.text((10, 10), f"SIMULATED CAPTURE\n{datetime.now().isoformat()}", fill=(255, 255, 255))
                img.save(save_path, format="JPEG")
                logger.debug("SIMULATE capture saved to %s", save_path)
                return str(save_path)
            except Exception:
                # Fallback: just touch the file
                save_path.write_bytes(b"")
                return str(save_path)

        try:
            import cv2  # type: ignore

            ret, frame = self._cap.read()
            if not ret:
                logger.error("Failed to read frame from USB camera")
                return None
            cv2.imwrite(str(save_path), frame)
            logger.debug("Captured image saved to %s", save_path)
            return str(save_path)
        except Exception as exc:
            logger.error("Error capturing image: %s", exc)
            return None

    def status(self) -> dict[str, Any]:
        return {
            "configured": not self.simulate,
            "name": self.name,
            "device_index": self.device_index,
        }

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
