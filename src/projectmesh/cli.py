"""Command-line interface for projectmesh."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from projectmesh.config import ConfigError, load_config
from projectmesh.models import AppConfig, ProviderConfig
from projectmesh.orchestrator import create_assessment
from projectmesh.provider import CompletionProvider, ProviderError, create_provider
from projectmesh.render import render_markdown
from projectmesh.repository import capture_repository


ProviderFactory = Callable[[ProviderConfig], CompletionProvider]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="projectmesh",
        description="Assess a feature request against a configured local repository.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    assess = subparsers.add_parser("assess", help="generate a feature assessment")
    assess.add_argument("--config", required=True, help="path to the TOML config")
    assess.add_argument("--project", required=True, help="project key from [projects]")
    assess.add_argument(
        "--feature-request",
        required=True,
        help="feature request text to assess",
    )
    assess.add_argument(
        "--output-dir",
        help="report output directory (overrides configured output_dir)",
    )
    return parser


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_output_dir(output_dir: Path, config: AppConfig) -> None:
    resolved_output = output_dir.resolve()
    for project in config.projects.values():
        if _is_within(resolved_output, project.path.resolve()):
            raise ValueError(
                f"Output directory must not be inside reference repository "
                f"'{project.key}': {resolved_output}"
            )


def _validate_report_path(report_path: Path, config: AppConfig) -> None:
    if report_path.is_symlink():
        raise ValueError(f"Report target must not be a symlink: {report_path}")
    try:
        report_stat = report_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        report_stat = None
    if report_stat is not None and not stat.S_ISREG(report_stat.st_mode):
        raise ValueError(f"Report target is not a regular file: {report_path}")
    resolved_report = report_path.resolve()
    for project in config.projects.values():
        if _is_within(resolved_report, project.path.resolve()):
            raise ValueError(
                f"Report target must not be inside reference repository "
                f"'{project.key}': {resolved_report}"
            )


def _write_report(report_path: Path, report: str, config: AppConfig) -> None:
    _validate_report_path(report_path, config)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{report_path.name}.",
        suffix=".tmp",
        dir=report_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as report_file:
            descriptor = -1
            report_file.write(report)
        _validate_report_path(report_path, config)
        os.replace(temporary_path, report_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _report_filename(project_key: str, request: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", request.lower()).strip("-")[:48].strip("-")
    slug = slug or "feature"
    request_sha = hashlib.sha256(request.encode("utf-8")).hexdigest()[:8]
    safe_key = re.sub(r"[^a-zA-Z0-9._-]+", "-", project_key).strip("-")
    key_sha = hashlib.sha256(project_key.encode("utf-8")).hexdigest()
    return f"{safe_key}-{key_sha}-{slug}-{request_sha}.md"


def run_assessment(
    config: AppConfig,
    project_key: str,
    feature_request: str,
    output_dir: Path,
    provider_factory: ProviderFactory = create_provider,
) -> Path:
    if project_key not in config.projects:
        available = ", ".join(sorted(config.projects))
        raise ValueError(
            f"Unknown project '{project_key}'. Available projects: {available}"
        )
    if not feature_request.strip():
        raise ValueError("Feature request must not be empty.")
    _validate_output_dir(output_dir, config)
    report_path = output_dir / _report_filename(project_key, feature_request.strip())
    _validate_report_path(report_path, config)
    project = config.projects[project_key]
    snapshot = capture_repository(project.path)
    provider = provider_factory(config.provider)
    assessment = create_assessment(
        project=project,
        feature_request=feature_request.strip(),
        snapshot=snapshot,
        provider_config=config.provider,
        provider=provider,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_report(report_path, render_markdown(assessment), config)
    return report_path


def main(
    argv: Sequence[str] | None = None,
    provider_factory: ProviderFactory = create_provider,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "assess":
            if args.output_dir:
                output_dir = Path(args.output_dir).expanduser()
                if not output_dir.is_absolute():
                    output_dir = Path.cwd() / output_dir
                output_dir = output_dir.resolve()
            else:
                output_dir = config.output_dir
            report_path = run_assessment(
                config=config,
                project_key=args.project,
                feature_request=args.feature_request,
                output_dir=output_dir,
                provider_factory=provider_factory,
            )
            print(report_path)
            return 0
    except (ConfigError, ProviderError, OSError, ValueError) as exc:
        print(f"projectmesh: error: {exc}", file=sys.stderr)
        return 2
    return 0
