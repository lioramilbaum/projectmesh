"""Typed values shared by projectmesh's assessment pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROMPT_VERSION = "projectmesh-assessment-v1"


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    name: str
    path: Path


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: int = 60


@dataclass(frozen=True)
class AppConfig:
    projects: dict[str, ProjectConfig]
    provider: ProviderConfig
    output_dir: Path


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str


@dataclass(frozen=True)
class RepositorySnapshot:
    root: Path
    files: tuple[SourceFile, ...]
    source_sha: str
    skipped_paths: tuple[str, ...] = ()

    def file_map(self) -> dict[str, str]:
        """Return the captured source text keyed by normalized repository path."""
        return {source_file.path: source_file.text for source_file in self.files}


@dataclass(frozen=True)
class Evidence:
    claim: str
    source_path: str
    quote: str
    capability: str


@dataclass(frozen=True)
class Inference:
    claim: str
    basis: str
    capability: str


@dataclass(frozen=True)
class AnalysisResult:
    capability: str
    title: str
    summary: str
    cited_evidence: tuple[Evidence, ...]
    inferences: tuple[Inference, ...]
    unsupported_claims: tuple[str, ...]
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]


@dataclass(frozen=True)
class Assessment:
    project: ProjectConfig
    feature_request: str
    analyses: tuple[AnalysisResult, ...]
    model: str
    prompt_version: str
    provider_endpoint: str
    source_sha: str
    source_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]
