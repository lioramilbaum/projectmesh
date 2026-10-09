from __future__ import annotations

from pathlib import Path

from projectmesh.cli import main
from projectmesh.provider import CompletionProvider
from test_assessment import StubProvider, _config_file, _make_project


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
