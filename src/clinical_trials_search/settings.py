"""Local configuration is deliberately stored outside the source repository."""

from __future__ import annotations

import json
import os
from pathlib import Path


def source_repository() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (
            parent / "src" / "clinical_trials_search"
        ).is_dir():
            return parent
    return None


def require_external(path: Path | str) -> Path:
    target = Path(path).expanduser().resolve()
    # Inspect the destination as well as __file__: installed wheels are outside
    # their original checkout, and must still reject writes into that checkout.
    roots = [source_repository(), *target.parents, target]
    for repo in roots:
        if (
            repo
            and (repo / "pyproject.toml").is_file()
            and (repo / "src" / "clinical_trials_search").is_dir()
        ):
            if target == repo or repo in target.parents:
                raise ValueError(
                    "Local settings and search output must be outside the source repository"
                )
    return target


def load_settings(config: str | Path | None = None) -> dict:
    path = config or os.environ.get("CTS_CONFIG")
    values = {}
    if path:
        config_path = require_external(path)
        values = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict) or set(values) - {
            "output_root",
            "rscript",
            "r_library",
        }:
            raise ValueError("Config supports only output_root, rscript, and r_library")
        for key, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Config {key} must be a nonempty string")
        # Resolve relative paths against the config, not the current shell directory.
        for key in ("output_root", "r_library"):
            if key in values:
                values[key] = str((config_path.parent / values[key]).resolve())
        if "rscript" in values and (
            "/" in values["rscript"] or "\\" in values["rscript"]
        ):
            values["rscript"] = str((config_path.parent / values["rscript"]).resolve())
    if os.environ.get("CTS_OUTPUT_ROOT"):
        values["output_root"] = os.environ["CTS_OUTPUT_ROOT"]
    return values


def save_settings(path: str | Path, values: dict) -> Path:
    target = require_external(path)
    if target.exists():
        raise ValueError(
            "Config already exists; edit it explicitly rather than overwriting it"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    return target
