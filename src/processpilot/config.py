"""Project configuration: ``.env`` loading, env-var path resolution, presets.

WHY one module for this: machine-specific paths (where the BPI 2019 XES lives)
must never be committed, and dataset knowledge must never be hardcoded in
``src/``. Presets therefore name an *environment variable* (``path_env``) rather
than a path, and this module is the single place that turns that indirection
into a real ``Path`` -- with an error that says exactly which variable is
missing, instead of a ``KeyError`` deep inside a notebook.

Nothing here reads data; it only locates it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_PROJECT_MARKER = "pyproject.toml"
_CONFIGS_DIRNAME = "configs"
_CACHE_DIR_ENV = "PP_CACHE_DIR"
_RUNS_DIR_ENV = "PP_RUNS_DIR"
_APP_DIR_ENV = "PP_APP_DIR"


def find_project_root(start: Path | str | None = None) -> Path:
    """Return the nearest ancestor of ``start`` that contains ``pyproject.toml``.

    WHY search upward instead of using ``Path(__file__).parents[n]``: the
    latter silently points somewhere wrong as soon as the package is installed
    non-editably, and notebooks run with ``notebooks/`` as their working
    directory. Searching from the cwd first, then from this file, covers both.
    """
    starts = [Path(start)] if start is not None else [Path.cwd(), Path(__file__).resolve()]
    for origin in starts:
        origin = origin.resolve()
        for candidate in (origin, *origin.parents):
            if (candidate / _PROJECT_MARKER).is_file():
                return candidate
    raise FileNotFoundError(
        f"could not find {_PROJECT_MARKER} above {', '.join(str(s) for s in starts)}"
    )


def load_env(dotenv_path: Path | str | None = None) -> bool:
    """Load ``.env`` from the project root without overriding real env vars.

    ``override=False`` so a variable set in the shell (e.g. in CI) always wins
    over the file -- the file is a convenience, not the source of truth.
    Returns whether a file was found and loaded.
    """
    path = Path(dotenv_path) if dotenv_path is not None else find_project_root() / ".env"
    return load_dotenv(path, override=False)


def default_cache_dir() -> Path:
    """Where parsed-source caches go: ``$PP_CACHE_DIR`` or ``<root>/data/processed``."""
    override = os.environ.get(_CACHE_DIR_ENV)
    if override:
        return Path(override)
    return find_project_root() / "data" / "processed"


def default_runs_dir() -> Path:
    """Where mining runs go: ``$PP_RUNS_DIR`` or ``<root>/outputs/runs``."""
    override = os.environ.get(_RUNS_DIR_ENV)
    return Path(override) if override else find_project_root() / "outputs" / "runs"


def default_app_dir() -> Path:
    """The API's own state (SQLite index, uploads): ``$PP_APP_DIR`` or ``<root>/outputs/app``."""
    override = os.environ.get(_APP_DIR_ENV)
    return Path(override) if override else find_project_root() / "outputs" / "app"


def resolve_preset_path(name_or_path: Path | str) -> Path:
    """Accept either a preset name (``"bpi2019"``) or a path to a YAML file."""
    as_path = Path(name_or_path)
    if as_path.suffix.lower() in (".yaml", ".yml"):
        resolved = as_path
    else:
        resolved = find_project_root() / _CONFIGS_DIRNAME / f"{name_or_path}.yaml"
    if not resolved.is_file():
        raise FileNotFoundError(f"preset not found: {resolved}")
    return resolved


def load_preset_yaml(name_or_path: Path | str, _chain: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Parse a dataset preset YAML into a dict. The only YAML reader for presets.

    ``extends: <preset>`` inherits every top-level block of another preset;
    a block the child names replaces the parent's block whole (no deep merge).
    WHY: the committed BPI sample must use exactly the full dataset's rules,
    and a copied rule list would silently drift from the original.
    """
    path = resolve_preset_path(name_or_path).resolve()
    if path in _chain:
        raise ValueError(f"preset 'extends' cycle: {' -> '.join(p.name for p in (*_chain, path))}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"preset {path} must be a YAML mapping, got {type(raw).__name__}")
    base = raw.pop("extends", None)
    if base is None:
        return raw
    return {**load_preset_yaml(base, (*_chain, path)), **raw}


def load_config_yaml(*parts: str) -> dict[str, Any]:
    """Parse ``configs/<parts...>`` (e.g. ``("domain", "p2p.yaml")``) into a dict.

    For non-preset configuration (agent settings, heuristics catalogue,
    domain knowledge); presets go through ``load_preset_yaml``.
    """
    path = find_project_root().joinpath(_CONFIGS_DIRNAME, *parts)
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config {path} must be a YAML mapping, got {type(raw).__name__}")
    return raw


def resolve_source_path(preset: dict[str, Any]) -> Path:
    """Turn a preset's ``source.path_env`` (or ``source.path``) into an existing file path.

    ``path_env`` names an environment variable, for machine-specific files
    outside the repo. ``path`` is relative to the project root and only meant
    for files committed with the project (the BPI sample in ``data/sample``).

    Fails loudly with the variable's name, because "file not found" without
    saying *which setting* produced the path is the most common setup error on
    a fresh machine.
    """
    source = preset.get("source") or {}
    env_var = source.get("path_env")
    if not env_var and source.get("path"):
        committed = find_project_root() / Path(source["path"])
        if not committed.is_file():
            raise FileNotFoundError(f"source.path points at a missing file: {committed}")
        return committed
    if not env_var:
        raise ValueError("preset has no source.path_env or source.path; cannot locate the input")
    value = os.environ.get(env_var)
    if not value:
        raise OSError(
            f"environment variable {env_var} is not set; copy .env.example to .env "
            "and fill it in (or call load_env() first)"
        )
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(f"{env_var} points at a file that does not exist: {path}")
    return path
