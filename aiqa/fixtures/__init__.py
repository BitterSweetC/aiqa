"""Explicit test data fixture lifecycle management for AIQA."""

from aiqa.fixtures.lifecycle import FixtureLifecycleManager
from aiqa.models.test_case import CreatedEntityRecord, FixtureSpec

__all__ = [
    "CreatedEntityRecord",
    "FixtureLifecycleManager",
    "FixtureSpec",
]
