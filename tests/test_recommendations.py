"""Tests for NPK recommendations module."""

from __future__ import annotations

from recommendations.rule_engine import RuleBasedRecommender
from recommendations.mock_recommender import MockRecommender


def test_rule_engine_recommends_when_below_target():
    """Rule engine should recommend fertilizer when NPK is below target."""
    cfg = {
        "npk_recommendations": {
            "enabled": True,
            "crop_type": "tomato",
            "growth_stage": "vegetative",
            "targets": {
                "tomato": {
                    "vegetative": {
                        "nitrogen": [100, 150],
                        "phosphorus": [40, 60],
                        "potassium": [80, 120],
                    }
                }
            },
            "fertilizers": [
                {
                    "id": "high_n",
                    "name": "High Nitrogen 30-10-10",
                    "n_p_k": [30, 10, 10],
                    "dosage_g_per_10L": 5,
                    "suitable_for": ["vegetative"],
                },
                {
                    "id": "balanced",
                    "name": "Balanced 20-20-20",
                    "n_p_k": [20, 20, 20],
                    "dosage_g_per_10L": 10,
                    "suitable_for": ["vegetative"],
                },
            ],
        }
    }

    recommender = RuleBasedRecommender(cfg)

    # N=50 (below 100), P=50 (in range), K=100 (in range)
    result = recommender.recommend({"nitrogen": 50, "phosphorus": 50, "potassium": 100})

    assert result.recommended is True
    assert result.fertilizer_id == "high_n"
    assert result.dosage_g_per_10L > 0
    assert "nitrogen" in result.deficits
    assert result.deficits["nitrogen"] > 0


def test_rule_engine_no_recommendation_when_in_range():
    """Rule engine should not recommend when all nutrients are in range."""
    cfg = {
        "npk_recommendations": {
            "enabled": True,
            "crop_type": "tomato",
            "growth_stage": "vegetative",
            "targets": {
                "tomato": {
                    "vegetative": {
                        "nitrogen": [100, 150],
                        "phosphorus": [40, 60],
                        "potassium": [80, 120],
                    }
                }
            },
            "fertilizers": [
                {
                    "id": "high_n",
                    "name": "High Nitrogen 30-10-10",
                    "n_p_k": [30, 10, 10],
                    "dosage_g_per_10L": 5,
                    "suitable_for": ["vegetative"],
                },
            ],
        }
    }

    recommender = RuleBasedRecommender(cfg)

    # All in range
    result = recommender.recommend({"nitrogen": 120, "phosphorus": 50, "potassium": 100})

    assert result.recommended is False


def test_rule_engine_disabled_returns_no_recommendation():
    """Disabled engine should never recommend."""
    cfg = {
        "npk_recommendations": {
            "enabled": False,
            "crop_type": "tomato",
            "growth_stage": "vegetative",
        }
    }

    recommender = RuleBasedRecommender(cfg)
    result = recommender.recommend({"nitrogen": 0, "phosphorus": 0, "potassium": 0})

    assert result.recommended is False


def test_rule_engine_scales_dosage_by_deficit():
    """Dosage should scale with severity of deficit."""
    cfg = {
        "npk_recommendations": {
            "enabled": True,
            "crop_type": "tomato",
            "growth_stage": "vegetative",
            "targets": {
                "tomato": {
                    "vegetative": {
                        "nitrogen": [100, 150],
                        "phosphorus": [40, 60],
                        "potassium": [80, 120],
                    }
                }
            },
            "fertilizers": [
                {
                    "id": "high_n",
                    "name": "High Nitrogen 30-10-10",
                    "n_p_k": [30, 10, 10],
                    "dosage_g_per_10L": 10,
                    "suitable_for": ["vegetative"],
                },
            ],
        }
    }

    recommender = RuleBasedRecommender(cfg)

    # Small deficit
    result_small = recommender.recommend({"nitrogen": 90, "phosphorus": 50, "potassium": 100})
    # Large deficit
    result_large = recommender.recommend({"nitrogen": 20, "phosphorus": 50, "potassium": 100})

    assert result_large.dosage_g_per_10L > result_small.dosage_g_per_10L


def test_rule_engine_priority_high_when_deficit_severe():
    """Priority should be high when deficit > 50%."""
    cfg = {
        "npk_recommendations": {
            "enabled": True,
            "crop_type": "tomato",
            "growth_stage": "vegetative",
            "targets": {
                "tomato": {
                    "vegetative": {
                        "nitrogen": [100, 150],
                    }
                }
            },
            "fertilizers": [
                {"id": "high_n", "name": "High N", "n_p_k": [30, 10, 10], "dosage_g_per_10L": 10, "suitable_for": ["vegetative"]},
            ],
        }
    }

    recommender = RuleBasedRecommender(cfg)

    # 30% deficit -> medium
    result_med = recommender.recommend({"nitrogen": 70, "phosphorus": 50, "potassium": 100})
    # 60% deficit -> high
    result_high = recommender.recommend({"nitrogen": 40, "phosphorus": 50, "potassium": 100})

    assert result_med.priority == "medium"
    assert result_high.priority == "high"


def test_mock_recommender_none_mode():
    """Mock recommender in 'none' mode never recommends."""
    cfg = {"npk_recommendations": {"mock": {"mode": "none"}}}
    rec = MockRecommender(cfg)
    result = rec.recommend({"nitrogen": 0, "phosphorus": 0, "potassium": 0})
    assert result.recommended is False


def test_mock_recommender_status():
    """Mock recommender status should include mode."""
    cfg = {"npk_recommendations": {"mock": {"mode": "random"}}}
    rec = MockRecommender(cfg)
    status = rec.status()
    assert status["configured"] is True
    assert status["model"] == "mock"
    assert status["mode"] == "random"