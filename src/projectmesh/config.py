"""TOML configuration loading and validation."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from projectmesh.models import (
    ANALYSIS_CAPABILITIES,
    AppConfig,
    ProjectConfig,
    ProviderConfig,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_CLAUDE_CLI,
)


class ConfigError(ValueError):
    """Raised when configuration is invalid or incomplete."""


_GITHUB_REPO = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/[A-Za-z0-9._-]{1,100}"
)


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


def _github_repo(table: dict[str, object], key: str) -> str | None:
    value = table.get("github_repo")
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or _GITHUB_REPO.fullmatch(value) is None
        or value.split("/", 1)[1] in {".", ".."}
    ):
        raise ConfigError(
            f"'projects.{key}.github_repo' must be a string in 'owner/repo' form."
        )
    return value


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _provider_config(table: dict[str, object], location: str) -> ProviderConfig:
    provider_type = table.get("type", PROVIDER_OPENAI_COMPATIBLE)
    if not isinstance(provider_type, str) or provider_type not in {
        PROVIDER_OPENAI_COMPATIBLE,
        PROVIDER_CLAUDE_CLI,
    }:
        raise ConfigError(
            f"'{location}.type' must be one of: 'openai-compatible', 'claude-cli'."
        )
    if provider_type == PROVIDER_CLAUDE_CLI:
        _reject_unknown_keys(
            table,
            {"type", "model", "timeout_seconds"},
            location,
        )
        endpoint = ""
        api_key_env = ""
        model = _required_string(table, "model", location)
        default_timeout = 300
    else:
        _reject_unknown_keys(
            table,
            {"type", "endpoint", "model", "api_key_env", "timeout_seconds"},
            location,
        )
        endpoint = _required_string(table, "endpoint", location)
        parsed_endpoint = urlparse(endpoint)
        if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
            raise ConfigError(f"'{location}.endpoint' must be an absolute HTTP(S) URL.")
        model = _required_string(table, "model", location)
        api_key_env = _required_string(table, "api_key_env", location)
        default_timeout = 60
    timeout_seconds = table.get("timeout_seconds", default_timeout)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ConfigError(f"'{location}.timeout_seconds' must be a positive integer.")
    return ProviderConfig(
        endpoint=endpoint,
        model=model,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
        type=provider_type,
    )


def _agent_providers(
    raw_agents: object,
    provider_table: dict[str, object],
    default: ProviderConfig,
) -> dict[str, ProviderConfig]:
    """Resolve [agents.<capability>] overrides against the global provider table.

    An override with the same type as the default inherits its unset settings;
    switching type starts from the override alone.
    """
    if raw_agents is None:
        return {}
    agents = _table(raw_agents, "agents")
    _reject_unknown_keys(agents, set(ANALYSIS_CAPABILITIES), "agents")
    providers: dict[str, ProviderConfig] = {}
    for name, raw in agents.items():
        override = _table(raw, f"agents.{name}")
        if override.get("type", default.type) == default.type:
            merged = {**provider_table, **override}
        else:
            merged = dict(override)
        providers[name] = _provider_config(merged, f"agents.{name}")
    return providers


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

    _reject_unknown_keys(raw, {"provider", "projects", "assessment", "agents"}, "root")
    provider_table = _table(raw.get("provider"), "provider")
    provider = _provider_config(provider_table, "provider")
    analysis_providers = _agent_providers(raw.get("agents"), provider_table, provider)

    projects_table = _table(raw.get("projects"), "projects")
    if not projects_table:
        raise ConfigError("'projects' must contain at least one project.")
    projects: dict[str, ProjectConfig] = {}
    for key, raw_project in projects_table.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError("Project keys under 'projects' must be non-empty strings.")
        project_table = _table(raw_project, f"projects.{key}")
        _reject_unknown_keys(
            project_table, {"name", "path", "github_repo"}, f"projects.{key}"
        )
        name = _required_string(project_table, "name", f"projects.{key}")
        configured_path = _required_string(project_table, "path", f"projects.{key}")
        github_repo = _github_repo(project_table, key)
        project_path = _resolve_path(configured_path, config_path.parent)
        if not project_path.exists():
            raise ConfigError(
                f"Project '{key}' path does not exist: {project_path}"
            )
        if not project_path.is_dir():
            raise ConfigError(f"Project '{key}' path is not a directory: {project_path}")
        projects[key] = ProjectConfig(
            key=key, name=name, path=project_path, github_repo=github_repo
        )

    assessment_table = raw.get("assessment", {})
    assessment = _table(assessment_table, "assessment")
    _reject_unknown_keys(assessment, {"output_dir"}, "assessment")
    output_value = assessment.get("output_dir", "assessments")
    if not isinstance(output_value, str) or not output_value.strip():
        raise ConfigError("'assessment.output_dir' must be a non-empty string.")
    output_dir = _resolve_path(output_value, config_path.parent)
    return AppConfig(
        projects=projects,
        provider=provider,
        output_dir=output_dir,
        analysis_providers=analysis_providers,
    )
