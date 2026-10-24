from __future__ import annotations

import json
from pathlib import Path

RENOVATE_CONFIG = Path(__file__).resolve().parent.parent / "renovate.json"


def _load_renovate_config() -> dict:
    return json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))


def test_renovate_config_is_valid_json_with_schema() -> None:
    config = _load_renovate_config()

    assert config["$schema"] == "https://docs.renovatebot.com/renovate-schema.json"


def test_renovate_groups_all_updates_into_single_pr() -> None:
    extends = _load_renovate_config()["extends"]

    assert "group:all" in extends
    assert "config:recommended" in extends
    assert extends.index("group:all") > extends.index("config:recommended")


def test_renovate_config_does_not_add_major_split_override() -> None:
    """Non-splitting is guaranteed by group:all (verified in test 2); this only guards against explicit counter-overrides."""
    config = _load_renovate_config()

    assert "group:allNonMajor" not in config["extends"]
    assert config.get("separateMajorMinor") is not True
