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


def load_preset_yaml(name_or_path: Path | str) -> dict[str, Any]:
    """Parse a dataset preset YAML into a dict. The only YAML reader for presets."""
    path = resolve_preset_path(name_or_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"preset {path} must be a YAML mapping, got {type(raw).__name__}")
    return raw


def resolve_source_path(preset: dict[str, Any]) -> Path:
    """Turn a preset's ``source.path_env`` into an existing file path.

    Fails loudly with the variable's name, because "file not found" without
    saying *which setting* produced the path is the most common setup error on
    a fresh machine.
    """
    source = preset.get("source") or {}
    env_var = source.get("path_env")
    if not env_var:
        raise ValueError("preset has no source.path_env; cannot locate the input file")
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
