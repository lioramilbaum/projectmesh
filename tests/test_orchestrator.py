from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

import langsmith
import langsmith.utils
import pytest
from langchain_core.tracers.langchain import LangChainTracer
from test_assessment import (
    GitHubStubProvider,
    ScriptedProvider,
    StubProvider,
    _config_file,
    _make_project,
    _section,
    _tree_bytes,
)
from test_github import _graphql_response, _stub_github

from projectmesh import orchestrator
from projectmesh.code_analysis import analyze_code
from projectmesh.config import load_config
from projectmesh.github import GitHubError
from projectmesh.orchestrator import (
    ASSEMBLE_REPORT,
    ASSESS_IMPLEMENTATION_IMPACT,
    ASSESS_SPECIFICATION,
    PREPARE_CONTEXT,
    WORKFLOW_STAGES,
    AssessmentContext,
    assemble_assessment,
    build_assessment_graph,
    run_assessment_workflow,
)
from projectmesh.provider import ProviderError
from projectmesh.render import render_markdown
from projectmesh.repository import capture_repository
from projectmesh.spec_analysis import analyze_spec

REQUEST = "Export archived records"
FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
REAL_CAPTURE = capture_repository
REAL_SPEC = analyze_spec
REAL_CODE = analyze_code


def _load(tmp_path: Path, github_repo: str | None = None):
    project = _make_project(tmp_path)
    return load_config(_config_file(tmp_path, project, github_repo=github_repo))


def _context(config, provider) -> AssessmentContext:
    return AssessmentContext(
        project=config.projects["fixture"],
        provider_config=config.provider,
        provider=provider,
    )


def _initial_state(generated_at: datetime | None = None) -> dict:
    return {
        "feature_request": REQUEST,
        "generated_at": generated_at,
        "completed_stages": (),
    }


def test_graph_declares_four_stages_in_linear_order() -> None:
    graph = build_assessment_graph().get_graph()

    assert set(graph.nodes) == {"__start__", *WORKFLOW_STAGES, "__end__"}
    assert {(edge.source, edge.target) for edge in graph.edges} == {
        ("__start__", PREPARE_CONTEXT),
        (PREPARE_CONTEXT, ASSESS_SPECIFICATION),
        (ASSESS_SPECIFICATION, ASSESS_IMPLEMENTATION_IMPACT),
        (ASSESS_IMPLEMENTATION_IMPACT, ASSEMBLE_REPORT),
        (ASSEMBLE_REPORT, "__end__"),
    }
    assert not any(edge.conditional for edge in graph.edges)


def test_stream_transitions_follow_established_order(tmp_path: Path) -> None:
    config = _load(tmp_path)
    provider = StubProvider()

    updates = list(
        build_assessment_graph().stream(
            _initial_state(FIXED_TIME),
            context=_context(config, provider),
            stream_mode="updates",
        )
    )

    assert [name for update in updates for name in update] == list(WORKFLOW_STAGES)
    keys = {name: set(update[name]) for update in updates for name in update}
    assert keys == {
        PREPARE_CONTEXT: {"snapshot", "github", "completed_stages"},
        ASSESS_SPECIFICATION: {"spec_result", "completed_stages"},
        ASSESS_IMPLEMENTATION_IMPACT: {"code_result", "completed_stages"},
        ASSEMBLE_REPORT: {"assessment", "report_markdown", "completed_stages"},
    }
    assert all("provider" not in update[name] for update in updates for name in update)

    final_state = build_assessment_graph().invoke(
        _initial_state(FIXED_TIME),
        context=_context(config, StubProvider()),
    )
    assert final_state["completed_stages"] == WORKFLOW_STAGES


def test_analyses_share_one_frozen_snapshot_and_github_only_reaches_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-tok")
    _stub_github(monkeypatch, _graphql_response())
    project = _make_project(tmp_path)
    original = _tree_bytes(project)
    config = load_config(_config_file(tmp_path, project, github_repo="acme/widgets"))
    capture_calls: list[Path] = []
    spec_calls: list[tuple[object, object]] = []
    code_calls: list[object] = []

    def recording_capture(root):
        capture_calls.append(root)
        return REAL_CAPTURE(root)

    def recording_spec(feature_request, snapshot, provider, github=None):
        spec_calls.append((snapshot, github))
        return REAL_SPEC(feature_request, snapshot, provider, github)

    def recording_code(feature_request, snapshot, provider):
        code_calls.append(snapshot)
        return REAL_CODE(feature_request, snapshot, provider)

    monkeypatch.setattr(orchestrator, "capture_repository", recording_capture)
    monkeypatch.setattr(orchestrator, "analyze_spec", recording_spec)
    monkeypatch.setattr(orchestrator, "analyze_code", recording_code)

    final_state = run_assessment_workflow(
        project=config.projects["fixture"],
        feature_request=REQUEST,
        provider_config=config.provider,
        provider=GitHubStubProvider(),
    )

    snapshot = final_state["snapshot"]
    assert len(capture_calls) == 1
    assert len(spec_calls) == 1 and len(code_calls) == 1
    assert spec_calls[0][0] is snapshot
    assert code_calls[0] is snapshot
    assert spec_calls[0][1] is final_state["github"]
    assert final_state["github"] is not None
    assessment = final_state["assessment"]
    assert assessment.source_sha == snapshot.source_sha
    assert "+github-v1" in assessment.prompt_version
    assert _tree_bytes(project) == original


