"""TOML configuration loading and validation."""

from __future__ import annotations

import tomllib
from pathlib import Path
from urllib.parse import urlparse

from projectmesh.models import AppConfig, ProjectConfig, ProviderConfig


class ConfigError(ValueError):
    """Raised when configuration is invalid or incomplete."""


def _table(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"'{name}' must be a TOML table.")
    return value


def _reject_unknown_keys(
    table: dict[str, object], allowed: set[str], location: str
) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        names = ", ".join(f"'{key}'" for key in unknown)
        raise ConfigError(f"Unknown setting(s) in '{location}': {names}.")


def _required_string(table: dict[str, object], key: str, location: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{location}.{key}' must be a non-empty string.")
    return value.strip()


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def load_config(path: str | Path) -> AppConfig:
    """Load a TOML configuration file and validate its project checkouts."""
    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {config_path}") from exc
    except PermissionError as exc:
        raise ConfigError(f"Cannot read configuration file: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    _reject_unknown_keys(raw, {"provider", "projects", "assessment"}, "root")
    provider_table = _table(raw.get("provider"), "provider")
    _reject_unknown_keys(
        provider_table,
        {"endpoint", "model", "api_key_env", "timeout_seconds"},
        "provider",
    )
    endpoint = _required_string(provider_table, "endpoint", "provider")
    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
        raise ConfigError("'provider.endpoint' must be an absolute HTTP(S) URL.")
    model = _required_string(provider_table, "model", "provider")
    api_key_env = _required_string(provider_table, "api_key_env", "provider")
    timeout_seconds = provider_table.get("timeout_seconds", 60)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ConfigError("'provider.timeout_seconds' must be a positive integer.")
    provider = ProviderConfig(
        endpoint=endpoint,
        model=model,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
    )

    projects_table = _table(raw.get("projects"), "projects")
    if not projects_table:
        raise ConfigError("'projects' must contain at least one project.")
    projects: dict[str, ProjectConfig] = {}
    for key, raw_project in projects_table.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("Project keys under 'projects' must be non-empty strings.")
        project_table = _table(raw_project, f"projects.{key}")
        _reject_unknown_keys(project_table, {"name", "path"}, f"projects.{key}")
        name = _required_string(project_table, "name", f"projects.{key}")
        configured_path = _required_string(project_table, "path", f"projects.{key}")
        project_path = _resolve_path(configured_path, config_path.parent)
        if not project_path.exists():
            raise ConfigError(
                f"Project '{key}' path does not exist: {project_path}"
            )
        if not project_path.is_dir():
            raise ConfigError(f"Project '{key}' path is not a directory: {project_path}")
        projects[key] = ProjectConfig(key=key, name=name, path=project_path)

    assessment_table = raw.get("assessment", {})
    assessment = _table(assessment_table, "assessment")
    _reject_unknown_keys(assessment, {"output_dir"}, "assessment")
    output_value = assessment.get("output_dir", "assessments")
    if not isinstance(output_value, str) or not output_value.strip():
        raise ConfigError("'assessment.output_dir' must be a non-empty string.")
    output_dir = _resolve_path(output_value, config_path.parent)
    return AppConfig(projects=projects, provider=provider, output_dir=output_dir)
