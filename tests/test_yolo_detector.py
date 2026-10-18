"""Tests for the YOLOv11 Nano pest detector.

These must run on a machine with no ultralytics/OpenCV/numpy, so every test
injects a fake ``ultralytics`` module and a fake ``cv2``.

The fake frame is a pure-Python stand-in that supports the two-frame
slicing the detector uses (``frame[y0:y1, x0:x1]``) so region crops can be
verified without numpy.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from pest_detection import yolov11_nano_detector as yolo_mod
from pest_detection.yolov11_nano_detector import YoloV11NanoDetector


WIDTH, HEIGHT = 600, 300  # divisible by 3, 4, 6 for clean crop math

THRESHOLD = 0.70


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeProbs:
    def __init__(self, top1: int, top1conf: float) -> None:
        self.top1 = top1
        self.top1conf = _HasItem(top1conf)


class _HasItem:
    def __init__(self, value: float) -> None:
        self._value = value

    def item(self) -> float:
        return self._value


class FakeResult:
    def __init__(self, names: dict[int, str], probs: FakeProbs) -> None:
        self.names = names
        self.probs = probs


class FakeFrame:
    """Pure-Python stand-in for a cv2/numpy frame."""

    def __init__(self, bounds: tuple[int, int, int, int] | None = None) -> None:
        # bounds = (y0, y1, x0, x1) relative to the ORIGINAL frame
        self.bounds = bounds
        self.shape = (HEIGHT, WIDTH, 3)

    def __getitem__(self, key) -> "FakeFrame":
        (row_slice, col_slice) = key
        y0, y1 = row_slice.start or 0, row_slice.stop or HEIGHT
        x0, x1 = col_slice.start or 0, col_slice.stop or WIDTH
        if self.bounds is not None:
            base_y0, base_y1, base_x0, base_x1 = self.bounds
            return FakeFrame((base_y0 + y0, base_y0 + y1, base_x0 + x0, base_x1 + x1))
        return FakeFrame((y0, y1, x0, x1))


class FakeCv2:
    """Minimal cv2 module: only imread is needed by the detector."""

    def imread(self, path):
        if path == "missing.jpg":
            return None
        return FakeFrame()


class FakeYolo:
    """Minimal ultralytics.YOLO stand-in.

    Confidence depends on which region the source frame covers, letting us
    script "full frame misses, bottom-right tile wins" scenarios.
    """

    def __init__(self, weights, camera_name="grasshopper") -> None:
        assert weights, "YOLO must be constructed with a weights path"
        self.weights = weights
        self.names = {0: camera_name}
        self.predicted: list[tuple[int, int, int, int]] = []

    def predict(self, source=None, **_kwargs):
        bounds = getattr(source, "bounds", (0, HEIGHT, 0, WIDTH))
        y0, y1, x0, x1 = bounds
        self.predicted.append((y0, y1, x0, x1))
        conf = _scripted_confidence(bounds)
        return [FakeResult(self.names, FakeProbs(0, conf))]


# Confidence script: index tiles of a 2x2 grid (bounds map to which quadrant
# the crop covers), or the full frame / center crop.
def _scripted_confidence(bounds: tuple[int, int, int, int]) -> float:
    y0, y1, x0, x1 = bounds
    is_full = (y0, y1, x0, x1) == (0, HEIGHT, 0, WIDTH)
    if is_full:
        return 0.625          # below any configured threshold in tests
    y_mid, x_mid = HEIGHT / 2, WIDTH / 2
    row = 0 if (y0 + y1) / 2 < y_mid else 1
    col = 0 if (x0 + x1) / 2 < x_mid else 1
    return {(0, 0): 0.30, (0, 1): 0.40, (1, 0): 0.50, (1, 1): 0.875}[(row, col)]


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def yolo_detector(monkeypatch: pytest.MonkeyPatch) -> YoloV11NanoDetector:
    """Install fake ultralytics + cv2 modules and build a configured detector."""
    fake_ultralytics = types.ModuleType("ultralytics")
    holders: list[FakeYolo] = []

    class _Yolo(FakeYolo):
        def __init__(self, weights) -> None:  # noqa: N802 - mirrors the API
            super().__init__(weights)
            holders.append(self)

    fake_ultralytics.YOLO = _Yolo

    saved = {name: sys.modules.get(name) for name in ("ultralytics", "cv2")}
    sys.modules["ultralytics"] = fake_ultralytics
    sys.modules.pop("cv2", None)  # force the lazy _import_cv2 path to fail before patch
    monkeypatch.setattr(yolo_mod, "_import_cv2", lambda: (FakeCv2(), None))
    try:
        det = YoloV11NanoDetector({
            "confidence_threshold": THRESHOLD,
            "model_path": None,
        })
        assert det._model is not None
        yield det
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"confidence_threshold": THRESHOLD, "model_path": None}
    base.update(overrides)
    return base


@pytest.fixture
def make_detector(monkeypatch: pytest.MonkeyPatch):
    """Factory building detectors whose ROI settings come straight from config."""

    factory = {}

    def install(cfg: dict[str, Any]) -> YoloV11NanoDetector:
        fake_ultralytics = types.ModuleType("ultralytics")

        class _Yolo(FakeYolo):
            def __init__(self, weights) -> None:  # noqa: N802
                super().__init__(weights)

        fake_ultralytics.YOLO = _Yolo
        saved = sys.modules.get("ultralytics")
        sys.modules["ultralytics"] = fake_ultralytics
        monkeypatch.setattr(yolo_mod, "_import_cv2", lambda: (FakeCv2(), None))
        det = YoloV11NanoDetector(cfg)
        factory["cleanup"] = lambda: _restore(sys.modules, "ultralytics", saved)
        return det

    yield install
    factory.get("cleanup", lambda: None)()


def _restore(modules, name, saved):
    if saved is None:
        modules.pop(name, None)
    else:
        modules[name] = saved


# ----------------------------------------------------------------------
# Region behaviour
# ----------------------------------------------------------------------

def test_full_roi_classifies_whole_frame(yolo_detector: YoloV11NanoDetector):
    result = yolo_detector.detect("frame.jpg", roi="full")

    assert yolo_detector._model.predicted == [(0, HEIGHT, 0, WIDTH)]
    assert result.detected is False           # full frame scores 0.625 < 0.70
    assert result.confidence == 0.625
    assert result.pest_class == "grasshopper"
    assert result.image_path == "frame.jpg"   # ORIGINAL path, not a crop
    assert result.raw["region"] == "full"


def test_default_roi_comes_from_config(make_detector):
    det = make_detector(_cfg(roi="center"))
    result = det.detect("frame.jpg")
    assert result.raw["region"] == "center"


def test_null_roi_result_when_no_model(monkeypatch: pytest.MonkeyPatch):
    saved = sys.modules.get("ultralytics")
    sys.modules.pop("ultralytics", None)  # load failure -> model None -> no-op
    try:
        det = YoloV11NanoDetector(_cfg())
        result = det.detect("frame.jpg", roi="full")
        assert result.detected is False
        assert result.pest_class is None
        assert result.raw == {}
    finally:
        _restore(sys.modules, "ultralytics", saved)


def test_null_image_is_noop(yolo_detector: YoloV11NanoDetector):
    result = yolo_detector.detect(None)
    assert result.raw == {}
    assert yolo_detector._model.predicted == []


def test_missing_image_reports_error(yolo_detector: YoloV11NanoDetector):
    result = yolo_detector.detect("missing.jpg")
    assert result.detected is False
    assert "could not read image" in result.raw.get("error", "")


def test_center_roi_classifies_central_two_thirds(yolo_detector: YoloV11NanoDetector):
    result = yolo_detector.detect("frame.jpg", roi="center")

    # 2/3 crop: 400x200 centered on 600x300 -> x0=y0=50px... verified below.
    assert len(yolo_detector._model.predicted) == 1
    y0, y1, x0, x1 = yolo_detector._model.predicted[0]
    assert x1 - x0 == 400         # 2/3 of 600
    assert y1 - y0 == 200         # 2/3 of 300
    assert x0 == 100 and y0 == 50  # centered: (600-400)/2, (300-200)/2
    # Center crop still "sees" the factor at its centre -> quadrant confidence.
    assert result.raw["region"] == "center"


def test_explicit_box_classifies_only_that_crop(yolo_detector: YoloV11NanoDetector):
    result = yolo_detector.detect("frame.jpg", roi=[0.5, 0.5, 0.5, 0.5])

    (y0, y1, x0, x1), = yolo_detector._model.predicted
    assert (x0, y0) == (300, 150)     # fractional x=0.5, y=0.5
    assert x1 == 600 and y1 == 300    # bottom-right quadrant
    assert result.detected is True    # bottom-right quadrant scores 0.875
    assert result.confidence == 0.875
    assert result.raw["region"] == "box"
    assert result.image_path == "frame.jpg"


def test_invalid_explicit_box_falls_back_to_full(yolo_detector: YoloV11NanoDetector, caplog):
    result = yolo_detector.detect("frame.jpg", roi=[1.5, 0.0, 0.5, 0.5])

    assert yolo_detector._model.predicted == [(0, HEIGHT, 0, WIDTH)]
    assert result.raw["region"] == "full"
    assert any("Invalid roi" in rec.message for rec in caplog.records)


def test_unknown_roi_string_falls_back_to_full(yolo_detector: YoloV11NanoDetector, caplog):
    result = yolo_detector.detect("frame.jpg", roi="banana")

    assert yolo_detector._model.predicted == [(0, HEIGHT, 0, WIDTH)]
    assert result.raw["region"] == "full"
    assert any("Unknown roi" in rec.message for rec in caplog.records)


def test_grid_two_stage_runs_tiles_only_when_full_misses(yolo_detector: YoloV11NanoDetector):
    """Full frame scores 0.625 < 0.70, so the 2x2 tiles must run."""
    result = yolo_detector.detect("frame.jpg", roi="grid")

    predicted = yolo_detector._model.predicted
    assert predicted[0] == (0, HEIGHT, 0, WIDTH)   # stage 1: full frame first
    assert len(predicted) == 5                     # then exactly 4 tiles
    assert result.detected is True                 # tile 1,1 delivers 0.875
    assert result.confidence == 0.875
    assert result.raw["region"] == "tile:1,1"
    assert result.image_path == "frame.jpg"


def test_grid_does_not_tile_when_full_confirmed(make_detector):
    det = make_detector(_cfg(confidence_threshold=0.60))
    result = det.detect("frame.jpg", roi="grid")

    predicted = det._model.predicted
    assert predicted == [(0, HEIGHT, 0, WIDTH)]    # performance guard: no tiles
    assert result.detected is True
    assert result.confidence == 0.625
    assert result.raw["region"] == "full"


def test_grid_subthreshold_after_tiles_still_reported(make_detector):
    """A grid where no tile reaches the threshold: top-1 still reported."""
    det = make_detector(_cfg(confidence_threshold=0.95))
    result = det.detect("frame.jpg", roi="grid")

    assert len(det._model.predicted) == 5          # full + all 4 tiles
    assert result.detected is False
    assert result.pest_class == "grasshopper"
    assert result.confidence == 0.875              # best tile conf, unchanged
    assert result.raw["region"] == "tile:1,1"


def test_grid_from_config_without_explicit_roi(make_detector):
    det = make_detector(_cfg(roi="grid"))
    result = det.detect("frame.jpg")
    assert result.raw["region"] == "tile:1,1"


def test_detected_semantics_use_threshold_exactly(make_detector):
    """Borderline: conf == threshold -> detected, conf just below -> not."""
    det = make_detector(_cfg(confidence_threshold=0.875))
    result_eq = det.detect("frame.jpg", roi=[0.5, 0.5, 0.5, 0.5])  # scores 0.875
    assert result_eq.detected is True

    result_low = make_detector(_cfg(confidence_threshold=0.8751)).detect(
        "frame.jpg", roi=[0.5, 0.5, 0.5, 0.5]
    )
    assert result_low.detected is False
    assert result_low.pest_class == "grasshopper"   # top-1 still carried
    assert result_low.confidence == 0.875
