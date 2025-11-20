#!/usr/bin/env python3
"""Sync predefined archetype configs and folder structure from a sheet.

This utility reads `data/archetypes/predefined_archetypes.xlsx` (or `.csv`) and
materializes a folder per predefined archetype under `data/archetypes/predefined/`.

Each config captures hard constraints (DietaryRestrictions + Audience) and any
required sub‑archetype tag coverage hints. The prompt runner can then be pointed
at a given folder (via `--predefined-config` or auto-detection) and will scope
generation accordingly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_SHEET_XLSX = REPO_ROOT / "data/archetypes/predefined_archetypes.xlsx"
DEFAULT_SHEET_CSV = REPO_ROOT / "data/archetypes/predefined_archetypes.csv"
DEFAULT_DEFINED_TAGS = REPO_ROOT / "data/tags/defined_tags.json"
OUTPUT_BASE = REPO_ROOT / "data/archetypes/predefined"


def read_defined_tags(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing defined tags manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


PLACEHOLDER_TOKENS = {"", "na", "n/a", "na.", "nil", "nan"}


def _slugify(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        # Support pipe/comma separated strings
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace("|", ",").split(",")]
            items = [p for p in parts if p]
        else:
            items = [str(value).strip()] if str(value).strip() else []
    filtered: List[str] = []
    for item in items:
        token = item.strip()
        if token.lower() in PLACEHOLDER_TOKENS:
            continue
        filtered.append(token)
    return filtered

def _normalize_header(name: Any) -> str:
    return str(name).strip().lstrip("\ufeff")


def _normalize_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def load_sheet_rows(xlsx_path: Path, csv_path: Path) -> Tuple[List[dict[str, Any]], List[str]]:
    # Prefer Excel via pandas; fall back to CSV if not available.
    if xlsx_path.exists():
        try:
            import pandas as pd  # type: ignore

            df = pd.read_excel(xlsx_path)
            raw_columns = list(df.columns)
            columns = [_normalize_header(col) for col in raw_columns]
            rows: List[dict[str, Any]] = []
            for _, series in df.iterrows():
                record: dict[str, Any] = {}
                for raw_col, norm_col in zip(raw_columns, columns):
                    value = series[raw_col] if raw_col in series else None
                    if pd.isna(value):
                        value = ""
                    record[norm_col] = _normalize_cell(value)
                rows.append(record)
            return rows, columns
        except Exception as exc:  # pragma: no cover - runtime environment dependent
            print(
                f"Warning: Failed to read {xlsx_path} ({exc}). Falling back to CSV if present...",
                file=sys.stderr,
            )
    if csv_path.exists():
        import csv

        with csv_path.open("r", encoding="utf-8") as handle:
            sample = handle.read(2048)
            handle.seek(0)
            delimiter = ","
            if sample.count(";") > sample.count(","):
                delimiter = ";"
            elif sample.count("\t") > sample.count(","):
                delimiter = "\t"
            reader = csv.reader(handle, delimiter=delimiter)
            try:
                headers_raw = next(reader)
            except StopIteration:
                return [], []
            columns = [_normalize_header(col) for col in headers_raw]
            rows: List[dict[str, Any]] = []
            for raw_row in reader:
                if not any(cell.strip() for cell in raw_row):
                    continue
                record = {}
                for idx, col in enumerate(columns):
                    cell = raw_row[idx] if idx < len(raw_row) else ""
                    record[col] = _normalize_cell(cell)
                rows.append(record)
            return rows, columns
    raise FileNotFoundError(
        f"Neither {xlsx_path} nor {csv_path} is available and readable."
    )


def extract_required_subtags(row: dict[str, Any], known_categories: set[str]) -> dict[str, List[str]]:
    required: dict[str, List[str]] = {}
    for key, value in row.items():
        key_str = str(key).strip()
        key_lower = key_str.lower()
        if not key_str:
            continue
        if key_lower in {"notes", "name", "title"}:
            continue
        if key_lower == "diet" or key_lower.startswith("dietaryrestrictions"):
            continue
        if key_lower.startswith("audience"):
            continue
        # Only consider cells that map to known categories, if available
        if known_categories and key_str not in known_categories:
            continue
        items = _coerce_list(value)
        if items:
            required[key_str] = items
    return required


def ensure_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sync(args: argparse.Namespace) -> None:
    tags_manifest = read_defined_tags(Path(args.defined_tags))
    tags_version = tags_manifest.get("tags_version")
    archetype_required = set((tags_manifest.get("required_categories", {}) or {}).get("archetype", []) or [])

    rows, columns = load_sheet_rows(Path(args.xlsx), Path(args.csv))
    if not rows:
        raise ValueError("No rows found in the predefined archetypes sheet")

    created = 0
    skipped_duplicates = 0
    seen_slugs: set[str] = set()

    if len(columns) < 3:
        raise ValueError("Predefined archetype sheet must contain at least three columns (DietaryRestrictions, Audience, DietaryRestrictions2)")
    diet_primary_col = columns[0]
    audience_col = columns[1]
    diet_secondary_col = columns[2]

    for row in rows:
        diet_primary = _normalize_cell(row.get(diet_primary_col))
        audience_value = _normalize_cell(row.get(audience_col))
        diet_secondary = _normalize_cell(row.get(diet_secondary_col))

        # Skip rows missing primary diet or audience
        if not diet_primary or not audience_value:
            continue

        diet_values = [diet_primary]
        if diet_secondary and diet_secondary.lower() not in PLACEHOLDER_TOKENS:
            diet_values.append(diet_secondary)

        diets = diet_values
        audiences = [audience_value]

        slug_parts = [
            _slugify(diet_primary or "none"),
            _slugify(audience_value or "none"),
            _slugify(diet_secondary or "none"),
        ]
        folder_slug = "_".join(slug_parts)

        slug = folder_slug
        folder = Path(args.output_base) / slug
        if slug in seen_slugs:
            skipped_duplicates += 1
        else:
            ensure_folder(folder)
            seen_slugs.add(slug)
            created += 1

        required_sub = extract_required_subtags(row, archetype_required)
        payload = {
            "predefined_uid": slug,
            "tags_version": tags_version,
            "hard_constraints": {
                "DietaryRestrictions": diets,
                "Audience": audiences,
            },
            "required_subarchetype_tags": required_sub,
            "source_row": row,
            "source_scope": {
                "dietary_restrictions": diets,
                "audience": audiences,
            },
        }
        write_json(folder / "config.json", payload)

    message = f"Materialized {created} predefined archetype folder(s) under {args.output_base}"
    if skipped_duplicates:
        message += f" (skipped {skipped_duplicates} duplicate combination(s))"
    print(message)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate predefined archetype folders from a sheet.")
    p.add_argument("--xlsx", default=str(DEFAULT_SHEET_XLSX), help="Path to predefined_archetypes.xlsx")
    p.add_argument("--csv", default=str(DEFAULT_SHEET_CSV), help="CSV fallback path if Excel cannot be read")
    p.add_argument("--defined-tags", default=str(DEFAULT_DEFINED_TAGS), help="Path to defined_tags.json")
    p.add_argument(
        "--output-base",
        default=str(OUTPUT_BASE),
        help="Base directory to place predefined archetype folders",
    )
    return p


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        sync(args)
    except Exception as exc:  # pragma: no cover - CLI surface
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
