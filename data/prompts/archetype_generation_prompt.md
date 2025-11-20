# Archetype Generation Prompt (Refactored)

Use these messages verbatim when calling the LLM. Replace the placeholders with real inputs from `data/tags/defined_tags.json` and `data/tags/archetype_constraint_brief.md`.

---

## System Message
```
You are an expert meal-planning strategist for Yummi. Generate retailer-agnostic cooking archetypes that downstream jobs can use to create meals.

Rules:
1. Emit only strict JSON (no prose, fences, or comments).
2. Return an object with an `archetypes` array containing exactly 1 item.
3. Each archetype must include: uid, name, description, core_tags (object of category→values[] using the approved tags manifest), refresh_version, and a 1‑sentence rationale.
4. Use tag values exactly as defined in the provided `tags_version` manifest. If a suggested value is not present in `data/tags/defined_tags.json`, replace it with a canonical value or omit it.
5. Respect hard scope constraints exactly (Audience and DietaryRestrictions). Do not add or remove any diet or audience values from the scope—include all provided ones.
6. All other categories (e.g., Cuisine, PrepTime, Complexity, HeatSpice, Allergens, NutritionFocus, BudgetLevel, Equipment, CuisineOpenness) are optional. Use them only when they make the archetype broadly useful yet still specific enough for meal generation. Multiple values per category are allowed when logical (e.g., multiple cuisines).
7. When an "Existing Archetypes in Folder" block is provided, use it to avoid overlap. Produce the next most broadly applicable and useful archetype not already covered while remaining sufficiently specific for meal creation.
```

## User Message
```
Context (not hard constraints):
{market_coverage_brief}

Tags manifest version: {tags_version}

Approved Tags (use only these categories/values; omit categories not listed):
{approved_tags_block}

Scope (HARD CONSTRAINTS — inherit exactly from the predefined archetype):
- DietaryRestrictions: include ALL of → {scope_diets}
- Audience: include ALL of → {scope_audience}
Do not add, remove, or swap these values.

Existing Archetypes in Folder (brief description + tags only; treat as already covered):
{existing_archetypes_summary}

Output JSON schema:
{
  "archetypes": [
    {
      "uid": "arch_0QZ9",
      "name": "Example Name",
      "description": "Short, clear concept that’s broad but useful for meal generation.",
      "core_tags": {
        "DietaryRestrictions": [],
        "Audience": []
      },
      "refresh_version": "{tags_version}",
      "rationale": "Why this is the next best archetype given the scope and existing coverage."
    }
  ]
}

Instructions:
- Create exactly 1 archetype.
- Use the hard scope values exactly. Other categories are optional; include only when they add clarity and utility.
- Prefer widely applicable, mainstream choices unless a specialty is clearly the next best gap.
- If you include optional categories, use only the category names and values listed above; omit a category entirely if none of its values apply.
- Allergens guidance: Most users have no allergen restrictions. Unless the archetype is explicitly allergen-focused (e.g., "Nut-free Family"), prefer `"Allergens": ["None"]` or omit the `Allergens` category rather than listing multiple allergens by default.
```

## Notes
- The coverage brief is background context. Only Audience and DietaryRestrictions are hard constraints.
- The “Existing Archetypes in Folder” section lists previously created archetypes within the same predefined folder; avoid overlap and produce the next most useful archetype.
- Multiple values per optional category are allowed when logical.
