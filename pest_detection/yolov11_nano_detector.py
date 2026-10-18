from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from pest_detection.detector import DetectionResult, PestDetector

logger = logging.getLogger(__name__)

# The "center" ROI covers this fraction of the frame's width and height.
_CENTER_FRACTION = 2.0 / 3.0
# Neighbouring grid tiles overlap by this fraction of the frame.
_TILE_OVERLAP = 0.10


def _import_cv2():
    """Import cv2 lazily so the module is importable without OpenCV."""
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        return None, str(exc)
    return cv2, None


class YoloV11NanoDetector(PestDetector):
    """YOLOv11 Nano pest detector.

    The trained checkpoint is a *classification* model (9 classes, 224px),
    so a bug on a blurred/edge frame can score below the threshold. To
    improve recall, ``detect`` supports region scanning:

    * ``"full"``   — classify the whole frame (original behaviour)
    * ``"center"`` — classify a central crop (2/3 width and height)
    * ``"grid"``   — classify the full frame first; only if it scores below
      ``confidence_threshold`` classify ``roi_grid`` x ``roi_grid`` tiles
      with ~10% overlap and keep the best confidence
    * ``[x, y, w, h]`` — fractional box (0-1), classify only that crop
    """

    name = "yolov11n"

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    _TRAINED_CHECKPOINT = Path("runs/veggiecare_cls_v1/weights/best.pt")
    _STOCK_MODEL = "yolo11n-cls.pt"

    def _resolve_model_name(self) -> str:
        """Pick a checkpoint: trained run first, then configured path, then stock weights."""
        candidates = [self._REPO_ROOT / self._TRAINED_CHECKPOINT]
        if self._model_path:
            configured = Path(self._model_path).expanduser()
            candidates.append(configured if configured.is_absolute() else self._REPO_ROOT / configured)
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return self._STOCK_MODEL

    def __init__(self, cfg: dict[str, Any]) -> None:
        self._cfg = cfg
        self._confidence_threshold = float(cfg.get("confidence_threshold", 0.70))
        self._model_path = cfg.get("model_path")
        self._roi = cfg.get("roi", "full")
        self._roi_grid = int(cfg.get("roi_grid", 2))
        self._model = None

        try:
            from ultralytics import YOLO  # type: ignore

            model_name = self._resolve_model_name()
            self._model = YOLO(model_name)
            logger.info("YOLOv11 Nano model loaded: %s", model_name)
        except Exception as exc:
            logger.warning(
                "Failed to load YOLOv11 Nano model: %s; pest detection will report no detections",
                exc,
            )
            self._model = None

    def detect(
        self,
        image_path: str | None = None,
        roi: str | list | None = None,
    ) -> DetectionResult:
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
            cv2, import_error = _import_cv2()
            if cv2 is None:
                raise RuntimeError(f"OpenCV is not available: {import_error}")
            frame = cv2.imread(image_path)
            if frame is None:
                raise RuntimeError(f"could not read image: {image_path}")

            best_conf = 0.0
            best_class: str | None = None
            best_region: str | None = None

            for label, crop in self._regions(roi, frame):
                conf, cls = self._classify(crop)
                if conf > best_conf:
                    best_conf = conf
                    best_class = cls
                    best_region = label
                # Grid two-stage: tiles follow the full frame; once the full
                # frame already meets the threshold there is no need to tile.
                if label == "full" and best_conf >= self._confidence_threshold:
                    break

            if best_conf >= self._confidence_threshold and best_class:
                return DetectionResult(
                    detected=True,
                    pest_class=best_class,
                    confidence=round(best_conf, 4),
                    image_path=image_path,
                    timestamp=datetime.now(),
                    model=self.name,
                    raw={"class": best_class, "conf": best_conf, "region": best_region},
                )

            # Below threshold: still report the top-1 class and confidence so
            # the controller can surface a "possible pest" warning band.
            # ``detected`` semantics stay exactly as before.
            raw: dict[str, Any] = {
                "class": best_class,
                "conf": best_conf if best_conf > 0 else None,
            }
            if best_region:
                raw["region"] = best_region
            return DetectionResult(
                detected=False,
                pest_class=best_class,
                confidence=best_conf if best_conf > 0 else None,
                image_path=image_path,
                timestamp=datetime.now(),
                model=self.name,
                raw=raw,
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

    def _regions(self, roi: str | list | None, frame) -> Iterator[tuple[str, Any]]:
        """Yield ``(region_label, crop)`` pairs in scan order.

        ``roi=None`` fallbacks to the configured default. Invalid values are
        handled gracefully: a warning is logged and the whole frame is used.
        """
        height = int(frame.shape[0])
        width = int(frame.shape[1])
        if width < 2 or height < 2:
            yield "full", frame
            return
        boxes = self._region_boxes(roi, width, height)
        for label, (x0, y0, x1, y1) in boxes:
            yield label, frame[y0:y1, x0:x1]

    def _region_boxes(
        self, roi: str | list | None, width: int, height: int
    ) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Compute pixel boxes (x0, y0, x1, y1) per region, in scan order."""
        if width < 2 or height < 2:
            return [("full", (0, 0, width, height))]

        mode = roi if roi is not None else self._roi

        if isinstance(mode, str):
            name = mode.strip().lower()
            if name == "full":
                return [("full", (0, 0, width, height))]
            if name == "center":
                w = width * _CENTER_FRACTION
                h = height * _CENTER_FRACTION
                x0 = (width - w) / 2.0
                y0 = (height - h) / 2.0
                return [
                    (
                        "center",
                        (
                            max(0, round(x0)),
                            max(0, round(y0)),
                            min(width, round(x0 + w)),
                            min(height, round(y0 + h)),
                        ),
                    )
                ]
            if name == "grid":
                n = self._roi_grid
                n = max(2, min(4, n))  # config-validated, defensive here
                tile = min(1.0, (1.0 + _TILE_OVERLAP) / n)
                boxes: list[tuple[str, tuple[int, int, int, int]]] = [
                    ("full", (0, 0, width, height))
                ]
                for r in range(n):
                    for c in range(n):
                        x0f = c * (1.0 - tile) / (n - 1)
                        y0f = r * (1.0 - tile) / (n - 1)
                        boxes.append((
                            f"tile:{r},{c}",
                            (
                                round(x0f * width),
                                round(y0f * height),
                                min(width, round((x0f + tile) * width)),
                                min(height, round((y0f + tile) * height)),
                            ),
                        ))
                return boxes
            logger.warning("Unknown roi %r; falling back to the full frame", mode)
            return [("full", (0, 0, width, height))]

        if isinstance(mode, (list, tuple)) and len(mode) == 4:
            x, y, w, h = mode
            if all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y, w, h)
            ):
                x, y, w, h = float(x), float(y), float(w), float(h)
                valid = (
                    0 <= x < 1
                    and 0 <= y < 1
                    and 0 < w <= 1
                    and 0 < h <= 1
                    and x + w <= 1 + 1e-9
                    and y + h <= 1 + 1e-9
                )
                if valid:
                    return [
                        (
                            "box",
                            (
                                round(x * width),
                                round(y * height),
                                min(width, round((x + w) * width)),
                                min(height, round((y + h) * height)),
                            ),
                        )
                    ]
        elif mode is None:
            return [("full", (0, 0, width, height))]

        logger.warning("Invalid roi %r; falling back to the full frame", mode)
        return [("full", (0, 0, width, height))]

    def _classify(self, crop) -> tuple[float, str | None]:
        """Run one inference pass and return ``(best_conf, best_class_name)``.

        Works for both detection (``boxes``) and classification (``probs``)
        result types, preferring the highest confidence across the results.
        """
        best_conf = 0.0
        best_class = None
        results = self._model.predict(source=crop, conf=self._confidence_threshold, verbose=False)
        for r in results or []:
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
        return best_conf, best_class

    def status(self) -> dict[str, Any]:
        return {
            "configured": self._model is not None,
            "model": self.name,
            "roi": self._roi,
            "roi_grid": self._roi_grid,
        }
