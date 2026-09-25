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
    """Collection of all discovered routes and testable features on a target site."""

    base_url: str
    routes: list[str] = Field(default_factory=list, description="Unique routes discovered on the site")
    features: list[DiscoveredFeature] = Field(default_factory=list)

    def get_by_type(self, feat_type: str) -> list[DiscoveredFeature]:
        """Return all features of a specific category."""
        return [f for f in self.features if f.type.lower() == feat_type.lower()]


class FeatureCoverageSummary(BaseModel):
    """Summary of coverage for a specific feature type."""

    total: int = 0
    covered: int = 0
    rate: float = 0.0


class CoverageReport(BaseModel):
    """Detailed coverage report comparing executed/planned tests against the FeatureRegistry."""

    base_url: str
    total_features: int
    covered_features: int
    coverage_rate: float = Field(description="0.0 to 1.0 representing percentage coverage")
    tested_feature_ids: list[str] = Field(default_factory=list)
    untested_features: list[DiscoveredFeature] = Field(default_factory=list)
    by_type: dict[str, FeatureCoverageSummary] = Field(default_factory=dict)
    discovered_routes_count: int = 0
