#!/usr/bin/env python3
"""Run the archetype generation prompt (single shot or batches) and persist output."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional

from llm_utils import call_openai_api, OpenAIClientError


DEFAULT_PROMPT_PATH = Path("data/prompts/archetype_generation_prompt.md")
DEFAULT_CONSTRAINT_PATH = Path("data/tags/archetype_constraint_brief.md")
DEFAULT_TAGS_PATH = Path("data/tags/defined_tags.json")
DEFAULT_OUTPUT_DIR = Path("data/archetypes")
SNAPSHOT_FILENAME = "archetypes_so_far.json"


@dataclass
class PromptTemplate:
    system: str
    user: str


class PromptTemplateError(RuntimeError):
    pass


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def _extract_block(lines: List[str], header: str) -> str:
    header_lower = header.lower().strip()
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.strip().lower() == header_lower)
    except StopIteration as exc:  # pragma: no cover - defensive
        raise PromptTemplateError(f"Could not find section '{header}'.") from exc

    idx = start_idx + 1
    while idx < len(lines) and not lines[idx].strip().startswith("```"):
        idx += 1
    if idx >= len(lines):
        raise PromptTemplateError(f"Missing code fence for '{header}'.")

    fence = lines[idx].strip()
    idx += 1
    block: List[str] = []
    while idx < len(lines) and lines[idx].strip() != fence:
        block.append(lines[idx])
        idx += 1
    if idx >= len(lines):
        raise PromptTemplateError(f"Unterminated code fence in '{header}'.")
    return "".join(block).strip()


def load_prompt_template(path: Path) -> PromptTemplate:
    text = _read_file(path)
    lines = text.splitlines(keepends=True)
    system = _extract_block(lines, "## System Message")
    user = _extract_block(lines, "## User Message")
    return PromptTemplate(system=system, user=user)


def scrub_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def parse_response_payload(payload_text: str) -> Dict[str, Any]:
    body = scrub_json_text(payload_text)
    data = json.loads(body)
    if "archetypes" not in data or not isinstance(data["archetypes"], list):
        raise ValueError("Response missing 'archetypes' list.")
    return data


def _fill_placeholders(template: str, values: Dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


def render_user_prompt(
    template: str,
    *,
    market_brief: str,
    tags_version: str,
    scope_diets: List[str] | None,
    scope_audience: List[str] | None,
    approved_tags_block: str,
    existing_archetypes_summary: str,
) -> str:
    replacements = {
        "market_coverage_brief": market_brief.strip(),
        "tags_version": tags_version,
        "scope_diets": ", ".join(scope_diets or []),
        "scope_audience": ", ".join(scope_audience or []),
        "approved_tags_block": approved_tags_block.strip(),
        "existing_archetypes_summary": existing_archetypes_summary.strip(),
    }
    return _fill_placeholders(template, replacements)


def render_system_prompt(template: str, *, archetype_count: int) -> str:
    # We still accept archetype_count placeholder for compatibility, but the refactor
    # always generates exactly 1 archetype per call.
    return _fill_placeholders(template, {"archetype_count": archetype_count})


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_output_dir(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / f"run_{timestamp_slug()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_constraint_brief(path: Path) -> str:
    return dedent(_read_file(path)).strip()


def load_archetype_snapshot(run_dir: Path) -> List[Dict[str, Any]]:
    snapshot_path = run_dir / SNAPSHOT_FILENAME
    if not snapshot_path.exists():
        return []
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    archetypes = data.get("archetypes")
    return archetypes if isinstance(archetypes, list) else []


def write_archetype_snapshot(run_dir: Path, archetypes: List[Dict[str, Any]], tags_version: str) -> None:
    snapshot_path = run_dir / SNAPSHOT_FILENAME
    payload = {"tags_version": tags_version, "archetypes": archetypes}
    save_json(snapshot_path, payload)


def summarize_archetypes(archetypes: List[Dict[str, Any]], limit: int | None = None) -> str:
    """Return a compact, two-line summary per archetype: brief description + tags.

    - Line 1: <n>. <name> — <short description>
    - Line 2: tags: <Category1>=v1,v2; <Category2>=v1; ... (only categories present)
    If limit is provided (>0), include only the most recent `limit` entries.
    """
    items = archetypes
    if limit is not None and limit > 0 and len(archetypes) > limit:
        items = archetypes[-limit:]
    lines: List[str] = []
    for idx, a in enumerate(items, start=1):
        name = (a.get("name") or "Unknown").strip()
        desc = (a.get("description") or "").strip()
        desc_short = desc if len(desc) <= 120 else (desc[:117] + "...")
        lines.append(f"{idx}. {name} — {desc_short}")
        tags = a.get("core_tags") or {}
        tag_parts: List[str] = []
        for cat, values in tags.items():
            vals = ",".join(list(values)[:6]) if isinstance(values, list) else str(values)
            tag_parts.append(f"{cat}={vals}")
        lines.append("tags: " + "; ".join(tag_parts))
    return "\n".join(lines)


def _gather_existing_archetypes_in_folder(predefined_base: Path) -> List[Dict[str, Any]]:
    """Scan all run_* subfolders under a predefined folder and collect archetypes.

    Deduplicate by uid (keep first occurrence) and return in chronological order.
    """
    collected: List[Dict[str, Any]] = []
    seen: set[str] = set()
    if not predefined_base.exists():
        return collected
    for run_dir in sorted(predefined_base.glob("run_*") ):
        agg = run_dir / "archetypes_aggregated.json"
        snap = run_dir / SNAPSHOT_FILENAME
        payload = None
        if agg.exists():
            try:
                payload = json.loads(agg.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        elif snap.exists():
            try:
                payload = json.loads(snap.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        if not payload:
            continue
        for a in payload.get("archetypes", []) or []:
            uid = str(a.get("uid") or "").strip()
            if uid and uid not in seen:
                seen.add(uid)
                collected.append(a)
    return collected


def build_tag_catalog(tags_manifest: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    catalog: Dict[str, List[Dict[str, str]]] = {}
    for entry in tags_manifest.get("defined_tags", []) or []:
        category = entry.get("category")
        value = entry.get("value")
        if not category or not value:
            continue
        description = str(entry.get("description") or "").strip()
        bucket = catalog.setdefault(str(category), [])
        if all(item["value"] != value for item in bucket):
            bucket.append({"value": str(value), "description": description})
    for category in catalog:
        catalog[category].sort(key=lambda item: item["value"].lower())
    return catalog


def format_approved_tags_block(catalog: Dict[str, List[Dict[str, str]]]) -> str:
    lines: List[str] = []
    for category in sorted(catalog):
        items = catalog[category]
        if not items:
            continue
        lines.append(f"{category}:")
        for item in items:
            value = item["value"]
            description = item.get("description") or ""
            if description:
                lines.append(f"  - {value}: {description}")
            else:
                lines.append(f"  - {value}")
    return "\n".join(lines)


def validate_archetype_core_tags(
    archetypes: List[Dict[str, Any]],
    catalog: Dict[str, List[Dict[str, str]]],
    scope_diets: List[str] | None,
    scope_audience: List[str] | None,
) -> None:
    catalog_sets: Dict[str, set[str]] = {
        category: {item["value"] for item in items}
        for category, items in catalog.items()
    }
    for idx, archetype in enumerate(archetypes, start=1):
        uid = archetype.get("uid") or f"index {idx}"
        core_tags = archetype.get("core_tags") or {}
        for category, values in core_tags.items():
            if category not in catalog_sets:
                raise ValueError(
                    f"Archetype {uid} references unknown category '{category}'. Update defined_tags.json or adjust the model output."
                )
            allowed_values = catalog_sets[category]
            for value in values or []:
                if value not in allowed_values:
                    raise ValueError(
                        f"Archetype {uid} uses value '{value}' for category '{category}', which is not in defined_tags.json."
                    )

        if scope_diets:
            archetype_diets = set(core_tags.get("DietaryRestrictions") or [])
            if archetype_diets != set(scope_diets):
                raise ValueError(
                    f"Archetype {uid} must include DietaryRestrictions values {scope_diets} exactly, but got {sorted(archetype_diets)}."
                )
        if scope_audience:
            archetype_audience = set(core_tags.get("Audience") or [])
            if archetype_audience != set(scope_audience):
                raise ValueError(
                    f"Archetype {uid} must include Audience values {scope_audience} exactly, but got {sorted(archetype_audience)}."
                )


def _slugify(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _compute_predefined_slug(diets: List[str] | None, audiences: List[str] | None) -> str:
    diets_slug = "-".join(_slugify(v) for v in (diets or ["any"])) or "any"
    aud_slug = "-".join(_slugify(v) for v in (audiences or ["any"])) or "any"
    return f"diet_{diets_slug}__aud_{aud_slug}"


def _build_scope_block(
    *,
    tags_manifest: Dict[str, Any],
    scope_diets: List[str] | None,
    scope_audiences: List[str] | None,
    required_sub_tags: Dict[str, List[str]] | None,
) -> str:
    """Legacy scope block (kept for compatibility in curator flows).

    The refactored prompt now embeds scope directly into the user template; this
    function is retained to keep metadata/context parity if needed elsewhere.
    """
    if not scope_diets and not scope_audiences and not required_sub_tags:
        return ""

    lines: List[str] = []
    lines.append("\n\nScope (HARD CONSTRAINTS; exact inclusion):")
    if scope_diets:
        lines.append(
            "- DietaryRestrictions: include ALL of → " + ", ".join(sorted(scope_diets))
        )
    if scope_audiences:
        lines.append(
            "- Audience: include ALL of → " + ", ".join(sorted(scope_audiences))
        )
    if required_sub_tags:
        # Optional guidance within the scope
        lines.append("Optional coverage hints (within this scope):")
        for cat, values in required_sub_tags.items():
            if not values:
                continue
            lines.append(f"  • {cat}: consider coverage across {', '.join(sorted(set(values)))}")
    lines.append("Reject or revise any archetype that fails exact scope inclusion.")
    return "\n".join(lines) + "\n\n"


def run_batches(args: argparse.Namespace) -> None:
    tags_manifest = _load_json(Path(args.tags_manifest))
    tags_version: str = tags_manifest.get("tags_version", "unknown")
    tag_catalog = build_tag_catalog(tags_manifest)
    if not tag_catalog:
        raise RuntimeError("defined_tags.json did not yield any tag categories/values.")
    approved_tags_block = format_approved_tags_block(tag_catalog)

    prompt = load_prompt_template(Path(args.prompt_template))
    market_brief = load_constraint_brief(Path(args.constraint_brief))

    # Resolve scope from predefined config (if provided) or CLI flags
    predefined_config: Optional[Dict[str, Any]] = None
    scope_diets: List[str] | None = None
    scope_audiences: List[str] | None = None
    required_sub_tags: Dict[str, List[str]] | None = None

    if args.predefined_config:
        cfg_path = Path(args.predefined_config)
        # Allow passing the predefined folder directly; resolve to config.json
        if cfg_path.is_dir():
            candidate = cfg_path / "config.json"
            if candidate.exists():
                cfg_path = candidate
        predefined_config = _load_json(cfg_path)
        hard = predefined_config.get("hard_constraints") or {}
        scope_diets = list(hard.get("DietaryRestrictions") or hard.get("Diet") or []) or None
        scope_audiences = list(hard.get("Audience") or []) or None
        rst = predefined_config.get("required_subarchetype_tags") or {}
        # Normalize to list[str]
        required_sub_tags = {
            str(k): list(v) if isinstance(v, list) else ([str(v)] if v else [])
            for k, v in rst.items()
        } or None

    if args.scope_dietary:
        scope_diets = list(args.scope_dietary)
    if args.scope_audience:
        scope_audiences = list(args.scope_audience)

    total = args.archetype_count

    # Compute output dir; if predefined scope present and output-dir equals default base, nest under a slug
    base_output = Path(args.output_dir)
    predefined_slug: Optional[str] = None
    if predefined_config:
        predefined_slug = predefined_config.get("predefined_uid") or None
    if (predefined_slug or scope_diets or scope_audiences) and (base_output == DEFAULT_OUTPUT_DIR):
        slug = predefined_slug or _compute_predefined_slug(scope_diets or [], scope_audiences or [])
        base_output = base_output / "predefined" / slug
    output_dir = ensure_output_dir(base_output)
    # Support resumable context by loading any previously written snapshot.
    all_archetypes: List[Dict[str, Any]] = load_archetype_snapshot(output_dir)
    metadata = {
        "model": args.model,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "max_output_tokens": args.max_output_tokens,
        "reasoning_effort": args.reasoning_effort,
        "tags_version": tags_version,
        "prompt_template": str(Path(args.prompt_template)),
        "constraint_brief": str(Path(args.constraint_brief)),
        "batches": [],
        "total_requested": total,
    }

    # Collect existing archetypes across the predefined folder (all runs) and include current-run snapshot
    folder_existing: List[Dict[str, Any]] = []
    # If base_output ends with the slug (predefined/<slug>), use that folder to scan
    if base_output.name and base_output.parent.name == "predefined":
        folder_existing = _gather_existing_archetypes_in_folder(base_output)

    for idx in range(1, total + 1):
        system_prompt = render_system_prompt(prompt.system, archetype_count=1)
        # Build existing summary: combine folder history with current run so far
        prior_archetypes = (folder_existing or []) + (load_archetype_snapshot(output_dir) or all_archetypes)
        existing_summary = summarize_archetypes(
            prior_archetypes,
            None if args.context_summary_max == 0 else args.context_summary_max,
        )
        if not existing_summary:
            existing_summary = "None yet."

        user_prompt = render_user_prompt(
            prompt.user,
            market_brief=market_brief,
            tags_version=tags_version,
            scope_diets=scope_diets or [],
            scope_audience=scope_audiences or [],
            approved_tags_block=approved_tags_block,
            existing_archetypes_summary=existing_summary,
        )

        print(f"Generating archetype {idx}/{total}...")
        if args.dry_run:
            preview_path = output_dir / f"call_{idx:02d}_prompt.txt"
            preview_path.write_text(
                f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n",
                encoding="utf-8",
            )
            print(f"[dry-run] Wrote prompt preview to {preview_path}")
            continue

        raw_text = call_openai_api(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=args.model,
            temperature=args.temperature,
            top_p=args.top_p,
            max_tokens=args.max_tokens,
            reasoning_effort=args.reasoning_effort,
            max_output_tokens=args.max_output_tokens,
        )
        raw_file = output_dir / f"call_{idx:02d}_raw.txt"
        raw_file.write_text(raw_text, encoding="utf-8")

        parsed = parse_response_payload(raw_text)
        archetypes = parsed.get("archetypes", [])
        validate_archetype_core_tags(archetypes, tag_catalog, scope_diets, scope_audiences)
        all_archetypes.extend(archetypes)
        write_archetype_snapshot(output_dir, all_archetypes, tags_version)
        metadata.setdefault("batches", []).append(
            {
                "batch": idx,
                "requested_count": 1,
                "received_count": len(archetypes),
                "raw_file": raw_file.name,
            }
        )
        print(f"Call {idx}: received {len(archetypes)} archetype(s)")

    if args.dry_run:
        print(f"Dry run complete. Prompts saved under {output_dir}")
        return

    output_payload = {"archetypes": all_archetypes, "tags_version": tags_version}
    if predefined_config or scope_diets or scope_audiences:
        output_payload["predefined_scope"] = {
            "dietary_restrictions": scope_diets or [],
            "audience": scope_audiences or [],
        }
    aggregated_file = output_dir / "archetypes_aggregated.json"
    save_json(aggregated_file, output_payload)
    # Persist run metadata with scope for traceability
    metadata.update(
        {
            "scope": {
                "dietary_restrictions": scope_diets or [],
                "audience": scope_audiences or [],
            },
            "predefined_config": str(Path(args.predefined_config)) if args.predefined_config else None,
            "output_dir": str(base_output),
        }
    )
    save_json(output_dir / "run_metadata.json", metadata)

    print(f"Saved {len(all_archetypes)} archetypes to {aggregated_file}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM runner for archetype generation prompt.")
    parser.add_argument("--prompt-template", default=str(DEFAULT_PROMPT_PATH), help="Path to prompt template markdown.")
    parser.add_argument("--constraint-brief", default=str(DEFAULT_CONSTRAINT_PATH), help="Path to coverage brief injected into the prompt.")
    parser.add_argument("--tags-manifest", default=str(DEFAULT_TAGS_PATH), help="Path to defined_tags manifest.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for run artifacts.")
    parser.add_argument(
        "--predefined-config",
        default=None,
        help="Path to predefined archetype config.json, or pass the predefined folder to auto-detect config.json.",
    )
    parser.add_argument(
        "--scope-dietary",
        action="append",
        default=None,
        help="Hard scope: one or more Diet/DietaryRestrictions values (can repeat).",
    )
    parser.add_argument(
        "--scope-audience",
        action="append",
        default=None,
        help="Hard scope: one or more Audience values (can repeat).",
    )
    parser.add_argument("--archetype-count", type=int, default=5, help="How many archetypes to create (one per call).")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), help="Chat/response model to call.")
    parser.add_argument("--temperature", type=float, default=0.4, help="Sampling temperature for the LLM call.")
    parser.add_argument("--top-p", type=float, default=1.0, help="Nucleus sampling parameter (top_p).")
    parser.add_argument("--max-tokens", type=int, default=2000, help="max_tokens for chat completion responses.")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="max_output_tokens for reasoning models such as GPT-5.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default="low",
        help="Reasoning effort hint for GPT-5 models (default: low to reduce token usage).",
    )
    parser.add_argument(
        "--context-summary-max",
        type=int,
        default=0,
        help="Limit how many existing archetypes to include (0 = include all from the folder).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip API calls and just materialize the rendered prompts.")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_batches(args)
    except OpenAIClientError as exc:  # pragma: no cover - CLI surface
        parser.error(str(exc))
    except Exception as exc:  # pragma: no cover
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
