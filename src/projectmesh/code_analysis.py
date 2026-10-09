"""Code-level implementation impact analysis."""

from __future__ import annotations

from projectmesh.analysis import analyze
from projectmesh.models import AnalysisResult, RepositorySnapshot
from projectmesh.provider import CompletionProvider


def analyze_code(
    feature_request: str,
    snapshot: RepositorySnapshot,
    provider: CompletionProvider,
) -> AnalysisResult:
    """Assess likely code impact, risks, and implementation questions."""
    return analyze(
        capability="code_analysis",
        title="Implementation impact",
        purpose=(
            "Outline likely affected areas, dependencies, risks, and open "
            "questions. Separate verified repository facts from reasoned "
            "implications."
        ),
        feature_request=feature_request,
        snapshot=snapshot,
        provider=provider,
    )
