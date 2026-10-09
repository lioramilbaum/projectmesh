"""Specification-level feature fit analysis."""

from __future__ import annotations

from projectmesh.analysis import analyze
from projectmesh.models import AnalysisResult, RepositorySnapshot
from projectmesh.provider import CompletionProvider


def analyze_spec(
    feature_request: str,
    snapshot: RepositorySnapshot,
    provider: CompletionProvider,
) -> AnalysisResult:
    """Assess feature fit against documented repository context."""
    return analyze(
        capability="spec_analysis",
        title="Repository context and feature fit",
        purpose=(
            "Identify relevant existing components, interfaces, and patterns, "
            "then explain how the feature request relates to them."
        ),
        feature_request=feature_request,
        snapshot=snapshot,
        provider=provider,
    )
