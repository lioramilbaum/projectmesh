"""Sequential assessment orchestration."""

from __future__ import annotations

from projectmesh.code_analysis import analyze_code
from projectmesh.models import (
    PROMPT_VERSION,
    Assessment,
    ProjectConfig,
    ProviderConfig,
    RepositorySnapshot,
)
from projectmesh.provider import CompletionProvider
from projectmesh.spec_analysis import analyze_spec


def create_assessment(
    project: ProjectConfig,
    feature_request: str,
    snapshot: RepositorySnapshot,
    provider_config: ProviderConfig,
    provider: CompletionProvider,
) -> Assessment:
    """Run both assessment capabilities in fixed sequence over one snapshot."""
    results = (
        analyze_spec(
            feature_request=feature_request,
            snapshot=snapshot,
            provider=provider,
        ),
        analyze_code(
            feature_request=feature_request,
            snapshot=snapshot,
            provider=provider,
        ),
    )
    return Assessment(
        project=project,
        feature_request=feature_request,
        analyses=results,
        model=provider_config.model,
        prompt_version=PROMPT_VERSION,
        provider_endpoint=provider_config.endpoint,
        source_sha=snapshot.source_sha,
        source_paths=tuple(source_file.path for source_file in snapshot.files),
        skipped_paths=snapshot.skipped_paths,
    )
