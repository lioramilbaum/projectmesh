from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError

import pytest
from test_github import _graphql_response, _stub_github

from projectmesh.cli import _report_filename, _write_report, run_assessment
from projectmesh.config import load_config
from projectmesh.github import GitHubError
from projectmesh.models import PROMPT_VERSION
from projectmesh.provider import CompletionProvider, ProviderError


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "fixture"
    project.mkdir()
    (project / "README.md").write_text(
        "# Fixture service\n\nRecords are stored in `records.json`.\n",
        encoding="utf-8",
    )
    (project / "records.json").write_text(
        '{"records": []}\n', encoding="utf-8"
    )
    return project


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        item.relative_to(root).as_posix(): item.read_bytes()
        for item in root.rglob("*")
        if item.is_file()
    }


def _section(report, heading):
    marker = f"\n## {heading}\n"
    start = report.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = report.find("\n## ", start)
    return report[start:end] if end != -1 else report[start:]


class StubProvider(CompletionProvider):
    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        if "Capability: Repository context" in user_prompt:
            result = {
                "summary": "The service describes its record storage.",
                "problem_statement": "The system lacks X",
                "goals": ["Enable Y", "Support Z"],
                "cited_evidence": [
                    {
                        "claim": "The README names the record data file.",
                        "source_path": "README.md",
                        "quote": "Records are stored in `records.json`.",
                    },
                    {
                        "claim": "This made-up citation must be rejected.",
                        "source_path": "missing.py",
                        "quote": "not in snapshot",
                    },
                ],
                "existing_capabilities": [
                    {
                        "claim": "Records are stored in records.json",
                        "source_path": "README.md",
                        "quote": "Records are stored in `records.json`.",
                        "capability": "Repository context and feature fit",
                    },
                    {
                        "claim": "This existing capability is not in the file",
                        "source_path": "README.md",
                        "quote": "This quote does not exist in README.md.",
                        "capability": "Repository context and feature fit",
                    },
                ],
                "inferences": [
                    {
                        "claim": "An export may reuse the record storage format.",
                        "basis": "The README points to records.json.",
                    }
                ],
                "unsupported_claims": ["The service has a REST API."],
                "risks": ["The export format is unspecified."],
                "open_questions": ["Which records should be included?"],
                "gaps": [],
            }
        else:
            result = {
                "summary": "An export could read the existing record store.",
                "problem_statement": "",
                "goals": [],
                "cited_evidence": [
                    {
                        "claim": "The fixture has a records collection.",
                        "source_path": "records.json",
                        "quote": '{"records": []}',
                    }
                ],
                "existing_capabilities": [],
                "inferences": [],
                "unsupported_claims": [],
                "risks": [],
                "open_questions": [],
                "gaps": ["Gap: no authentication layer exists"],
            }
        return json.dumps(result)


class GitHubStubProvider(StubProvider):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if "GitHub activity for acme/widgets" not in user_prompt:
            return super().complete(system_prompt, user_prompt)
        self.prompts.append((system_prompt, user_prompt))
        result = {
            "summary": "Login problems are tracked in GitHub issues.",
            "problem_statement": "Users cannot log in.",
            "goals": ["Restore login"],
            "cited_evidence": [
                {
                    "claim": "Issue 12 reports that users cannot log in.",
                    "source_path": "https://github.com/acme/widgets/issues/12",
                    "quote": "Users can't log in.",
                },
                {
                    "claim": "Issue 999 confirms the export is required.",
                    "source_path": "https://github.com/acme/widgets/issues/999",
                    "quote": "Export is required.",
                },
            ],
            "existing_capabilities": [
                {
                    "claim": "Pull request 45 fixes issue 12.",
                    "source_path": "https://github.com/acme/widgets/pull/45",
                    "quote": "Fixes #12.",
                    "capability": "Repository context and feature fit",
                }
            ],
            "inferences": [],
            "unsupported_claims": [],
            "risks": [],
            "open_questions": [],
            "gaps": [],
        }
        return json.dumps(result)


class SecretEchoProvider(StubProvider):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = super().complete(system_prompt, user_prompt)
        if self.secret in system_prompt + user_prompt:
            result = json.loads(response)
            result["summary"] = self.secret
            return json.dumps(result)
        return response


class ScriptedProvider(CompletionProvider):
    def __init__(self, responses: list[str | ProviderError]) -> None:
        self.responses = responses
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        response = self.responses.pop(0)
        if isinstance(response, ProviderError):
            raise response
        return response


