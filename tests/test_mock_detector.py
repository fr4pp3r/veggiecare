"""Tests for the mock pest detector."""

from __future__ import annotations

import pytest
from pest_detection.mock_detector import MockDetector
from pest_detection.detector import DetectionResult


def _base_pest_cfg():
    return {
        "pest_classes": ["aphid", "caterpillar", "fungus"],
        "mock": {
            "mode": "none",
            "sequence": [],
            "random": {"detection_probability": 0.25, "confidence_min": 0.50, "confidence_max": 0.98},
        },
    }


def test_mock_detector_none_mode_never_detects():
    cfg = _base_pest_cfg()
    cfg["mock"]["mode"] = "none"
    det = MockDetector(cfg)

    for _ in range(5):
        result = det.detect()
        assert isinstance(result, DetectionResult)
        assert result.detected is False
        assert result.pest_class is None
        assert result.confidence is None
        assert result.model == "mock"


def test_mock_detector_sequential_mode_cycles():
    cfg = _base_pest_cfg()
    cfg["mock"]["mode"] = "sequential"
    cfg["mock"]["sequence"] = [
        ("aphid", 0.91, True),
        ("caterpillar", 0.85, True),
        ("none", 0.0, False),
    ]
    det = MockDetector(cfg)

    r1 = det.detect()
    assert r1.detected is True
    assert r1.pest_class == "aphid"
    assert r1.confidence == 0.91

    r2 = det.detect()
    assert r2.detected is True
    assert r2.pest_class == "caterpillar"
    assert r2.confidence == 0.85

    r3 = det.detect()
    assert r3.detected is False
    assert r3.pest_class is None

    # Should cycle back
    r4 = det.detect()
    assert r4.detected is True
    assert r4.pest_class == "aphid"


def test_mock_detector_random_mode_respects_probability():
    cfg = _base_pest_cfg()
    cfg["mock"]["mode"] = "random"
    cfg["mock"]["random"]["detection_probability"] = 1.0  # always detect
    cfg["mock"]["random"]["confidence_min"] = 0.90
    cfg["mock"]["random"]["confidence_max"] = 0.95
    det = MockDetector(cfg)

    for _ in range(10):
        result = det.detect()
        assert result.detected is True
        assert result.pest_class in cfg["pest_classes"]
        assert 0.90 <= result.confidence <= 0.95


def test_mock_detector_random_mode_zero_probability_never_detects():
    cfg = _base_pest_cfg()
    cfg["mock"]["mode"] = "random"
    cfg["mock"]["random"]["detection_probability"] = 0.0
    det = MockDetector(cfg)

    for _ in range(10):
        result = det.detect()
        assert result.detected is False


def test_mock_detector_status():
    cfg = _base_pest_cfg()
    cfg["mock"]["mode"] = "random"
    det = MockDetector(cfg)
    status = det.status()
    assert status["configured"] is True
    assert status["model"] == "mock"
    assert status["mode"] == "random"