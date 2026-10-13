"""Tests for pest treatment advisor and pest suggestion config validation."""

from __future__ import annotations

import json

import pytest

from config import ConfigError, load_config
from pest_detection.treatment_advisor import (
    PestTreatmentAdvisor,
    TreatmentAdvice,
)


def _cfg_with_kb() -> dict:
    return load_config() if False else None


def test_match_exact_class():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("caterpillar")
    assert advice.matched is True
    assert advice.treatment_id == "caterpillar"
    assert advice.title == "Caterpillar (general)"
    assert advice.severity in ("low", "moderate", "high")


def test_match_via_alias():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("aphid")
    assert advice.matched is True
    assert advice.treatment_id == "aphids"


def test_case_insensitive():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    for variant in ("APHID", "  Aphid  ", "ApHiD"):
        advice = adv.suggest(variant)
        assert advice.matched is True
        assert advice.treatment_id == "aphids"


def test_whitespace_tolerant_lookup():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("  mite  ")
    assert advice.matched is True
    assert advice.treatment_id == "mites"


def test_unknown_class_returns_fallback():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("not-a-pest")
    assert advice.matched is False
    assert advice.pest_class == "not-a-pest"
    assert advice.treatment_id is None
    assert advice.title == "Pest not in local guide"


def test_none_returns_fallback():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest(None)
    assert advice.matched is False
    assert advice.pest_class is None
    assert advice.treatment_id is None


def test_empty_string_returns_fallback():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("")
    assert advice.matched is False
    assert advice.pest_class == ""
    assert advice.treatment_id is None


def test_disclaimer_always_populated():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    for cls in ("aphid", "nope", None):
        advice = adv.suggest(cls)
        assert advice.disclaimer
        assert "label" in advice.disclaimer.lower() or "prescription" in advice.disclaimer.lower()


def test_to_dict_json_serialisable():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("thrips")
    d = advice.to_dict()
    text = json.dumps(d)
    assert "thrips" in text.lower() or d["treatment_id"] == "thrips"
    assert d["cultural"] == advice.cultural
    assert isinstance(d["cultural"], list)


def test_all_mock_detector_classes_covered():
    from config.config import default_config
    from pest_detection.mock_detector import DEFAULT_PEST_CLASSES

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    for cls in DEFAULT_PEST_CLASSES:
        advice = adv.suggest(cls)
        assert advice.matched is True, f"no treatment for mock class {cls!r}"


def test_all_real_model_classes_covered():
    from config.config import default_config

    real = ["aphids", "armyworm", "beetle", "bollworm", "grasshopper", "mites", "mosquito", "sawfly", "stem_borer"]
    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    for cls in real:
        advice = adv.suggest(cls)
        assert advice.matched is True, f"no treatment for real class {cls!r}"


def test_fungus_is_treated_as_disease():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("fungus")
    assert advice.matched is True
    # chemical entry should mention fungicide class, not insecticide-only phrasing
    assert any("fungicide" in c.lower() for c in advice.chemical)
    assert advice.cultural


def test_empty_config_does_not_raise():
    adv = PestTreatmentAdvisor({})
    advice = adv.suggest("aphid")
    assert isinstance(advice, TreatmentAdvice)
    assert advice.matched is False
    assert advice.cultural == []
    assert advice.organic == []
    assert advice.chemical == []
    assert advice.notes == []
    assert adv.status()["treatments_configured"] == 0


def test_none_config_does_not_raise():
    adv = PestTreatmentAdvisor(None)  # type: ignore[arg-type]
    advice = adv.suggest("aphid")
    assert advice.matched is False
    assert adv.status()["configured"] is True


def test_malformed_config_does_not_raise():
    cfg = {
        "pest_detection": {
            "suggestions": "not-a-dict",
            "treatments": ["garbage", {"names": [123]}, None],
        }
    }
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("aphid")
    assert advice.matched is False
    assert adv.status()["treatments_configured"] >= 0


def test_match_populates_all_fields():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("aphids")
    assert advice.matched is True
    assert advice.pest_class == "aphids"
    assert advice.treatment_id == "aphids"
    assert advice.title
    assert advice.summary
    assert isinstance(advice.cultural, list) and advice.cultural
    assert isinstance(advice.organic, list) and advice.organic
    assert isinstance(advice.chemical, list) and advice.chemical
    assert isinstance(advice.notes, list) and advice.notes


def test_status_shape():
    from config.config import default_config

    cfg = default_config()
    adv = PestTreatmentAdvisor(cfg)
    st = adv.status()
    assert st == {"configured": True, "model": "rule_engine", "treatments_configured": 14}


def test_missing_fallback_still_populates_disclaimer():
    cfg = {
        "pest_detection": {
            "suggestions": {
                "disclaimer": "be careful",
                "enabled": True,
            },
            "treatments": [],
        }
    }
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("nope")
    assert advice.matched is False
    assert advice.title is None
    assert advice.disclaimer == "be careful"


def test_no_disclaimer_config_yields_none():
    cfg = {"pest_detection": {"treatments": []}}
    adv = PestTreatmentAdvisor(cfg)
    advice = adv.suggest("nope")
    assert advice.disclaimer is None


# --- config validation tests ---


def test_config_rejects_bad_suggestions_enabled(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data.setdefault("pest_detection", {})
    data["pest_detection"].setdefault("suggestions", {})
    data["pest_detection"]["suggestions"]["enabled"] = "not-a-bool"
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "enabled" in str(exc.value)


def test_config_rejects_bad_treatment(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data.setdefault("pest_detection", {})
    data["pest_detection"]["treatments"] = [
        {"id": "x", "names": ["x"], "title": "t", "severity": "extreme", "summary": "s"}
    ]
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "severity" in str(exc.value)


def test_config_rejects_duplicate_alias(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data.setdefault("pest_detection", {})
    data["pest_detection"]["treatments"] = [
        {"id": "a", "names": ["shared"], "title": "t", "severity": "low", "summary": "s"},
        {"id": "b", "names": ["shared"], "title": "t", "severity": "low", "summary": "s"},
    ]
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "duplicate alias" in str(exc.value).lower()


def test_config_rejects_duplicate_id(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data.setdefault("pest_detection", {})
    data["pest_detection"]["treatments"] = [
        {"id": "dup", "names": ["a"], "title": "t", "severity": "low", "summary": "s"},
        {"id": "dup", "names": ["b"], "title": "t", "severity": "low", "summary": "s"},
    ]
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "duplicate treatment id" in str(exc.value).lower()


def test_config_rejects_bad_str_list(temp_config):
    import yaml

    with open(temp_config) as f:
        data = yaml.safe_load(f)
    data.setdefault("pest_detection", {})
    data["pest_detection"]["treatments"] = [
        {
            "id": "x",
            "names": ["x"],
            "title": "t",
            "severity": "low",
            "summary": "s",
            "cultural": ["ok", 123],
        }
    ]
    with open(temp_config, "w") as f:
        yaml.safe_dump(data, f)

    with pytest.raises(ConfigError) as exc:
        load_config(temp_config)
    assert "cultural" in str(exc.value)
