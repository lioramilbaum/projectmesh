from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_assessment import StubProvider, _config_file, _make_project

from projectmesh.cli import main


def test_cli_end_to_end_with_injected_stub(tmp_path: Path, capsys) -> None:
    project = _make_project(tmp_path)
    config_path = _config_file(tmp_path, project)
    provider = StubProvider()

    exit_code = main(
        [
            "assess",
            "--config",
            str(config_path),
            "--project",
            "fixture",
            "--feature-request",
            "Export archived records",
            "--output-dir",
            str(tmp_path / "cli-reports"),
        ],
        provider_factory=lambda _: provider,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    report_path = Path(captured.out.strip())
    assert report_path.exists()
    assert "## Repository context and feature fit" in report_path.read_text(
        encoding="utf-8"
    )
    assert captured.err == ""


def test_two_configured_projects_are_assessed_without_code_changes(
    tmp_path: Path, capsys
) -> None:
    alpha_dir = tmp_path / "alpha"
    alpha_dir.mkdir()
    (alpha_dir / "README.md").write_text("Alpha project README.", encoding="utf-8")
    beta_dir = tmp_path / "beta"
    beta_dir.mkdir()
    (beta_dir / "README.md").write_text("Beta project README.", encoding="utf-8")
    config_path = tmp_path / "projects.toml"
    config_path.write_text(
        f"""
[provider]
endpoint = "https://provider.invalid/v1/chat/completions"
model = "fixture-model"
api_key_env = "TEST_API_KEY"

[projects.alpha]
name = "Alpha Project"
path = "{alpha_dir.as_posix()}"

[projects.beta]
name = "Beta Project"
path = "{beta_dir.as_posix()}"

[assessment]
output_dir = "{(tmp_path / 'out').as_posix()}"
""",
        encoding="utf-8",
    )
    reports = {}

    for key, name in (("alpha", "Alpha Project"), ("beta", "Beta Project")):
        provider = StubProvider()
        exit_code = main(
            [
                "assess",
                "--config",
                str(config_path),
                "--project",
                key,
                "--feature-request",
                "Add search",
            ],
            provider_factory=lambda _: provider,
        )
        captured = capsys.readouterr()
        assert exit_code == 0
        assert captured.err == ""
        report = Path(captured.out.strip()).read_text(encoding="utf-8")
        assert f"- Project key: `{key}`" in report
        assert f"- Project name: {name}" in report
        reports[key] = report

    def source_sha(report: str) -> str:
        marker = "- Source SHA-256: `"
        start = report.rindex(marker) + len(marker)
        return report[start : report.index("`", start)]

    assert source_sha(reports["alpha"]) != source_sha(reports["beta"])


def test_cli_unknown_project_is_a_clear_error(tmp_path: Path, capsys) -> None:
    project = _make_project(tmp_path)
    config_path = _config_file(tmp_path, project)

    exit_code = main(
        [
            "assess",
            "--config",
            str(config_path),
            "--project",
            "missing",
            "--feature-request",
            "Export records",
        ],
        provider_factory=lambda _: StubProvider(),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Unknown project 'missing'" in captured.err
    assert captured.out == ""


def test_cli_end_to_end_with_claude_cli_provider(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    project = _make_project(tmp_path)
    config_path = tmp_path / "projects.toml"
    config_path.write_text(
        f"""
[provider]
type = "claude-cli"
model = "sonnet"

[projects.fixture]
name = "Fixture"
path = "{project.as_posix()}"

[assessment]
output_dir = "{(tmp_path / 'reports').as_posix()}"
""",
        encoding="utf-8",
    )
    stub = StubProvider()

    def fake_run(argv, **kwargs):
        system_prompt = argv[argv.index("--system-prompt") + 1]
        structured = json.loads(stub.complete(system_prompt, kwargs["input"]))
        stdout = json.dumps({"is_error": False, "structured_output": structured})
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr("projectmesh.provider.subprocess.run", fake_run)

    exit_code = main(
        [
            "assess",
            "--config",
            str(config_path),
            "--project",
            "fixture",
            "--feature-request",
            "Add search",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    report = Path(captured.out.strip()).read_text(encoding="utf-8")
    assert "claude-cli (local Claude Code session)" in report
    assert "The system lacks X" in report


def test_cli_github_error_is_clear_nonzero(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    project = _make_project(tmp_path)
    config_path = _config_file(tmp_path, project, github_repo="acme/widgets")

    exit_code = main(
        [
            "assess",
            "--config",
            str(config_path),
            "--project",
            "fixture",
            "--feature-request",
            "Export archived records",
            "--output-dir",
            str(tmp_path / "cli-reports"),
        ],
        provider_factory=lambda _: StubProvider(),
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "GITHUB_TOKEN" in captured.err
    assert captured.out == ""
