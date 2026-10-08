"""NPK-based fertilizer recommendation system.

Provides a pluggable interface for generating fertilizer recommendations
from soil NPK readings. The rule-based engine is the default implementation
(negligible RAM, fully explainable, config-driven). ML implementations
(sklearn, ONNX) can swap in without changing controller or dashboard code.
"""

from recommendations.base import NPKRecommender, RecommendationResult
from recommendations.rule_engine import RuleBasedRecommender
from recommendations.mock_recommender import MockRecommender
from recommendations.sklearn_recommender import SklearnRecommender
from recommendations.onnx_recommender import ONNXRecommender

__all__ = [
    "NPKRecommender",
    "RecommendationResult",
    "RuleBasedRecommender",
    "MockRecommender",
    "SklearnRecommender",
    "ONNXRecommender",
]