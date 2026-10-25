from __future__ import annotations

from pathlib import Path

import pytest

from projectmesh.config import ConfigError, load_config


def _write_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_multiple_project_entries_relative_to_config(tmp_path: Path) -> None:
    (tmp_path / "service").mkdir()
    (tmp_path / "library").mkdir()
    config_file = _write_config(
        tmp_path / "projects.toml",
        """
[provider]
endpoint = "https://example.test/v1/chat/completions"
model = "test-model"
api_key_env = "TEST_API_KEY"

[projects.service]
name = "Service"
path = "service"

[projects.library]
name = "Library"
path = "library"

[assessment]
output_dir = "reports"
""",
    )

    config = load_config(config_file)

    assert set(config.projects) == {"service", "library"}
    assert config.projects["service"].path == (tmp_path / "service").resolve()
    assert config.projects["library"].name == "Library"
    assert config.output_dir == (tmp_path / "reports").resolve()
    assert config.provider.timeout_seconds == 60
    assert config.provider.type == "openai-compatible"


def test_loads_claude_cli_provider_without_endpoint_or_api_key(tmp_path: Path) -> None:
    (tmp_path / "service").mkdir()
    config_file = _write_config(
        tmp_path / "projects.toml",
        """
[provider]
type = "claude-cli"
model = "sonnet"

[projects.x]
name = "X"
path = "service"

[assessment]
output_dir = "reports"
""",
    )

    config = load_config(config_file)

    assert config.provider.type == "claude-cli"
    assert config.provider.endpoint == ""
    assert config.provider.api_key_env == ""
    assert config.provider.timeout_seconds == 300
    assert config.provider.model == "sonnet"


def test_loads_agent_model_override_inheriting_provider_settings(
    tmp_path: Path,
) -> None:
    (tmp_path / "service").mkdir()
    config_file = _write_config(
        tmp_path / "projects.toml",
        """
[provider]
type = "claude-cli"
model = "sonnet"
timeout_seconds = 300

[agents.code_analysis]
model = "opus"

[projects.x]
name = "X"
path = "service"
""",
    )

    config = load_config(config_file)

    code = config.provider_for("code_analysis")
    assert code.type == "claude-cli"
    assert code.model == "opus"
    assert code.timeout_seconds == 300
    assert config.provider_for("spec_analysis") == config.provider


def test_agent_type_switch_does_not_inherit(tmp_path: Path) -> None:
    (tmp_path / "service").mkdir()
    config_file = _write_config(
        tmp_path / "projects.toml",
        """
[provider]
type = "claude-cli"
model = "sonnet"
timeout_seconds = 300

[agents.spec_analysis]
type = "openai-compatible"
endpoint = "https://spec.test/v1/chat/completions"
model = "spec-model"
api_key_env = "SPEC_API_KEY"

[projects.x]
name = "X"
path = "service"
""",
    )

    config = load_config(config_file)

    spec = config.provider_for("spec_analysis")
    assert spec.type == "openai-compatible"
    assert spec.timeout_seconds == 60
    assert spec.endpoint == "https://spec.test/v1/chat/completions"


def test_config_without_agents_is_unchanged(tmp_path: Path) -> None:
    (tmp_path / "service").mkdir()
    config_file = _write_config(
        tmp_path / "projects.toml",
        """
[provider]
endpoint = "https://example.test/v1/chat/completions"
model = "test-model"
api_key_env = "TEST_API_KEY"

[projects.x]
name = "X"
path = "service"
""",
    )

    config = load_config(config_file)

    assert config.analysis_providers == {}


def test_loads_optional_github_repo(tmp_path: Path) -> None:
    (tmp_path / "service").mkdir()
    (tmp_path / "library").mkdir()
    config_file = _write_config(
        tmp_path / "projects.toml",
        """
[provider]
endpoint = "https://example.test/v1/chat/completions"
model = "test-model"
api_key_env = "TEST_API_KEY"

[projects.service]
name = "Service"
path = "service"
github_repo = "acme/widgets"

[projects.library]
name = "Library"
path = "library"
""",
    )

    config = load_config(config_file)

    assert config.projects["service"].github_repo == "acme/widgets"
    assert config.projects["library"].github_repo is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[provider\n", "Invalid TOML"),
        (
            (
                '[provider]\ntype = "bogus"\nmodel = "m"\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "provider.type",
        ),
        (
            (
                '[provider]\ntype = "claude-cli"\nmodel = "sonnet"\n'
                'endpoint = "https://x.test"\n[projects.p]\nname = "P"\npath = "."\n'
            ),
            "Unknown setting",
        ),
        (
            (
                '[provider]\ntype = "claude-cli"\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "provider.model",
        ),
        (
            (
                '[provider]\nmodel = "m"\napi_key_env = "KEY"\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "provider.endpoint",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "missing"\n'
            ),
            "path does not exist",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\nextra = true\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "Unknown setting",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "."\n'
                'github_repo = "acme"\n'
            ),
            "github_repo",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "."\n'
                'github_repo = "acme-/repo"\n'
            ),
            "github_repo",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "."\n'
                'github_repo = "ac--me/repo"\n'
            ),
            "github_repo",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "."\n'
                'github_repo = "a/b/c"\n'
            ),
            "github_repo",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "."\n'
                'github_repo = "acme/.."\n'
            ),
            "github_repo",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "."\n'
                'github_repo = 5\n'
            ),
            "github_repo",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[agents.security]\nmodel = "m"\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "Unknown setting.*in 'agents'",
        ),
        (
            (
                'agents = 5\n[provider]\nendpoint = "https://example.test"\n'
                'model = "m"\napi_key_env = "KEY"\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "'agents' must be a TOML table",
        ),
        (
            (
                '[provider]\ntype = "claude-cli"\nmodel = "sonnet"\n'
                '[agents.spec_analysis]\ntype = "openai-compatible"\nmodel = "m"\n'
                'api_key_env = "KEY"\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "'agents.spec_analysis.endpoint' must be a non-empty string",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[agents.code_analysis]\nbogus = 1\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "Unknown setting.*in 'agents.code_analysis'",
        ),
        (
            (
                '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
                'api_key_env = "KEY"\n[agents.code_analysis]\ntimeout_seconds = 0\n'
                '[projects.p]\nname = "P"\npath = "."\n'
            ),
            "'agents.code_analysis.timeout_seconds' must be a positive integer",
        ),
    ],
)
def test_invalid_configuration_has_clear_error(
    tmp_path: Path, content: str, message: str
) -> None:
    config_file = _write_config(tmp_path / "bad.toml", content)

    with pytest.raises(ConfigError, match=message):
        load_config(config_file)
