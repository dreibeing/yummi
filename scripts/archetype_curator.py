#!/usr/bin/env python3
"""Curate generated archetypes to maximize coverage without destructive edits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional

from llm_utils import OpenAIClientError, call_openai_api


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(_read_text(path))


def scrub_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()


def parse_curation_payload(text: str) -> Dict[str, Any]:
    data = json.loads(scrub_json(text))
    if "recommendations" not in data:
        raise ValueError("Curator response missing 'recommendations' field.")
    return data


def build_system_prompt() -> str:
    return dedent(
        """
        You are Yummi's archetype curation expert. Your job is to review the full set of generated archetypes,
        identify overlaps, highlight missing coverage, and recommend non-destructive improvements.
        Always return strict JSON matching the requested schema. Do not delete archetypes outright; if something
        must change, mark it as `modify` or `replace` and explain why.
        """
    ).strip()


def build_user_prompt(
    *,
    constraint_brief: str,
    archetypes: List[Dict[str, Any]],
    tags_version: str,
    scope_block: str = "",
) -> str:
    dataset_json = json.dumps(archetypes, indent=2)
    return dedent(
        f"""
        Constraint Brief (abridged):
        {constraint_brief}

        {scope_block}

        Current archetype dataset (tags_version {tags_version}):
        {dataset_json}

        Task:
        1. Assess coverage vs. the brief and highlight overlaps, gaps, and any inconsistent tag usage.
        2. Recommend actions for every archetype: `keep` (as-is), `modify` (provide concrete adjustments), or `replace` (suggest a better-fit archetype concept while preserving the slot count).
        3. Identify conceptual areas that are missing entirely and suggest new archetype directions if needed.

        Output strict JSON following this schema:
        {{
          "recommendations": [
            {{
              "uid": "arch_XXXX",
              "name": "Existing Archetype Name",
              "action": "keep|modify|replace",
              "notes": "Short rationale referencing constraint brief requirements.",
              "suggested_changes": {{
                "core_tags": {{ "BudgetLevel": ["ValueStaples"], "...": ["..."] }},
                "description": "If action==modify, give revised description guidance."
              }},
              "replacement_proposal": {{
                "name": "Only when action=='replace'",
                "concept_summary": "Outline the new archetype's intent + tags to cover missing space."
              }}
            }}
          ],
          "overlap_clusters": [
            {{
              "uids": ["arch_A", "arch_B"],
              "reason": "Both target Vegan Balanced weeknight bowls."
            }}
          ],
          "missing_areas": [
            "ValueStaples pescatarian meals for Families",
            "LuxuryExperience ExperimentalFusion for Entertaining"
          ],
          "summary": {{
            "keep": 0,
            "modify": 0,
            "replace": 0,
            "notes": "High-level observations + next steps."
          }}
        }}

        Reminder: Be kind but thorough—flag redundancies, conflicting tags, or missing openness/budget tiers. Maintain the original archetype set size; replacements merely propose better coverage for a slot.
        """
    ).strip()


def ensure_output_dir(run_dir: Path) -> Path:
    out_dir = run_dir / "curation"
    out_dir.mkdir(exist_ok=True, parents=True)
    return out_dir


def run(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    aggregated_path = run_dir / "archetypes_aggregated.json"
    aggregated_data = _read_json(aggregated_path)
    archetypes: List[Dict[str, Any]] = aggregated_data.get("archetypes") or []
    if not archetypes:
        raise ValueError(f"No archetypes found in {aggregated_path}")
    tags_version = aggregated_data.get("tags_version", "unknown")
    constraint_brief = _read_text(Path(args.constraint_brief))

    # Build optional scope block for curator prompt
    scope = aggregated_data.get("predefined_scope") or {}
    diets = scope.get("dietary_restrictions") or []
    audiences = scope.get("audience") or []
    scope_lines: List[str] = []
    if diets or audiences:
        scope_lines.append("Scope (enforcement):")
        if diets:
            scope_lines.append("- Every archetype MUST include one of Diet/DietaryRestrictions: " + ", ".join(diets))
        if audiences:
            scope_lines.append("- Every archetype MUST include one of Audience: " + ", ".join(audiences))
        scope_lines.append("Reject proposals that violate scope; propose modify/replace instead.")
    scope_block = "\n".join(scope_lines)

    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        constraint_brief=constraint_brief,
        archetypes=archetypes,
        tags_version=tags_version,
        scope_block=scope_block,
    )

    if args.dry_run:
        preview = ensure_output_dir(run_dir) / "curation_prompt.txt"
        preview.write_text(f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n", encoding="utf-8")
        print(f"[dry-run] Wrote curator prompt to {preview}")
        return

    raw_text = call_openai_api(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=args.model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        max_output_tokens=args.max_output_tokens,
        reasoning_effort=args.reasoning_effort,
    )

    out_dir = ensure_output_dir(run_dir)
    (out_dir / "curation_raw.txt").write_text(raw_text, encoding="utf-8")

    parsed = parse_curation_payload(raw_text)
    rec_path = out_dir / "curation_recommendations.json"
    rec_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    print(f"Stored curated recommendations at {rec_path}")

    # Materialize a final curated list if the user wants an immediate artifact.
    curated_data = dict(aggregated_data)
    curated_data["curation_notes"] = parsed
    final_path = out_dir / "archetypes_curated.json"
    final_path.write_text(json.dumps(curated_data, indent=2), encoding="utf-8")
    print(f"Saved curated dataset with notes to {final_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Curate archetypes for coverage and diversity.")
    parser.add_argument("--run-dir", required=True, help="Path to a single archetype run directory (containing archetypes_aggregated.json).")
    parser.add_argument("--constraint-brief", default="data/tags/archetype_constraint_brief.md", help="Path to the constraint brief used for the run.")
    parser.add_argument("--model", default="gpt-5", help="Model to use for curation (default: gpt-5).")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature.")
    parser.add_argument("--top-p", type=float, default=1.0, help="Nucleus sampling parameter.")
    parser.add_argument("--max-tokens", type=int, default=2000, help="max_tokens for non-reasoning models (ignored by GPT-5).")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=6000,
        help="max_output_tokens for GPT-5 Responses API.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default="high",
        help="Reasoning effort hint for GPT-5.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip API call and write the rendered prompt instead.")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except (OpenAIClientError, FileNotFoundError, ValueError) as exc:  # pragma: no cover - CLI surface
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