def test_workflow_report_matches_direct_composition(tmp_path: Path) -> None:
    config = _load(tmp_path)
    project_config = config.projects["fixture"]

    final_state = run_assessment_workflow(
        project=project_config,
        feature_request=REQUEST,
        provider_config=config.provider,
        provider=StubProvider(),
        generated_at=FIXED_TIME,
    )

    provider = StubProvider()
    snapshot = capture_repository(project_config.path)
    spec = analyze_spec(REQUEST, snapshot, provider)
    code = analyze_code(REQUEST, snapshot, provider)
    assessment = assemble_assessment(
        project_config,
        REQUEST,
        snapshot,
        config.provider,
        (spec, code),
        None,
    )
    expected = render_markdown(assessment, generated_at=FIXED_TIME)

    assert final_state["report_markdown"] == expected
    assert "This made-up citation must be rejected." in _section(
        final_state["report_markdown"], "Unsupported claims"
    )


def test_github_failure_stops_graph_before_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-tok")
    _stub_github(
        monkeypatch,
        HTTPError("https://api.github.com/graphql", 404, "Not Found", {}, None),
    )
    config = _load(tmp_path, github_repo="acme/widgets")
    provider = StubProvider()
    updates: list = []

    with pytest.raises(GitHubError):
        for update in build_assessment_graph().stream(
            _initial_state(FIXED_TIME),
            context=_context(config, provider),
            stream_mode="updates",
        ):
            updates.append(update)  # noqa: PERF402

    assert updates == []
    assert provider.prompts == []


def test_provider_error_stops_before_impact_and_assembly(tmp_path: Path) -> None:
    config = _load(tmp_path)
    provider = ScriptedProvider([ProviderError("Provider returned HTTP 503.")])
    updates: list = []

    with pytest.raises(ProviderError) as excinfo:
        for update in build_assessment_graph().stream(
            _initial_state(FIXED_TIME),
            context=_context(config, provider),
            stream_mode="updates",
        ):
            updates.append(update)  # noqa: PERF402

    assert [name for update in updates for name in update] == [PREPARE_CONTEXT]
    assert type(excinfo.value) is ProviderError
    assert str(excinfo.value) == "Provider returned HTTP 503."
    assert len(provider.prompts) == 1


def test_langsmith_tracing_environment_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    # langsmith memoizes env lookups, so clear them before and after this test.
    langsmith.utils.get_env_var.cache_clear()
    request.addfinalizer(langsmith.utils.get_env_var.cache_clear)
    instances: list[LangChainTracer] = []
    original_init = LangChainTracer.__init__

    def spy_init(self, *args, **kwargs):
        instances.append(self)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(LangChainTracer, "__init__", spy_init)
    config = _load(tmp_path)
    assert langsmith.utils.tracing_is_enabled() is True
    with langsmith.tracing_context(enabled=False):
        assert langsmith.utils.tracing_is_enabled() is False

    final_state = run_assessment_workflow(
        project=config.projects["fixture"],
        feature_request=REQUEST,
        provider_config=config.provider,
        provider=StubProvider(),
    )

    assert langsmith.utils.tracing_is_enabled() is True
    assert instances == []
    assert final_state["report_markdown"]


def test_only_orchestrator_imports_langgraph() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "projectmesh"
    forbidden = {"langgraph", "langsmith"}

    def imported_roots(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    offenders = {
        path.name
        for path in source_root.rglob("*.py")
        if imported_roots(path) & forbidden
    }

    assert offenders == {"orchestrator.py"}
    for name in (
        "analysis.py",
        "spec_analysis.py",
        "code_analysis.py",
        "provider.py",
        "models.py",
        "repository.py",
        "github.py",
        "render.py",
        "cli.py",
    ):
        assert name not in offenders
