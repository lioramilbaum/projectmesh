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


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[provider\n", "Invalid TOML"),
        (
            '[provider]\nmodel = "m"\napi_key_env = "KEY"\n'
            '[projects.p]\nname = "P"\npath = "."\n',
            "provider.endpoint",
        ),
        (
            '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
            'api_key_env = "KEY"\n[projects.p]\nname = "P"\npath = "missing"\n',
            "path does not exist",
        ),
        (
            '[provider]\nendpoint = "https://example.test"\nmodel = "m"\n'
            'api_key_env = "KEY"\nextra = true\n'
            '[projects.p]\nname = "P"\npath = "."\n',
            "Unknown setting",
        ),
    ],
)
def test_invalid_configuration_has_clear_error(
    tmp_path: Path, content: str, message: str
) -> None:
    config_file = _write_config(tmp_path / "bad.toml", content)

    with pytest.raises(ConfigError, match=message):
        load_config(config_file)
