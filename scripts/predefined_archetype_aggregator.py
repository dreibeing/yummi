#!/usr/bin/env python3
"""Combine archetype runs per predefined scope into a single JSON artifact."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


DEFAULT_BASE_DIR = Path("data/archetypes/predefined")
DEFAULT_OUTPUT_FILENAME = "archetypes_combined.json"


@dataclass
class RunPayload:
    source_file: Path
    archetypes: List[dict]
    tags_version: Optional[str]
    scope: Optional[dict]


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_BASE_DIR,
        help="Folder containing predefined archetype scopes (each with run_* subdirectories).",
    )
    parser.add_argument(
        "--output-filename",
        default=DEFAULT_OUTPUT_FILENAME,
        help="Filename to write at each predefined folder root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report without writing any files.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing expected file: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}") from exc


def discover_run_payload(run_dir: Path) -> RunPayload | None:
    """Return the first matching payload for a run or None if nothing usable."""
    candidates = [
        run_dir / "archetypes_aggregated.json",
        run_dir / "curation" / "archetypes_curated.json",
        run_dir / "archetypes_so_far.json",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        data = load_json(candidate)
        archetypes = data.get("archetypes")
        if not isinstance(archetypes, list) or not archetypes:
            continue
        tags_version = data.get("tags_version")
        scope = data.get("predefined_scope") or data.get("scope")
        return RunPayload(candidate, archetypes, tags_version, scope)
    return None


def aggregate_predefined_folder(folder: Path, output_filename: str, dry_run: bool) -> dict:
    run_dirs = sorted(
        [p for p in folder.iterdir() if p.is_dir() and p.name.startswith("run_")]
    )
    combined: List[dict] = []
    seen_uids: set[str] = set()
    duplicate_uids: set[str] = set()
    tags_version: Optional[str] = None
    scope: Optional[dict] = None
    source_runs: List[dict] = []

    for run_dir in run_dirs:
        payload = discover_run_payload(run_dir)
        if payload is None:
            continue
        if tags_version is None:
            tags_version = payload.tags_version
        elif payload.tags_version and payload.tags_version != tags_version:
            raise ValueError(
                f"Tag version mismatch in {folder}: expected {tags_version}, "
                f"got {payload.tags_version} from {payload.source_file}"
            )
        if scope is None and payload.scope:
            scope = payload.scope

        added = 0
        for archetype in payload.archetypes:
            uid = str(archetype.get("uid") or "").strip()
            if not uid:
                continue
            if uid in seen_uids:
                duplicate_uids.add(uid)
                continue
            seen_uids.add(uid)
            combined.append(archetype)
            added += 1

        source_runs.append(
            {
                "run_dir": run_dir.name,
                "source_file": str(payload.source_file.relative_to(folder)),
                "archetype_count": len(payload.archetypes),
                "added_to_combined": added,
            }
        )

    if not combined:
        return {
            "folder": str(folder),
            "status": "skipped",
            "reason": "no archetypes discovered",
        }

    output_payload = {
        "tags_version": tags_version,
        "predefined_scope": scope,
        "archetypes": combined,
        "source_runs": source_runs,
        "stats": {
            "run_count": len(source_runs),
            "archetype_count": len(combined),
            "duplicate_uids_skipped": sorted(duplicate_uids),
        },
    }

    if dry_run:
        return {
            "folder": str(folder),
            "status": "dry-run",
            "archetype_count": len(combined),
            "duplicate_uids": sorted(duplicate_uids),
        }

    output_path = folder / output_filename
    output_path.write_text(json.dumps(output_payload, indent=2) + "\n", encoding="utf-8")
    return {
        "folder": str(folder),
        "status": "written",
        "output": str(output_path),
        "archetype_count": len(combined),
        "duplicate_uids": sorted(duplicate_uids),
    }


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    base_dir: Path = args.base_dir
    if not base_dir.exists():
        raise SystemExit(f"Base directory not found: {base_dir}")

    results = []
    for folder in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        results.append(aggregate_predefined_folder(folder, args.output_filename, args.dry_run))

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
