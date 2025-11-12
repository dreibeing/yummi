# Archetype Constraint Brief (Step 2)

## Purpose
Summarize the non-negotiable coverage expectations for the archetype generation prompt so that every downstream hard filter (diet, allergen, household, prep speed, heat tolerance, ethics) is represented before we create the 20–30 archetypes.

## Global Targets
- **Archetype count**: 24–28 distinct entries to balance coverage with manageability.
- **Tags version**: `2025.02.0` from `data/tags/defined_tags.json`.
- **Context sources**: `yummi_business_logic_requirements.txt`, `thisproject.md`, PayFast thin-slice scope notes, and controlled vocabulary manifest.

## Required Category Coverage
| Category | Non-negotiable coverage expectations |
|----------|--------------------------------------|
| Diet | Ensure at least one archetype each for Omnivore, Flexitarian, Vegetarian, Vegan, Pescatarian, PoultryOnly, LowCarb, Keto, GlutenAware, HighProtein. Mix-and-match where sensible (e.g., Vegan + HighProtein). |
| Cuisine & Openness | Cover SouthAfrican staples, ModernAmerican comfort, Mediterranean, Indian, MiddleEastern, LatinAmerican, NorthAfrican, SoutheastAsian, EastAsian. Split archetypes across Familiar, RegionalTwist, GlobalExplorer, ExperimentalFusion openness bands so users can self-select adventure level. |
| PrepTime | Maintain a roughly even split across Under15, 15-30, 30-45, 45Plus to support weeknight rush vs. weekend projects. |
| Complexity | Guarantee at least 6 archetypes per complexity tier (Simple, Intermediate, Advanced, Showstopper) to map to skill/confidence. |
| Heat/Spice | Reserve archetypes tuned for NoHeat (kids/sensitive), Mild, Medium, Hot, ExtraHot so the runtime can filter without re-ranking. |
| Audience | Span Solo, Couple, Family, MealPrep, Entertaining contexts since household sizing drives ingredient economics. |
| BudgetLevel | Provide at least five spend tiers (ValueStaples, Affordable, Balanced, PremiumOccasion, LuxuryExperience). Ensure every tier shows up multiple times so price-sensitive users have genuine choice. |
| Mainstream Baseline | Include several broad, high-appeal archetypes for "normal" households: Omnivore/Vegetarian family weeknights with Balanced/Affordable budgets, Mild heat, 15–30 min, Simple complexity, and SouthAfrican/ModernAmerican cuisines. |
| Allergens | Every archetype must state which of the top allergens (Gluten, Dairy, Egg, Soy, Peanut, TreeNut, Fish, Shellfish, Sesame, Mustard) it avoids or tolerates. At minimum, create dedicated allergen-safe archetypes for Gluten-free, Dairy-free, Nut-free, Shellfish-free cohorts. |
| Ethics/Religious | Provide at least one Halal, one Kosher-style, and one Jain-friendly archetype; optional sustainability tags can layer on. |

## Constraint Notes
- **Diet + Audience interplay**: Ensure family/meal-prep archetypes include at least one plant-forward option so cost- and leftovers-driven households aren’t forced into meat-heavy plans.
- **Heat tolerance**: Kids/comfort archetypes should cap at Mild; adventurous explorers should explicitly opt into Hot/ExtraHot to prevent accidental matches.
- **Allergen guardrails**: When an archetype claims an allergen-safe posture, the downstream meal generation must restrict canonical ingredients accordingly. Flag these archetypes for QA priority.
- **Ethics layering**: Halal/Kosher/Jain tags always pair with relevant diet types (e.g., Jain-friendly archetype also Vegan + No root vegetables) to keep messaging honest.
- **PrepTime vs Complexity**: Long prep doesn’t always mean difficult—include archetypes that are SlowCooker/SheetPan Easy but >45 min hands-off, versus high-touch 30-min advanced dishes.
- **Budget clarity**: Budget tags must reflect the real ingredient expectations for Woolworths shoppers (ValueStaples = pantry legumes/rice, LuxuryExperience = specialty imports). No archetype should be missing a spend signal.

## Suggested Archetype Grid (example targets)
- **Weeknight Core (Simple, Under15/15-30, Mild)**: Omnivore Familiar, Flexitarian RegionalTwist, GlutenAware Family.
- **Plant-Forward Variety**: Vegan GlobalExplorer (Medium heat), Vegetarian Familiar (NoHeat), HighProtein Vegan MealPrep.
- **Protein Specialists**: Pescatarian Mediterranean (Medium heat), PoultryOnly SouthAfrican (Mild), Beef/Lamb LatinAmerican (Hot) for adventurous eaters.
- **Cultural Explorers**: NorthAfrican ExperimentalFusion (Medium), SoutheastAsian GlobalExplorer (Hot), MiddleEastern RegionalTwist (Mild).
- **Special Diets**: Keto Entertaining (Showstopper), LowCarb Couple (Intermediate), Halal Family (Simple), Kosher-style MealPrep (Intermediate), Jain-friendly Weeknight (Simple, NoHeat).
- **Occasion/Format Anchors**: ComfortFood WeekendProject (Showstopper), MealPrep SheetPan (Simple, 45Plus), AirFry Quick Bites (Simple, Under15).

Use this brief verbatim (or lightly edited) inside Prompt Stage 0 so the LLM understands the coverage contract before emitting archetype JSON.
