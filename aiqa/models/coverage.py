"""Pydantic models for Stage 3 Feature Registry and Test Coverage Tracking."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FeatureType = Literal[
    "navigation",
    "search",
    "auth",
    "cart",
    "checkout",
    "form",
    "content",
    "interaction",
]


class DiscoveredFeature(BaseModel):
    """A testable feature or user flow discovered on a website."""

    id: str = Field(description="Unique feature identifier, e.g. 'feat_search_bar'")
    name: str = Field(description="Human-readable feature name")
    type: str = Field(description="Category of the feature, e.g. 'search', 'cart', 'auth'")
    url: str = Field(description="URL or route where the feature was detected")
    selector: str | None = Field(default=None, description="DOM selector if element-specific")
    description: str = Field(default="", description="Description of the feature capabilities")


class FeatureRegistry(BaseModel):
    """Collection of all discovered routes, page summaries, blocked areas, and testable features."""

    base_url: str
    routes: list[str] = Field(default_factory=list, description="Unique routes discovered on the site")
    features: list[DiscoveredFeature] = Field(default_factory=list)
    pages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Structured per-page inspection summaries discovered during crawling",
    )
    blocked_routes: dict[str, str] = Field(
        default_factory=dict,
        description="Routes that could not be entered (e.g. 401/403/login-wall/cross-origin/error) and reasons",
    )

    def get_by_type(self, feat_type: str) -> list[DiscoveredFeature]:
        """Return all features of a specific category."""
        return [f for f in self.features if f.type.lower() == feat_type.lower()]


class FeatureCoverageSummary(BaseModel):
    """Summary of coverage for a specific feature type."""

    total: int = 0
    covered: int = 0
    verified: int = 0
    rate: float = 0.0
    verified_rate: float = 0.0


class CoverageReport(BaseModel):
    """Detailed coverage report comparing executed/planned tests against the FeatureRegistry."""

    base_url: str
    total_features: int
    covered_features: int
    coverage_rate: float = Field(description="0.0 to 1.0 representing planned or matched coverage")
    verified_features: int = Field(
        default=0,
        description="Count of features verified by passing test execution evidence",
    )
    verified_coverage_rate: float = Field(
        default=0.0,
        description="0.0 to 1.0 representing execution-verified coverage rate",
    )
    tested_feature_ids: list[str] = Field(default_factory=list)
    verified_feature_ids: list[str] = Field(default_factory=list)
    failed_feature_ids: list[str] = Field(default_factory=list)
    inconclusive_feature_ids: list[str] = Field(default_factory=list)
    untested_features: list[DiscoveredFeature] = Field(default_factory=list)
    unverified_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="Map of feature ID to reason it remains unverified",
    )
    blocked_routes: dict[str, str] = Field(
        default_factory=dict,
        description="Routes that could not be entered during crawling and why",
    )
    by_type: dict[str, FeatureCoverageSummary] = Field(default_factory=dict)
    discovered_routes_count: int = 0
