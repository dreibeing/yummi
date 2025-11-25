#!/usr/bin/env python3
"""Curate ingredient pools per archetype using the canonical core item list."""

from __future__ import annotations

import argparse
import copy
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from llm_utils import OpenAIClientError, call_openai_api


DEFAULT_CORE_ITEMS = Path("data/ingredients/unique_core_items.csv")
ARH_COMBINED_FILENAME = "archetypes_combined.json"
TRACKING_FILENAME = "curated_ingredients.json"
DEFAULT_MODEL = "gpt-5-mini"


@dataclass(frozen=True)
class CoreItem:
    name: str
    item_type: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Curate archetype-specific ingredient pools from the canonical core item catalog."
    )
    parser.add_argument(
        "--predefined-dir",
        type=Path,
        required=True,
        help="Predefined archetype directory (contains archetypes_combined.json).",
    )
    parser.add_argument(
        "--core-items",
        type=Path,
        default=DEFAULT_CORE_ITEMS,
        help="CSV containing canonical core items (default: data/ingredients/unique_core_items.csv).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="OpenAI model to use for ingredient curation (default: gpt-5-mini, low reasoning).",
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature.")
    parser.add_argument(
        "--top-p",
        type=float,
        default=1.0,
        help="Nucleus sampling parameter (top_p).",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=2500,
        help="max_output_tokens for the Responses API (ignored by non-reasoning models).",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default="low",
        help="Reasoning effort hint for GPT-5 models.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip API calls and materialize rendered prompts instead.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=120,
        help="Hard ceiling for how many ingredient names the model may return.",
    )
    parser.add_argument(
        "--recurate-all",
        action="store_true",
        help="Regenerate all archetypes even if a curated ingredient list already exists.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        help="Number of catalog ingredients to include per prompt chunk.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_core_items(path: Path) -> List[CoreItem]:
    if not path.exists():
        raise FileNotFoundError(f"Core item catalog not found: {path}")
    items: List[CoreItem] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "core_item_name" not in reader.fieldnames:
            raise ValueError(f"CSV missing 'core_item_name' header: {path}")
        for row in reader:
            raw_name = (row.get("core_item_name") or "").strip()
            if not raw_name:
                continue
            item_type = (row.get("item_type") or "ingredient").strip() or "ingredient"
            items.append(CoreItem(name=raw_name, item_type=item_type))
    if not items:
        raise ValueError(f"No core items loaded from {path}")
    return items


def index_core_items(items: Iterable[CoreItem]) -> Tuple[Dict[str, CoreItem], Dict[str, List[str]]]:
    name_map: Dict[str, CoreItem] = {}
    grouped: Dict[str, List[str]] = defaultdict(list)
    for item in items:
        name_map[item.name.lower()] = item
        grouped[item.item_type].append(item.name)
    for values in grouped.values():
        values.sort(key=lambda name: name.lower())
    return name_map, grouped


def load_archetypes(predefined_dir: Path) -> Dict[str, Any]:
    archetype_path = predefined_dir / ARH_COMBINED_FILENAME
    if not archetype_path.exists():
        raise FileNotFoundError(f"Missing archetypes_combined.json in {predefined_dir}")
    try:
        payload = json.loads(archetype_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse {archetype_path}") from exc
    archetypes = payload.get("archetypes")
    if not archetypes:
        raise ValueError(f"No archetypes found in {archetype_path}")
    return payload


def ensure_run_dir(predefined_dir: Path) -> Path:
    base = predefined_dir / "ingredient_curation"
    base.mkdir(parents=True, exist_ok=True)
    run_dir = base / f"run_{timestamp_slug()}"
    run_dir.mkdir(exist_ok=False, parents=True)
    return run_dir


def _normalize_existing_entry(
    entry: Dict[str, Any],
    *,
    known_items: Dict[str, CoreItem],
    ) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    """Return ingredient names, structured ingredients, and unmatched names."""
    raw_names: List[str] = []
    unmatched: List[str] = []
    if "ingredient_names" in entry and isinstance(entry["ingredient_names"], list):
        raw_names = [str(item).strip() for item in entry["ingredient_names"] if str(item).strip()]
    elif "curated_ingredients" in entry:
        curated = entry.get("curated_ingredients") or {}
        for bucket in ("essential", "supporting", "exploratory"):
            for item in curated.get(bucket) or []:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                else:
                    name = str(item).strip()
                if name:
                    raw_names.append(name)
    elif "ingredients" in entry:
        for item in entry.get("ingredients") or []:
            name = str(item.get("core_item_name") or "").strip()
            if name:
                raw_names.append(name)

    normalized, unmatched = _normalize_name(raw_names, known_items=known_items)
    structured = [
        {
            "core_item_name": name,
            "item_type": known_items[name.lower()].item_type,
        }
        for name in normalized
        if name.lower() in known_items
    ]
    return normalized, structured, unmatched


def get_tracking_dir(predefined_dir: Path) -> Path:
    path = predefined_dir / "ingredient_curation"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_tracking_data(
    predefined_dir: Path, *, known_items: Dict[str, CoreItem]
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    tracking_dir = get_tracking_dir(predefined_dir)
    tracking_path = tracking_dir / TRACKING_FILENAME
    if not tracking_path.exists():
        return {}, {}
    try:
        payload = json.loads(tracking_path.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}

    existing: Dict[str, Dict[str, Any]] = {}
    for entry in payload.get("archetype_ingredient_sets", []) or []:
        uid = str(entry.get("uid") or "").strip()
        if not uid:
            continue
        names, structured, unmatched = _normalize_existing_entry(
            entry, known_items=known_items
        )
        existing[uid] = {
            "uid": uid,
            "name": entry.get("name"),
            "description": entry.get("description"),
            "core_tags": entry.get("core_tags"),
            "ingredient_names": names,
            "ingredients": structured,
            "unmatched_suggestions": unmatched or entry.get("unmatched_suggestions") or [],
        }
    metadata = {
        "generated_at": payload.get("generated_at"),
        "model": payload.get("model"),
        "tags_version": payload.get("tags_version"),
        "predefined_scope": payload.get("predefined_scope"),
        "core_item_catalog": payload.get("core_item_catalog"),
    }
    return existing, metadata


def write_tracking_data(
    predefined_dir: Path,
    *,
    combined_entries: Dict[str, Dict[str, Any]],
    metadata: Dict[str, Any],
) -> None:
    tracking_dir = get_tracking_dir(predefined_dir)
    tracking_path = tracking_dir / TRACKING_FILENAME
    payload = {
        "generated_at": timestamp_slug(),
        "model": metadata.get("model"),
        "tags_version": metadata.get("tags_version"),
        "predefined_scope": metadata.get("predefined_scope"),
        "core_item_catalog": metadata.get("core_item_catalog"),
        "archetype_ingredient_sets": sorted(
            combined_entries.values(),
            key=lambda item: (item.get("uid") or "").lower(),
        ),
    }
    tracking_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def chunk_core_items(items: Sequence[CoreItem], chunk_size: int) -> List[List[CoreItem]]:
    if chunk_size <= 0:
        return [list(items)] if items else []
    chunks: List[List[CoreItem]] = []
    for start in range(0, len(items), chunk_size):
        chunk = list(items[start : start + chunk_size])
        if chunk:
            chunks.append(chunk)
    if not chunks and items:
        chunks.append(list(items))
    return chunks


def build_system_prompt() -> str:
    return dedent(
        f"""
        You are Yummi's ingredient sourcing strategist. For each request you receive a single archetype plus the canonical
        ingredient catalog. Your job is to identify the ingredients that align with that archetype's culinary profile,
        using only the context supplied in the conversation. Always ground your decisions in the archetype description
        and tags provided later; do not assume any other defaults. When uncertain, err on the side of keeping plausible
        fits, but filter clear mismatches.

        Return strict JSON with this structure:
        {{
          "archetype_uid": "arch_XXXX",
          "selected_ingredients": [
            "Ingredient A",
            "Ingredient B"
          ]
        }}

        Rules:
        - Return only the JSON object above; no extra commentary.
        - Names must match the catalog exactly (case and spelling).
        - Avoid beverages, household items, or duplicative variants that the archetype would reasonably skip.
        - Ensure the list spans pantry staples, proteins, starches, produce, and sauces that fit the archetype’s cuisines.
        - Do not invent ingredients that are not in the catalog.
        """
    ).strip()


def _format_core_tags(core_tags: Dict[str, Any]) -> str:
    lines: List[str] = []
    for category in sorted(core_tags):
        values = core_tags[category]
        if not values:
            continue
        if isinstance(values, list):
            joined = ", ".join(values)
        else:
            joined = str(values)
        lines.append(f"- {category}: {joined}")
    return "\n".join(lines) if lines else "None recorded."


def build_chunk_user_prompt(
    archetype: Dict[str, Any],
    *,
    chunk_items: Sequence[CoreItem],
    chunk_index: int,
    total_chunks: int,
) -> str:
    lines: List[str] = []
    lines.append(f"Archetype UID: {archetype.get('uid')}")
    lines.append(f"Archetype Name: {archetype.get('name')}")
    lines.append("Archetype Description:")
    lines.append(archetype.get("description", "").strip() or "No description provided.")
    lines.append("")
    lines.append("Core Tags:")
    lines.append(_format_core_tags(archetype.get("core_tags") or {}))
    lines.append("")
    lines.append(
        dedent(
            f"""
            Ingredient catalog chunk {chunk_index}/{total_chunks} (format: item_type — core_item_name).
            For each entry, answer a single question: would this archetype reasonably use this ingredient when cooking?
            If yes (even occasionally), include the ingredient name in `selected_ingredients`. If the ingredient is clearly unsuitable, exclude it.
            """
        ).strip()
    )
    for item in chunk_items:
        lines.append(f"- {item.item_type or 'unknown'} — {item.name}")
    lines.append("")
    lines.append("Return strict JSON only; do not include extra commentary.")
    return "\n".join(lines)


def scrub_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()


def parse_model_payload(raw_text: str) -> Dict[str, Any]:
    payload = json.loads(scrub_json(raw_text))
    names = payload.get("selected_ingredients")
    if not isinstance(names, list):
        raise ValueError("Model response missing 'selected_ingredients' list.")
    return payload


def _normalize_name(
    value: Any,
    *,
    known_items: Dict[str, CoreItem],
) -> Tuple[List[str], List[str]]:
    if not isinstance(value, list):
        raise ValueError("ingredient_names must be a list.")
    normalized: List[str] = []
    unmatched: List[str] = []
    seen: set[str] = set()
    for raw in value:
        name = str(raw).strip()
        if not name:
            continue
        lookup = known_items.get(name.lower())
        if not lookup:
            unmatched.append(name)
            continue
        key = lookup.name.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(lookup.name)
    normalized.sort(key=lambda item: item.lower())
    return normalized, unmatched


def run(args: argparse.Namespace) -> None:
    payload = load_archetypes(args.predefined_dir)
    core_items = load_core_items(args.core_items)
    known_items, _ = index_core_items(core_items)

    run_dir = ensure_run_dir(args.predefined_dir)
    existing_curations, previous_metadata = load_tracking_data(
        args.predefined_dir, known_items=known_items
    )
    combined_entries: Dict[str, Dict[str, Any]] = copy.deepcopy(existing_curations)

    tags_version = payload.get("tags_version")
    scope = payload.get("predefined_scope") or {}
    archetypes = payload["archetypes"]

    system_prompt = build_system_prompt()
    catalog_chunks = chunk_core_items(core_items, args.chunk_size)
    total_chunks = len(catalog_chunks)

    aggregated_results: List[Dict[str, Any]] = []
    run_metadata: Dict[str, Any] = {
        "generated_at": timestamp_slug(),
        "model": args.model,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_output_tokens": args.max_output_tokens,
        "reasoning_effort": args.reasoning_effort,
        "tags_version": tags_version,
        "predefined_scope": scope,
        "source": {
            "predefined_dir": str(args.predefined_dir),
            "archetypes_combined": str(args.predefined_dir / ARH_COMBINED_FILENAME),
            "core_items": str(args.core_items),
        },
        "archetype_runs": [],
    }
    run_metadata_path = run_dir / "run_metadata.json"
    tracking_metadata: Dict[str, Any] = {
        "model": args.model,
        "tags_version": tags_version or previous_metadata.get("tags_version"),
        "predefined_scope": scope or previous_metadata.get("predefined_scope"),
        "core_item_catalog": str(args.core_items),
    }
    if tracking_metadata["tags_version"] is None:
        tracking_metadata["tags_version"] = tags_version
    if tracking_metadata["predefined_scope"] is None:
        tracking_metadata["predefined_scope"] = scope

    for index, archetype in enumerate(archetypes, start=1):
        uid = archetype.get("uid") or f"index_{index}"
        name = archetype.get("name") or "Unknown archetype"
        print(f"[{index}/{len(archetypes)}] Curating ingredients for {uid} — {name}")

        if not args.dry_run and not args.recurate_all and uid in existing_curations:
            existing_entry = copy.deepcopy(existing_curations[uid])
            if not existing_entry.get("name"):
                existing_entry["name"] = name
            if not existing_entry.get("description"):
                existing_entry["description"] = archetype.get("description")
            if not existing_entry.get("core_tags"):
                existing_entry["core_tags"] = archetype.get("core_tags")
            aggregated_results.append(existing_entry)
            run_metadata["archetype_runs"].append(
                {
                    "uid": uid,
                    "name": name,
                    "status": "skipped_existing",
                    "reason": "existing curated ingredients found",
                    "return_count": len(existing_entry.get("ingredient_names") or []),
                    "chunks": total_chunks,
                }
            )
            run_metadata_path.write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")
            continue

        chunks = catalog_chunks

        if args.dry_run:
            for chunk_index, chunk in enumerate(chunks, start=1):
                user_prompt = build_chunk_user_prompt(
                    archetype,
                    chunk_items=chunk,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                )
                prompt_path = run_dir / f"{uid}_chunk{chunk_index:02d}_prompt.txt"
                prompt_path.write_text(
                    f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n",
                    encoding="utf-8",
                )
            run_metadata["archetype_runs"].append(
                {
                    "uid": uid,
                    "name": name,
                    "status": "dry-run",
                    "chunks": total_chunks,
                }
            )
            run_metadata_path.write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")
            continue

        selected_names: set[str] = set()
        unmatched_names: set[str] = set()
        chunk_records: List[Dict[str, Any]] = []
        chunk_failed = False
        failure_reason: Optional[str] = None
        failure_chunk: Optional[int] = None

        for chunk_index, chunk in enumerate(chunks, start=1):
            user_prompt = build_chunk_user_prompt(
                archetype,
                chunk_items=chunk,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
            )

            prompt_path = run_dir / f"{uid}_chunk{chunk_index:02d}_prompt.txt"
            prompt_path.write_text(
                f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n",
                encoding="utf-8",
            )

            raw_path = run_dir / f"{uid}_chunk{chunk_index:02d}_raw.txt"
            try:
                raw_text = call_openai_api(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=args.model,
                    temperature=args.temperature,
                    top_p=args.top_p,
                    max_tokens=2000,
                    max_output_tokens=args.max_output_tokens,
                    reasoning_effort=args.reasoning_effort,
                )
            except OpenAIClientError as exc:
                failure_reason = str(exc)
                failure_chunk = chunk_index
                error_path = run_dir / f"{uid}_chunk{chunk_index:02d}_error.txt"
                error_path.write_text(f"API error: {exc}", encoding="utf-8")
                chunk_records.append(
                    {
                        "chunk": chunk_index,
                        "size": len(chunk),
                        "status": "error",
                        "error": str(exc),
                        "prompt_path": prompt_path.name,
                        "error_path": error_path.name,
                    }
                )
                chunk_failed = True
                break

            raw_path.write_text(raw_text, encoding="utf-8")

            try:
                parsed_payload = parse_model_payload(raw_text)
            except Exception as exc:
                failure_reason = str(exc)
                failure_chunk = chunk_index
                error_path = run_dir / f"{uid}_chunk{chunk_index:02d}_error.txt"
                error_path.write_text(str(exc), encoding="utf-8")
                chunk_records.append(
                    {
                        "chunk": chunk_index,
                        "size": len(chunk),
                        "status": "error",
                        "error": str(exc),
                        "prompt_path": prompt_path.name,
                        "raw_path": raw_path.name,
                        "error_path": error_path.name,
                    }
                )
                chunk_failed = True
                print(f"  ! Failed to parse response for {uid} chunk {chunk_index}: {exc}")
                break

            chunk_names, chunk_unmatched = _normalize_name(
                parsed_payload.get("selected_ingredients") or [],
                known_items=known_items,
            )
            selected_names.update(chunk_names)
            unmatched_names.update(chunk_unmatched)
            chunk_records.append(
                {
                    "chunk": chunk_index,
                    "size": len(chunk),
                    "status": "completed",
                    "selected": len(chunk_names),
                    "unmatched": chunk_unmatched,
                    "prompt_path": prompt_path.name,
                    "raw_path": raw_path.name,
                }
            )

        if chunk_failed:
            run_metadata["archetype_runs"].append(
                {
                    "uid": uid,
                    "name": name,
                    "status": "error",
                    "failed_chunk": failure_chunk,
                    "error": failure_reason,
                    "chunks": chunk_records,
                }
            )
            run_metadata_path.write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")
            continue

        normalized = sorted(selected_names, key=lambda item: item.lower())
        unmatched = sorted(unmatched_names, key=lambda item: item.lower())
        ingredient_records = [
            {
                "core_item_name": name,
                "item_type": known_items[name.lower()].item_type,
            }
            for name in normalized
        ]

        entry = {
            "uid": uid,
            "name": name,
            "description": archetype.get("description"),
            "core_tags": archetype.get("core_tags"),
            "ingredient_names": normalized,
            "ingredients": ingredient_records,
            "unmatched_suggestions": unmatched,
        }
        aggregated_results.append(entry)
        combined_entries[uid] = entry

        write_tracking_data(
            args.predefined_dir,
            combined_entries=combined_entries,
            metadata=tracking_metadata,
        )

        run_metadata["archetype_runs"].append(
            {
                "uid": uid,
                "name": name,
                "status": "completed",
                "chunks": chunk_records,
                "return_count": len(normalized),
                "unmatched": unmatched,
            }
        )
        run_metadata_path.write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")

    aggregated_payload = {
        "generated_at": run_metadata["generated_at"],
        "model": args.model,
        "tags_version": tags_version,
        "predefined_scope": scope,
        "core_item_catalog": str(args.core_items),
        "archetype_ingredient_sets": aggregated_results,
    }

    summary_path = run_dir / "curated_ingredients.json"
    summary_path.write_text(json.dumps(aggregated_payload, indent=2), encoding="utf-8")

    metadata_path = run_dir / "run_metadata.json"
    metadata_path.write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")

    if args.dry_run:
        print(f"[dry-run] Prompts written under {run_dir}")
        return

    print(f"Completed ingredient curation for {len(aggregated_results)} archetype(s). Output: {summary_path}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except (FileNotFoundError, ValueError, OpenAIClientError) as exc:
        raise SystemExit(f"Error: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
