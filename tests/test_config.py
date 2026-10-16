"""Unit tests for processpilot.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from processpilot.config import (
    default_cache_dir,
    find_project_root,
    load_preset_yaml,
    resolve_preset_path,
    resolve_source_path,
)


def test_find_project_root_locates_pyproject_from_a_subdirectory(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    nested = tmp_path / "notebooks" / "deeper"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path.resolve()


def test_find_project_root_raises_when_no_pyproject(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="pyproject.toml"):
        find_project_root(tmp_path / "nowhere")


def test_resolve_preset_path_accepts_a_bare_name() -> None:
    path = resolve_preset_path("bpi2019")
    assert path.name == "bpi2019.yaml"
    assert path.parent.name == "configs"


def test_load_preset_yaml_returns_mapping_block() -> None:
    preset = load_preset_yaml("bpi2019")
    assert preset["source"]["path_env"] == "PP_BPI2019_PATH"
    assert "case_id" in preset["mapping"]


def test_resolve_source_path_names_the_missing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PP_TEST_SOURCE", raising=False)
    with pytest.raises(OSError, match="PP_TEST_SOURCE"):
        resolve_source_path({"source": {"path_env": "PP_TEST_SOURCE"}})


def test_resolve_source_path_rejects_nonexistent_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PP_TEST_SOURCE", str(tmp_path / "missing.xes"))
    with pytest.raises(FileNotFoundError, match="PP_TEST_SOURCE"):
        resolve_source_path({"source": {"path_env": "PP_TEST_SOURCE"}})


def test_resolve_source_path_returns_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "log.xes"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("PP_TEST_SOURCE", str(log))
    assert resolve_source_path({"source": {"path_env": "PP_TEST_SOURCE"}}) == log


def test_default_cache_dir_honours_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PP_CACHE_DIR", str(tmp_path))
    assert default_cache_dir() == tmp_path
