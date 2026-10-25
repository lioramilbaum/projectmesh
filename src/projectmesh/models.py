"""Typed values shared by projectmesh's assessment pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


PROMPT_VERSION = "projectmesh-assessment-v2"
GITHUB_PROMPT_SUFFIX = "+github-v1"
PROVIDER_OPENAI_COMPATIBLE = "openai-compatible"
PROVIDER_CLAUDE_CLI = "claude-cli"
SPEC_ANALYSIS = "spec_analysis"
CODE_ANALYSIS = "code_analysis"
ANALYSIS_CAPABILITIES = (SPEC_ANALYSIS, CODE_ANALYSIS)


@dataclass(frozen=True)
class ProjectConfig:
    key: str
    name: str
    path: Path
    github_repo: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: int = 60
    type: str = "openai-compatible"


@dataclass(frozen=True)
class AppConfig:
    projects: dict[str, ProjectConfig]
    provider: ProviderConfig
    output_dir: Path
    analysis_providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def provider_for(self, capability: str) -> ProviderConfig:
        """Return the provider configured for one analysis, else the default."""
        return self.analysis_providers.get(capability, self.provider)


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
class GitHubSnapshot:
    repo: str
    items: tuple[SourceFile, ...]
    source_sha: str
    skipped_urls: tuple[str, ...] = ()

    def file_map(self) -> dict[str, str]:
        """Return the rendered item text keyed by its GitHub URL."""
        return {item.path: item.text for item in self.items}


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
    problem_statement: str = ""
    goals: tuple[str, ...] = ()
    existing_capabilities: tuple[Evidence, ...] = ()
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisProvenance:
    capability: str
    title: str
    model: str
    provider_endpoint: str


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
    github: GitHubSnapshot | None = None
    analysis_providers: tuple[AnalysisProvenance, ...] = ()
