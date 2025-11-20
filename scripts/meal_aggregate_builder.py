#!/usr/bin/env python3
"""Build a consolidated meal manifest for serving and analytics."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_ARCHETYPE_PATH = Path("data/archetypes/run_20251112T091259Z/archetypes_aggregated.json")
DEFAULT_TAGS_MANIFEST = Path("data/tags/defined_tags.json")
DEFAULT_MEALS_DIR = Path("data/meals")
DEFAULT_MANIFEST_PATH = Path("resolver/meals/meals_manifest.json")
DEFAULT_PARQUET_PATH = Path("resolver/meals/meals_manifest.parquet")

ALWAYS_REQUIRED_MEAL_CATEGORIES = {
    "Diet",
    "Cuisine",
    "PrepTime",
    "Complexity",
    "HeatSpice",
    "BudgetLevel",
}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archetype-json", type=Path, default=DEFAULT_ARCHETYPE_PATH, help="Path to archetype JSON (aggregated run output)")
    parser.add_argument("--tags-manifest", type=Path, default=DEFAULT_TAGS_MANIFEST, help="Path to defined_tags manifest JSON")
    parser.add_argument("--meals-dir", type=Path, default=DEFAULT_MEALS_DIR, help="Directory that stores per-archetype meals")
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH, help="Output JSON manifest path")
    parser.add_argument("--parquet-path", type=Path, default=DEFAULT_PARQUET_PATH, help="Output Parquet path for flattened meal rows")
    parser.add_argument("--manifest-id", default=None, help="Optional manifest identifier; defaults to timestamp-based slug")
    parser.add_argument(
        "--archetype-uid",
        action="append",
        dest="archetype_uids",
        default=None,
        help="Limit aggregation to specific archetype UID (can be repeated)",
    )
    parser.add_argument("--schema-version", default="2025.11.14", help="Schema/contract version embedded in the manifest")
    parser.add_argument(
        "--skip-parquet",
        action="store_true",
        help="Skip Parquet generation even if pyarrow is available",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_archetype_index(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    archetypes = payload.get("archetypes") or []
    if not archetypes:
        raise RuntimeError(f"No archetypes found in {path}")
    index: dict[str, dict[str, Any]] = {}
    for entry in archetypes:
        uid = entry.get("uid")
        if not uid:
            continue
        index[uid] = entry
    return index


def load_meal_payloads(meals_dir: Path, archetype_uids: set[str] | None) -> list[dict[str, Any]]:
    if not meals_dir.exists():
        raise FileNotFoundError(f"Meals directory not found: {meals_dir}")
    payloads: list[dict[str, Any]] = []
    for meal_file in sorted(meals_dir.glob("arch_*/*.json")):
        try:
            record = json.loads(meal_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in {meal_file}") from exc
        uid = record.get("archetype_uid")
        if archetype_uids and uid not in archetype_uids:
            continue
        payloads.append(record)
    if not payloads:
        raise RuntimeError("No meal payloads discovered for aggregation")
    return payloads


def ensure_required_categories(required: Iterable[str], tags: dict[str, list[str]], archetype_tags: dict[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    normalized: dict[str, list[str]] = {}
    warnings: list[str] = []
    for category, values in (tags or {}).items():
        if not isinstance(values, list):
            continue
        normalized[category] = values
    fallback_source = archetype_tags or {}
    for category in required:
        if normalized.get(category):
            continue
        fallback = fallback_source.get(category)
        if fallback:
            normalized[category] = fallback if isinstance(fallback, list) else [fallback]
            warnings.append(f"Filled missing category '{category}' from archetype defaults")
        else:
            warnings.append(f"Meal missing required category '{category}' with no archetype fallback")
    return normalized, warnings


def build_archetype_block(archetype: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": archetype.get("uid"),
        "name": archetype.get("name"),
        "description": archetype.get("description"),
        "core_tags": archetype.get("core_tags") or {},
        "diet_profile": archetype.get("diet_profile") or {},
        "allergen_flags": archetype.get("allergen_flags") or {},
        "heat_band": archetype.get("heat_band"),
        "prep_time_minutes_range": archetype.get("prep_time_minutes_range"),
        "complexity": archetype.get("complexity"),
        "audience_context": archetype.get("audience_context"),
        "cuisine_openness": archetype.get("cuisine_openness"),
        "refresh_version": archetype.get("refresh_version"),
        "rationale": archetype.get("rationale"),
        "meals": [],
    }


def build_meal_block(
    meal: dict[str, Any],
    *,
    required_categories: list[str],
    archetype_tags: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = meal.get("warnings", []).copy()
    tags, required_notes = ensure_required_categories(required_categories, meal.get("meal_tags") or {}, archetype_tags)
    warnings.extend(required_notes)
    record = {
        "meal_id": meal.get("meal_id"),
        "name": meal.get("name"),
        "description": meal.get("description"),
        "servings": meal.get("servings"),
        "meal_tags": tags,
        "prep_steps": meal.get("prep_steps", []),
        "cook_steps": meal.get("cook_steps", []),
        "instructions": meal.get("instructions", []),
        "ingredients": meal.get("ingredients", []),
        "final_ingredients": meal.get("final_ingredients", []),
        "product_matches": meal.get("product_matches", []),
        "metadata": meal.get("metadata", {}),
    }
    if warnings:
        record["warnings"] = warnings
    return record, warnings


def flatten_meal_rows(
    manifest_id: str,
    archetype: dict[str, Any],
    meal: dict[str, Any],
) -> dict[str, Any]:
    return {
        "manifest_id": manifest_id,
        "archetype_uid": archetype.get("uid"),
        "archetype_name": archetype.get("name"),
        "archetype_refresh_version": archetype.get("refresh_version"),
        "meal_id": meal.get("meal_id"),
        "meal_name": meal.get("name"),
        "servings": meal.get("servings"),
        "tags_json": json.dumps(meal.get("meal_tags") or {}, ensure_ascii=False, sort_keys=True),
        "ingredients_json": json.dumps(meal.get("final_ingredients") or meal.get("ingredients") or [], ensure_ascii=False),
        "metadata_json": json.dumps(meal.get("metadata") or {}, ensure_ascii=False),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        print("[warn] pyarrow not available; skipping Parquet output")
        return False
    table = pa.Table.from_pylist(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return True


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    archetype_index = load_archetype_index(Path(args.archetype_json))
    tags_manifest = read_json(Path(args.tags_manifest))
    manifest_required_categories = tags_manifest.get("required_categories", {}).get("meal", [])
    if not manifest_required_categories:
        manifest_required_categories = sorted(ALWAYS_REQUIRED_MEAL_CATEGORIES)
    manifest_required_categories = list(dict.fromkeys(manifest_required_categories))
    archetype_filter = set(args.archetype_uids or [])
    meals = load_meal_payloads(Path(args.meals_dir), archetype_filter or None)

    manifest_id = args.manifest_id or f"meals_{timestamp_slug()}"
    generated_at = timestamp_slug()

    archetypes_payload: dict[str, dict[str, Any]] = {}
    manifest_warnings: list[str] = []
    parquet_rows: list[dict[str, Any]] = []

    for meal in meals:
        archetype_uid = meal.get("archetype_uid")
        if not archetype_uid:
            manifest_warnings.append(f"Skipping meal without archetype_uid: {meal.get('meal_id')}")
            continue
        archetype = archetype_index.get(archetype_uid)
        if not archetype:
            manifest_warnings.append(f"Skipping meal '{meal.get('meal_id')}' referencing unknown archetype '{archetype_uid}'")
            continue
        archetype_entry = archetypes_payload.setdefault(archetype_uid, build_archetype_block(archetype))
        meal_block, meal_warnings = build_meal_block(
            meal,
            required_categories=manifest_required_categories,
            archetype_tags=archetype.get("core_tags") or {},
        )
        archetype_entry["meals"].append(meal_block)
        manifest_warnings.extend(
            f"{meal_block.get('meal_id')}: {note}" for note in meal_warnings if note not in ("", None)
        )
        parquet_rows.append(flatten_meal_rows(manifest_id, archetype_entry, meal_block))

    archetype_list = sorted(archetypes_payload.values(), key=lambda item: item.get("uid") or "")
    for entry in archetype_list:
        entry["meals"] = sorted(entry.get("meals", []), key=lambda meal: meal.get("meal_id") or "")

    manifest = {
        "schema_version": args.schema_version,
        "manifest_id": manifest_id,
        "generated_at": generated_at,
        "tags_version": tags_manifest.get("tags_version"),
        "required_categories": manifest_required_categories,
        "source": {
            "archetype_json": str(Path(args.archetype_json)),
            "meals_dir": str(Path(args.meals_dir)),
        },
        "stats": {
            "archetype_count": len(archetype_list),
            "meal_count": sum(len(entry.get("meals", [])) for entry in archetype_list),
        },
        "warnings": sorted(set(manifest_warnings)),
        "archetypes": archetype_list,
    }

    write_json(Path(args.manifest_path), manifest)
    print(f"[ok] Wrote manifest to {args.manifest_path}")
    if not args.skip_parquet:
        if write_parquet(Path(args.parquet_path), parquet_rows):
            print(f"[ok] Wrote Parquet rows to {args.parquet_path}")


if __name__ == "__main__":
    main()