def _valid_completion(summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "problem_statement": "",
            "goals": [],
            "cited_evidence": [],
            "existing_capabilities": [],
            "inferences": [],
            "unsupported_claims": [],
            "risks": [],
            "open_questions": [],
            "gaps": [],
        }
    )


def _config_file(
    tmp_path: Path, project: Path, github_repo: str | None = None
) -> Path:
    config_path = tmp_path / "projects.toml"
    github_line = f'github_repo = "{github_repo}"\n' if github_repo else ""
    config_path.write_text(
        f"""
[provider]
endpoint = "https://provider.invalid/v1/chat/completions"
model = "fixture-model"
api_key_env = "TEST_API_KEY"

[projects.fixture]
name = "Fixture"
path = "{project.as_posix()}"
{github_line}
[assessment]
output_dir = "{(tmp_path / 'reports').as_posix()}"
""",
        encoding="utf-8",
    )
    return config_path


def test_generates_traceable_report_and_keeps_fixture_unchanged(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    original = _tree_bytes(project)
    config_path = _config_file(tmp_path, project)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "https://provider.invalid/v1/chat/completions",
            "https://report-user-secret:report-pass-secret@provider.invalid/"
            "v1/chat/completions?region=west&api_key=query-key-secret"
            "&X-Amz-Signature=query-signature-secret"
            "&clientSecret=camel-case-secret#fragment-secret",
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    provider = StubProvider()

    report_path = run_assessment(
        config=config,
        project_key="fixture",
        feature_request="Export archived records",
        output_dir=config.output_dir,
        provider_factory=lambda _: provider,
    )

    report = report_path.read_text(encoding="utf-8")
    source_sha = hashlib.sha256(
        b"README.md\0# Fixture service\n\nRecords are stored in `records.json`.\n\0"
        b"records.json\0{\"records\": []}\n\0"
    ).hexdigest()
    for heading in (
        "# Feature assessment:",
        "## Request",
        "## Problem statement",
        "## Goals",
        "## Summary",
        "## Repository context and feature fit",
        "## Implementation impact",
        "## Existing capabilities",
        "## Gaps",
        "## Cited evidence",
        "## Inferences",
        "## Unsupported claims",
        "## Risks and open questions",
        "## Traceability",
        "## Metadata",
    ):
        assert heading in report
    assert "- Model: `fixture-model`" in report
    assert f"- Prompt version: `{PROMPT_VERSION}`" in report
    assert f"- Source SHA-256: `{source_sha}`" in report
    assert (
        "- Provider endpoint: "
        "`https://provider.invalid/v1/chat/completions?region=west"
        "&api_key=REDACTED&X-Amz-Signature=REDACTED"
        "&clientSecret=REDACTED`"
    ) in report
    for secret in (
        "report-user-secret",
        "report-pass-secret",
        "query-key-secret",
        "query-signature-secret",
        "camel-case-secret",
        "fragment-secret",
    ):
        assert secret not in report
    assert "The service has a REST API." in report
    assert "This made-up citation must be rejected." in report
    assert "citation could not be verified" in report
    assert "The system lacks X" in _section(report, "Problem statement")
    assert "Enable Y" in _section(report, "Goals")
    assert "Records are stored in records.json" in _section(
        report, "Existing capabilities"
    )
    assert "README.md" in _section(report, "Existing capabilities")
    assert "Gap: no authentication layer exists" in _section(report, "Gaps")
    assert "Gap (inference)" in _section(report, "Gaps")
    # Demoted existing capability must not be rendered as verified
    assert "This existing capability is not in the file" not in _section(
        report, "Existing capabilities"
    )
    assert "This existing capability is not in the file" in _section(
        report, "Unsupported claims"
    )
    assert len(provider.prompts) == 2
    assert provider.prompts[0][1].index("Capability: Repository context") < 100
    assert "Capability: Implementation impact" in provider.prompts[1][1]
    assert _tree_bytes(project) == original
    assert report_path.parent == config.output_dir


def test_recovers_from_invalid_completion_with_one_correction_attempt(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    provider = ScriptedProvider(
        [
            json.dumps({"summary": 42}),
            _valid_completion("Recovered repository assessment."),
            _valid_completion("Implementation impact assessment."),
        ]
    )

    report_path = run_assessment(
        config=config,
        project_key="fixture",
        feature_request="Export archived records",
        output_dir=config.output_dir,
        provider_factory=lambda _: provider,
    )

    assert len(provider.prompts) == 3
    correction_prompt = provider.prompts[1][1]
    assert "Correction required" in correction_prompt
    assert "field 'summary' must be a string" in correction_prompt
    assert "Recovered repository assessment." in report_path.read_text(
        encoding="utf-8"
    )


def test_recovers_from_completion_missing_required_field(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    incomplete = json.loads(_valid_completion("Incomplete repository assessment."))
    del incomplete["risks"]
    provider = ScriptedProvider(
        [
            json.dumps(incomplete),
            _valid_completion("Corrected repository assessment."),
            _valid_completion("Implementation impact assessment."),
        ]
    )

    report_path = run_assessment(
        config=config,
        project_key="fixture",
        feature_request="Export archived records",
        output_dir=config.output_dir,
        provider_factory=lambda _: provider,
    )

    assert len(provider.prompts) == 3
    correction_prompt = provider.prompts[1][1]
    assert "Correction required" in correction_prompt
    assert "field 'risks' must be a list" in correction_prompt
    assert "Corrected repository assessment." in report_path.read_text(
        encoding="utf-8"
    )


def test_repeated_invalid_completion_fails_after_one_retry_without_report(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    provider = ScriptedProvider(["not JSON", "still not JSON"])

    with pytest.raises(
        ProviderError, match="remained invalid after one correction attempt"
    ):
        run_assessment(
            config=config,
            project_key="fixture",
            feature_request="Export archived records",
            output_dir=config.output_dir,
            provider_factory=lambda _: provider,
        )

    assert len(provider.prompts) == 2
    assert "not valid JSON" in provider.prompts[1][1]
    assert not config.output_dir.exists()


@pytest.mark.parametrize(
    "message",
    [
        "Provider returned HTTP 503.",
        "Could not reach the configured provider.",
    ],
)
def test_provider_errors_are_not_retried_or_reported(
    tmp_path: Path, message: str
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    provider = ScriptedProvider([ProviderError(message)])

    with pytest.raises(ProviderError, match=message):
        run_assessment(
            config=config,
            project_key="fixture",
            feature_request="Export archived records",
            output_dir=config.output_dir,
            provider_factory=lambda _: provider,
        )

    assert len(provider.prompts) == 1
    assert not config.output_dir.exists()


def test_secret_manifest_values_never_reach_provider_or_report(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    secret_value = "sensitive-manifest-value-canary"
    (project / "secret.yaml").write_text(
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: fixture-credentials\n"
        "stringData:\n"
        f"  password: {secret_value}\n",
        encoding="utf-8",
    )
    (project / "configmap.yaml").write_text(
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "data:\n"
        "  greeting: hello\n",
        encoding="utf-8",
    )
    config = load_config(_config_file(tmp_path, project))
    provider = SecretEchoProvider(secret_value)

    report_path = run_assessment(
        config=config,
        project_key="fixture",
        feature_request="Review configuration",
        output_dir=config.output_dir,
        provider_factory=lambda _: provider,
    )

    assert all(
        secret_value not in system_prompt + user_prompt
        for system_prompt, user_prompt in provider.prompts
    )
    assert any(
        "kind: ConfigMap" in user_prompt
        for _, user_prompt in provider.prompts
    )
    assert secret_value not in report_path.read_text(encoding="utf-8")


def test_rejects_output_directory_inside_any_configured_project(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))

    try:
        run_assessment(
            config=config,
            project_key="fixture",
            feature_request="Export records",
            output_dir=project / "reports",
            provider_factory=lambda _: StubProvider(),
        )
    except ValueError as exc:
        assert "must not be inside reference repository" in str(exc)
    else:
        raise AssertionError("Expected output path to be rejected")


def test_rejects_symlink_report_target_into_configured_project(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    output_dir = tmp_path / "external-reports"
    output_dir.mkdir()
    report_path = output_dir / _report_filename("fixture", "Export records")
    original_readme = (project / "README.md").read_bytes()
    report_path.symlink_to(project / "README.md")

    with pytest.raises(ValueError, match="Report target must not be a symlink"):
        run_assessment(
            config=config,
            project_key="fixture",
            feature_request="Export records",
            output_dir=output_dir,
            provider_factory=lambda _: StubProvider(),
        )

    assert (project / "README.md").read_bytes() == original_readme


def test_atomic_report_write_does_not_modify_a_hard_link_in_the_project(
    tmp_path: Path,
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    output_dir = tmp_path / "external-reports"
    output_dir.mkdir()
    repository_file = project / "README.md"
    report_path = output_dir / "report.md"
    original = repository_file.read_bytes()
    os.link(repository_file, report_path)

    _write_report(report_path, "# Safe replacement\n", config)

    assert repository_file.read_bytes() == original
    assert report_path.read_text(encoding="utf-8") == "# Safe replacement\n"
    assert not os.path.samefile(repository_file, report_path)


def test_report_filenames_distinguish_project_keys_with_same_slug() -> None:
    slash_key = _report_filename("foo/bar", "Export records")
    dash_key = _report_filename("foo-bar", "Export records")

    assert slash_key != dash_key
    assert slash_key.startswith("foo-bar-")
    assert dash_key.startswith("foo-bar-")


def test_report_write_rejects_symlink_created_during_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    output_dir = tmp_path / "external-reports"
    report_path = output_dir / _report_filename("fixture", "Export records")
    original_readme = (project / "README.md").read_bytes()
    original_mkstemp = tempfile.mkstemp

    def symlink_during_staging(*args, **kwargs):
        descriptor, path = original_mkstemp(*args, **kwargs)
        report_path.symlink_to(project / "README.md")
        return descriptor, path

    monkeypatch.setattr("projectmesh.cli.tempfile.mkstemp", symlink_during_staging)

    with pytest.raises(ValueError, match="Report target must not be a symlink"):
        run_assessment(
            config=config,
            project_key="fixture",
            feature_request="Export records",
            output_dir=output_dir,
            provider_factory=lambda _: StubProvider(),
        )

    assert (project / "README.md").read_bytes() == original_readme
    assert report_path.is_symlink()


def test_github_evidence_is_cited_with_item_url(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-tok")
    _stub_github(monkeypatch, _graphql_response())
    project = _make_project(tmp_path)
    original = _tree_bytes(project)
    config = load_config(_config_file(tmp_path, project, github_repo="acme/widgets"))
    provider = GitHubStubProvider()

    report_path = run_assessment(
        config=config,
        project_key="fixture",
        feature_request="Export archived records",
        output_dir=config.output_dir,
        provider_factory=lambda _: provider,
    )

    report = report_path.read_text(encoding="utf-8")
    issue_url = "https://github.com/acme/widgets/issues/12"
    pr_url = "https://github.com/acme/widgets/pull/45"
    discussion_url = "https://github.com/acme/widgets/discussions/3"
    assert f"Source: `{issue_url}`" in _section(report, "Cited evidence")
    assert pr_url in _section(report, "Existing capabilities")
    assert "/issues/999" not in _section(report, "Cited evidence")
    assert "/issues/999" in _section(report, "Unsupported claims")
    traceability = _section(report, "Traceability")
    for url in (issue_url, pr_url, discussion_url):
        assert url in traceability
    assert "GitHub evidence SHA-256" in traceability
    assert "GitHub repository: `acme/widgets`" in _section(report, "Metadata")
    assert f"- Prompt version: `{PROMPT_VERSION}+github-v1`" in report
    assert "GitHub activity" not in provider.prompts[1][1]
    for system_prompt, user_prompt in provider.prompts:
        assert "test-tok" not in system_prompt + user_prompt
    assert "test-tok" not in report
    assert _tree_bytes(project) == original


def test_without_github_repo_makes_no_github_calls(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _stub_github(monkeypatch, AssertionError("GitHub must not be contacted"))
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project))
    provider = StubProvider()

    report_path = run_assessment(
        config=config,
        project_key="fixture",
        feature_request="Export archived records",
        output_dir=config.output_dir,
        provider_factory=lambda _: provider,
    )

    report = report_path.read_text(encoding="utf-8")
    assert all("GitHub activity" not in user for _, user in provider.prompts)
    assert not any("GitHub" in line for line in report.splitlines())
    assert f"- Prompt version: `{PROMPT_VERSION}`" in report


def test_github_failure_aborts_before_provider_and_writes_no_report(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-tok")
    _stub_github(
        monkeypatch,
        HTTPError("https://api.github.com/graphql", 404, "Not Found", {}, None),
    )
    project = _make_project(tmp_path)
    config = load_config(_config_file(tmp_path, project, github_repo="acme/widgets"))
    provider = StubProvider()

    with pytest.raises(GitHubError):
        run_assessment(
            config=config,
            project_key="fixture",
            feature_request="Export archived records",
            output_dir=config.output_dir,
            provider_factory=lambda _: provider,
        )

    assert provider.prompts == []
    assert not config.output_dir.exists()
