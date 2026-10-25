"""LangGraph assessment workflow over one repository snapshot."""

from __future__ import annotations

import operator
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, TypedDict

import langsmith
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from projectmesh.code_analysis import analyze_code
from projectmesh.github import fetch_github_snapshot
from projectmesh.models import (
    ANALYSIS_CAPABILITIES,
    CODE_ANALYSIS,
    GITHUB_PROMPT_SUFFIX,
    PROMPT_VERSION,
    PROVIDER_OPENAI_COMPATIBLE,
    SPEC_ANALYSIS,
    AnalysisProvenance,
    AnalysisResult,
    Assessment,
    GitHubSnapshot,
    ProjectConfig,
    ProviderConfig,
    RepositorySnapshot,
)
from projectmesh.provider import CompletionProvider
from projectmesh.render import render_markdown
from projectmesh.repository import capture_repository
from projectmesh.spec_analysis import analyze_spec

PREPARE_CONTEXT = "prepare_context"
ASSESS_SPECIFICATION = "assess_specification"
ASSESS_IMPLEMENTATION_IMPACT = "assess_implementation_impact"
ASSEMBLE_REPORT = "assemble_report"
WORKFLOW_STAGES = (
    PREPARE_CONTEXT,
    ASSESS_SPECIFICATION,
    ASSESS_IMPLEMENTATION_IMPACT,
    ASSEMBLE_REPORT,
)
ANALYSIS_STAGES = (ASSESS_SPECIFICATION, ASSESS_IMPLEMENTATION_IMPACT)


@dataclass(frozen=True)
class AnalysisProvider:
    """Provider configuration and client used by one analysis capability."""

    config: ProviderConfig
    provider: CompletionProvider


@dataclass(frozen=True)
class AssessmentContext:
    """Run-scoped objects that stay out of checkpointable state."""

    project: ProjectConfig
    provider_config: ProviderConfig
    provider: CompletionProvider
    analysis_providers: Mapping[str, AnalysisProvider] = field(default_factory=dict)

    def provider_for(self, capability: str) -> AnalysisProvider:
        return self.analysis_providers.get(
            capability, AnalysisProvider(self.provider_config, self.provider)
        )


class AssessmentState(TypedDict, total=False):
    feature_request: str
    generated_at: datetime | None
    # Large objects are passed by reference; no checkpointer serializes them.
    snapshot: RepositorySnapshot
    github: GitHubSnapshot | None
    spec_result: AnalysisResult
    code_result: AnalysisResult
    assessment: Assessment
    report_markdown: str
    completed_stages: Annotated[tuple[str, ...], operator.add]


def _endpoint_label(config: ProviderConfig) -> str:
    return (
        config.endpoint
        if config.type == PROVIDER_OPENAI_COMPATIBLE
        else "claude-cli (local Claude Code session)"
    )


def assemble_assessment(
    project: ProjectConfig,
    feature_request: str,
    snapshot: RepositorySnapshot,
    provider_config: ProviderConfig,
    analyses: tuple[AnalysisResult, ...],
    github: GitHubSnapshot | None,
    *,
    analysis_provider_configs: Mapping[str, ProviderConfig] | None = None,
) -> Assessment:
    """Combine completed analyses with snapshot and provider metadata."""
    configs = analysis_provider_configs or {}
    provenances: list[AnalysisProvenance] = []
    for analysis in analyses:
        cfg = configs.get(analysis.capability, provider_config)
        provenances.append(
            AnalysisProvenance(
                analysis.capability,
                analysis.title,
                cfg.model,
                _endpoint_label(cfg),
            )
        )
    return Assessment(
        project=project,
        feature_request=feature_request,
        analyses=analyses,
        model=provider_config.model,
        prompt_version=(
            PROMPT_VERSION + GITHUB_PROMPT_SUFFIX if github else PROMPT_VERSION
        ),
        provider_endpoint=_endpoint_label(provider_config),
        source_sha=snapshot.source_sha,
        source_paths=tuple(source_file.path for source_file in snapshot.files),
        skipped_paths=snapshot.skipped_paths,
        github=github,
        analysis_providers=tuple(provenances),
    )


