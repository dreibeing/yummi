# Archetype Generation Prompt (Step 3)

Use these messages verbatim when calling the LLM (Gemini/GPT). Replace the placeholder braces with real inputs from `data/tags/defined_tags.json` and `data/tags/archetype_constraint_brief.md`.

---

## System Message
```
You are an expert meal-planning strategist for Yummi. Generate retailer-agnostic cooking archetypes that honor the canonical contracts in `yummi_business_logic_requirements.txt`.

Rules:
1. Emit only strict JSON (no prose, fences, or comments).
2. Every archetype must include: uid, name, description, core_tags (object of category→values[] drawn from the approved tag manifest), diet_profile, allergen_flags[], heat_band, prep_time_minutes_range, complexity, audience_context, cuisine_openness, refresh_version, rationale (<=1 sentence).
3. Use tag values exactly as defined in the `tags_version` manifest. Reject or recompute any value not present in `data/tags/defined_tags.json`.
4. Guarantee coverage expectations from the constraint brief: span all required Diet, Cuisine, PrepTime, Complexity, HeatSpice, Audience, BudgetLevel, Ethics/Religious, and Allergen combinations; allocate at least one archetype per mandatory cohort (Halal, Kosher-style, Jain-friendly, Gluten-free, Dairy-free, Nut-free, Shellfish-free, Vegan, HighProtein, Keto, LowCarb, Family meal-prep, Solo quick bites, ValueStaples budget, LuxuryExperience hosting, etc.).
5. Keep archetypes retailer-agnostic—no SKU or specific ingredient mentions—yet ensure guardrails are realistic for Woolworths + modern South African households.
6. Target {archetype_count} archetypes; do not exceed 30.
7. Produce stable base36 `uid`s by hashing archetype names deterministically (e.g., `arch_${BASE36(MD5(name))[0..5]}`).
8. Ensure descriptions make it clear which audiences, diets, heat tolerances, and budget expectations each archetype serves so downstream matching and QA require no extra inference.
9. When provided with a “Prior Archetypes” block, treat it as reserved concepts. Do not repeat the same Diet×Cuisine×Audience×Budget×Heat×Prep×Complexity×Ethics combinations. Use it purely to avoid duplicates.
10. Prioritize mainstream, broad-appeal archetypes first (Family weeknights; Omnivore/Vegetarian; Balanced/Affordable; Mild; 15–30; Simple; SA/ModernAmerican). Limit specialty/ethics-first archetypes to ≤20% of the total.
```

## User Message
```
Market Coverage Brief:
{market_coverage_brief}

Constraint Highlights:
- Tags version: {tags_version}
- Required categories (archetype-level): {required_categories_archetype}
- Allergen focus: create explicit safe archetypes for Gluten, Dairy, Egg, Soy, Peanut, TreeNut, Fish, Shellfish, Sesame, Mustard avoidance cohorts.
- Ethics & religious: provide at least one Halal, one Kosher-style, and one Jain-friendly archetype; note any additional sustainability/animal-welfare lenses.
- Household contexts: evenly split across Solo, Couple, Family, MealPrep, Entertaining.
- Budget tiers: ValueStaples, Affordable, Balanced, PremiumOccasion, LuxuryExperience. Align each archetype’s tone and description to the declared tier.
- Cuisine openness bands: FamiliarClassics, RegionalTwist, GlobalExplorer, ExperimentalFusion.
- If an “Existing Archetypes Summary” block follows, it will be compact, keywords-only lines (no descriptions). Use it as an exclusion guide: do not generate archetypes with materially equivalent combinations. Prefer mainstream, broad-appeal archetypes (e.g., Family weeknight, Omnivore/Vegetarian, Mild heat, 15–30 min, Simple, Balanced/Affordable budgets) unless the coverage brief explicitly asks for specialties.

Output JSON schema:
{
  "archetypes": [
    {
      "uid": "arch_0QZ9",
      "name": "Plant-Powered Weeknight Flex",
      "description": "Two-pan dinners using pantry veg + legumes for busy families...",
      "core_tags": {
        "Diet": ["Vegetarian","HighProtein"],
        "Cuisine": ["SouthAfrican","Mediterranean"],
        "CuisineOpenness": ["RegionalTwist"],
        "PrepTime": ["15to30"],
        "Complexity": ["Simple"],
        "HeatSpice": ["Mild"],
        "Audience": ["Family"],
        "BudgetLevel": ["Balanced"],
        "EthicsReligious": ["Halal"],
        "Allergens": ["Dairy"]
      },
      "diet_profile": {
        "allowed": ["Vegetarian"],
        "restricted": ["Beef","Shellfish"],
        "notes": "Keeps protein from legumes, eggs optional"
      },
      "allergen_flags": {
        "avoids": ["Shellfish","Peanut"],
        "contains": ["Dairy"],
        "cross_contact_notes": "Dairy optional; default recipes omit"
      },
      "heat_band": "Mild",
      "prep_time_minutes_range": [15,30],
      "complexity": "Simple",
      "audience_context": "Family",
      "cuisine_openness": "RegionalTwist",
      "refresh_version": "2025.02.0",
      "rationale": "Balances vegetarian comfort dishes with pantry staples for school-night dinners."
    }
  ]
}

Instructions:
- Fill the array with {archetype_count} entries.
- Refresh version should match the manifest (e.g., `2025.02.0`).
- Provide rationales referencing how each archetype meets the coverage brief.
```

## Notes
- `market_coverage_brief` pulls from `data/tags/archetype_constraint_brief.md` plus any retailer or seasonal notes you add.
- `required_categories_archetype` should be injected as a comma-separated list (Diet, Cuisine, CuisineOpenness, Complexity, PrepTime, HeatSpice, Allergens, Audience, BudgetLevel).
- Keep this template in git so every archetype build is reproducible; update the `refresh_version` and coverage brief when tags evolve.
