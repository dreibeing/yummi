from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..config import get_settings


_MANIFEST_CACHE: dict[str, Any] | None = None
_MANIFEST_PATH: Path | None = None
_MANIFEST_MTIME: float | None = None
_LOCK = threading.Lock()


def _candidate_paths(raw_path: str) -> list[Path]:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return [candidate]
    server_root = Path(__file__).resolve().parents[2]
    repo_root = server_root.parent
    cwd = Path.cwd()
    return [
        (cwd / candidate).resolve(),
        (server_root / candidate).resolve(),
        (repo_root / candidate).resolve(),
    ]


def _resolve_manifest_path(raw_path: str) -> Path:
    for option in _candidate_paths(raw_path):
        if option.exists():
            return option
    # Fall back to first candidate to produce a sensible error
    return _candidate_paths(raw_path)[0]


def _load_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_meal_manifest() -> dict[str, Any]:
    """Return the cached meal manifest, refreshing when the file changes."""
    settings = get_settings()
    if not settings.meals_manifest_path:
        raise HTTPException(status_code=503, detail="Meals manifest path is not configured")
    manifest_path = _resolve_manifest_path(settings.meals_manifest_path)
    if not manifest_path.exists():
        raise HTTPException(status_code=503, detail=f"Meals manifest not found at {manifest_path}")

    global _MANIFEST_CACHE, _MANIFEST_MTIME, _MANIFEST_PATH
    mtime = os.path.getmtime(manifest_path)
    with _LOCK:
        if (
            _MANIFEST_CACHE is not None
            and _MANIFEST_PATH == manifest_path
            and _MANIFEST_MTIME == mtime
        ):
            return deepcopy(_MANIFEST_CACHE)

        manifest = _load_manifest(manifest_path)
        _MANIFEST_CACHE = manifest
        _MANIFEST_PATH = manifest_path
        _MANIFEST_MTIME = mtime
        return deepcopy(manifest)


def get_meal_archetype(uid: str) -> dict[str, Any]:
    manifest = get_meal_manifest()
    for entry in manifest.get("archetypes", []):
        if entry.get("uid") == uid:
            return entry
    raise HTTPException(status_code=404, detail=f"Archetype '{uid}' not found")
