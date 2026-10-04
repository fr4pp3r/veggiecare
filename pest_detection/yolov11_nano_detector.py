from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pest_detection.detector import DetectionResult, PestDetector

logger = logging.getLogger(__name__)


class YoloV11NanoDetector(PestDetector):
    """YOLOv11 Nano pest detector."""

    name = "yolov11n"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg
        self._confidence_threshold = float(cfg.get("confidence_threshold", 0.70))
        self._model_path = cfg.get("model_path")
        self._model = None
        self._classes = cfg.get("pest_classes", [])

        try:
            from ultralytics import YOLO  # type: ignore

            import os

            trained = r"D:/Projects/veggiecare/runs/veggiecare_cls_v1/weights/best.pt"
            if os.path.exists(trained):
                model_name = trained
            else:
                model_name = self._model_path or "yolo11n-cls.pt"
            self._model = YOLO(model_name)
            logger.info("YOLOv11 Nano model loaded: %s", model_name)
        except Exception as exc:
            logger.warning("Failed to load YOLOv11 Nano model: %s; falling back to mock behavior", exc)
            self._model = None

    def detect(self, image_path: str | None = None) -> DetectionResult:
        if self._model is None or image_path is None:
            return DetectionResult(
                detected=False,
                pest_class=None,
                confidence=None,
                image_path=image_path,
                timestamp=datetime.now(),
                model=self.name,
                raw={},
            )

        try:
            results = self._model.predict(source=image_path, conf=self._confidence_threshold, verbose=False)
            if not results:
                return DetectionResult(
                    detected=False,
                    pest_class=None,
                    confidence=None,
                    image_path=image_path,
                    timestamp=datetime.now(),
                    model=self.name,
                    raw={},
                )

            best_conf = 0.0
            best_class = None
            for r in results:
                if hasattr(r, "boxes") and r.boxes is not None and len(r.boxes) > 0:
                    for box in r.boxes:
                        conf = float(box.conf.item()) if hasattr(box.conf, "item") else float(box.conf)
                        if conf > best_conf:
                            best_conf = conf
                            cls_id = int(box.cls.item()) if hasattr(box.cls, "item") else int(box.cls)
                            if hasattr(r, "names"):
                                best_class = r.names.get(cls_id, str(cls_id))
                            else:
                                best_class = str(cls_id)
                elif hasattr(r, "probs") and r.probs is not None:
                    top1 = r.probs.top1
                    top1conf = float(r.probs.top1conf.item()) if hasattr(r.probs.top1conf, "item") else float(r.probs.top1conf)
                    if top1conf > best_conf:
                        best_conf = top1conf
                        best_class = r.names.get(top1, str(top1)) if hasattr(r, "names") else str(top1)

            if best_conf >= self._confidence_threshold and best_class:
                # Map to configured pest class if possible
                mapped = best_class
                for pc in self._classes:
                    if pc.lower() in str(mapped).lower():
                        mapped = pc
                        break
                return DetectionResult(
                    detected=True,
                    pest_class=mapped,
                    confidence=round(best_conf, 4),
                    image_path=image_path,
                    timestamp=datetime.now(),
                    model=self.name,
                    raw={"class": best_class, "conf": best_conf},
                )

            return DetectionResult(
                detected=False,
                pest_class=None,
                confidence=best_conf if best_conf > 0 else None,
                image_path=image_path,
                timestamp=datetime.now(),
                model=self.name,
                raw={},
            )
        except Exception as exc:
            logger.error("YOLO detection failed: %s", exc)
            return DetectionResult(
                detected=False,
                pest_class=None,
                confidence=None,
                image_path=image_path,
                timestamp=datetime.now(),
                model=self.name,
                raw={"error": str(exc)},
            )

    def status(self) -> dict[str, Any]:
        return {"configured": self._model is not None, "model": self.name}
