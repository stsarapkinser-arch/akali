"""Tests for akali.paths — centralized path constants."""
from __future__ import annotations

from pathlib import Path

from akali import paths


def test_path_constants_are_path_instances():
    for name in (
        "PACKAGE_DIR", "PROJECT_ROOT", "COMMANDS_TXT", "AUTO_COMMANDS_JSON",
        "QUERY_CACHE_JSON", "EMBED_CACHE_PKL", "INDEXER_SCRIPT",
        "DEFAULT_VOSK_MODEL_DIR", "VOICES_DIR", "XTTS_REFERENCE_WAV",
        "UI_RESOURCES_DIR", "APP_STYLESHEET", "APP_ICON",
    ):
        val = getattr(paths, name)
        assert isinstance(val, Path), f"{name} should be Path, got {type(val)}"


def test_commands_txt_name():
    assert paths.COMMANDS_TXT.name == "commands.txt"


def test_package_dir_contains_paths_module():
    assert (paths.PACKAGE_DIR / "paths.py").exists()


def test_project_root_is_parent_of_package_dir():
    assert paths.PACKAGE_DIR.parent == paths.PROJECT_ROOT


def test_indexer_script_exists():
    assert paths.INDEXER_SCRIPT.exists()
    assert paths.INDEXER_SCRIPT.name == "system_indexer.py"


def test_ui_resources_paths_under_package():
    assert paths.UI_RESOURCES_DIR.is_relative_to(paths.PACKAGE_DIR)
    assert paths.APP_STYLESHEET.name == "app.qss"


def test_as_str_returns_string():
    result = paths.as_str(paths.COMMANDS_TXT)
    assert isinstance(result, str)
    assert result.endswith("commands.txt")