def prepare_context(
    state: AssessmentState, runtime: Runtime[AssessmentContext]
) -> dict:
    ctx = runtime.context
    snapshot = capture_repository(ctx.project.path)
    github = None
    if ctx.project.github_repo:
        github = fetch_github_snapshot(ctx.project.github_repo)
    return {
        "snapshot": snapshot,
        "github": github,
        "completed_stages": (PREPARE_CONTEXT,),
    }


def assess_specification(
    state: AssessmentState, runtime: Runtime[AssessmentContext]
) -> dict:
    ctx = runtime.context
    result = analyze_spec(
        state["feature_request"],
        state["snapshot"],
        ctx.provider_for(SPEC_ANALYSIS).provider,
        state.get("github"),
    )
    return {
        "spec_result": result,
        "completed_stages": (ASSESS_SPECIFICATION,),
    }


def assess_implementation_impact(
    state: AssessmentState, runtime: Runtime[AssessmentContext]
) -> dict:
    ctx = runtime.context
    result = analyze_code(
        state["feature_request"],
        state["snapshot"],
        ctx.provider_for(CODE_ANALYSIS).provider,
    )
    return {
        "code_result": result,
        "completed_stages": (ASSESS_IMPLEMENTATION_IMPACT,),
    }


def assemble_report(
    state: AssessmentState, runtime: Runtime[AssessmentContext]
) -> dict:
    ctx = runtime.context
    assessment = assemble_assessment(
        ctx.project,
        state["feature_request"],
        state["snapshot"],
        ctx.provider_config,
        (state["spec_result"], state["code_result"]),
        state.get("github"),
        analysis_provider_configs={
            capability: ctx.provider_for(capability).config
            for capability in ANALYSIS_CAPABILITIES
        },
    )
    report_markdown = render_markdown(
        assessment, generated_at=state.get("generated_at")
    )
    return {
        "assessment": assessment,
        "report_markdown": report_markdown,
        "completed_stages": (ASSEMBLE_REPORT,),
    }


def build_assessment_graph():
    graph = StateGraph(AssessmentState, context_schema=AssessmentContext)
    graph.add_node(PREPARE_CONTEXT, prepare_context)
    graph.add_node(ASSESS_SPECIFICATION, assess_specification)
    graph.add_node(ASSESS_IMPLEMENTATION_IMPACT, assess_implementation_impact)
    graph.add_node(ASSEMBLE_REPORT, assemble_report)
    graph.add_edge(START, PREPARE_CONTEXT)
    graph.add_edge(PREPARE_CONTEXT, ASSESS_SPECIFICATION)
    graph.add_edge(PREPARE_CONTEXT, ASSESS_IMPLEMENTATION_IMPACT)
    graph.add_edge(list(ANALYSIS_STAGES), ASSEMBLE_REPORT)
    graph.add_edge(ASSEMBLE_REPORT, END)
    return graph.compile()


def run_assessment_workflow(
    project: ProjectConfig,
    feature_request: str,
    provider_config: ProviderConfig,
    provider: CompletionProvider,
    generated_at: datetime | None = None,
    analysis_providers: Mapping[str, AnalysisProvider] | None = None,
) -> AssessmentState:
    # Tracing is forced off so request text and evidence never leave the process
    # through LangSmith, even when LANGSMITH_TRACING or LANGCHAIN_TRACING_V2 is set.
    with langsmith.tracing_context(enabled=False):
        return build_assessment_graph().invoke(
            {
                "feature_request": feature_request,
                "generated_at": generated_at,
                "completed_stages": (),
            },
            context=AssessmentContext(
                project=project,
                provider_config=provider_config,
                provider=provider,
                analysis_providers=analysis_providers or {},
            ),
        )
