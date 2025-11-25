from __future__ import annotations

from dataclasses import dataclass

from app.schemas import CandidateFilterRequest
from app.services.filtering import generate_candidate_pool
from app.services.preferences import load_tag_manifest


@dataclass
class DummyProfile:
    selected_tags: dict
    disliked_tags: dict
    tags_version: str = "2025.02.1"


def _build_manifest() -> dict:
    return {
        "manifest_id": "test_manifest",
        "tags_version": "2025.02.1",
        "archetypes": [
            {
                "uid": "arch_a",
                "meals": [
                    {
                        "meal_id": "meal_vegan",
                        "name": "Vegan Bowl",
                        "description": "Plant focused bowl",
                        "meal_tags": {
                            "Diet": ["Vegan"],
                            "Allergens": [],
                            "HeatSpice": ["Mild"],
                            "PrepTime": ["15to30"],
                            "Complexity": ["Simple"],
                            "EthicsReligious": ["Halal"],
                        },
                        "final_ingredients": [
                            {
                                "selected_product": {
                                    "product_id": "111",
                                    "name": "Chickpeas",
                                    "sale_price": 32.5,
                                    "detail_url": "https://example.com/111",
                                }
                            }
                        ],
                        "metadata": {},
                    },
                    {
                        "meal_id": "meal_dairy",
                        "name": "Creamy Pasta",
                        "description": "Contains dairy",
                        "meal_tags": {
                            "Diet": ["Omnivore"],
                            "Allergens": ["Dairy"],
                            "HeatSpice": ["Medium"],
                            "PrepTime": ["30to45"],
                            "Complexity": ["Intermediate"],
                        },
                        "final_ingredients": [],
                        "metadata": {},
                    },
                    {
                        "meal_id": "meal_hot",
                        "name": "Spicy Curry",
                        "description": "Plenty of heat",
                        "meal_tags": {
                            "Diet": ["Omnivore"],
                            "Allergens": [],
                            "HeatSpice": ["Hot"],
                            "PrepTime": ["15to30"],
                            "Complexity": ["Simple"],
                        },
                        "final_ingredients": [],
                        "metadata": {},
                    },
                ],
            },
            {
                "uid": "arch_b",
                "meals": [
                    {
                        "meal_id": "meal_halal",
                        "name": "Halal Curry",
                        "description": "Tikka masala",
                        "meal_tags": {
                            "Diet": ["Omnivore"],
                            "Allergens": [],
                            "HeatSpice": ["Mild"],
                            "PrepTime": ["30to45"],
                            "Complexity": ["Simple"],
                            "EthicsReligious": ["Halal"],
                        },
                        "final_ingredients": [],
                        "metadata": {},
                    }
                ],
            },
        ],
    }


def test_generate_candidate_pool_filters_by_diet_and_allergens():
    manifest = _build_manifest()
    profile = DummyProfile(
        selected_tags={"Diet": ["diet_vegan"]},
        disliked_tags={"Allergens": ["allergen_dairy"]},
    )
    request = CandidateFilterRequest()
    result = generate_candidate_pool(
        manifest=manifest,
        tag_manifest=load_tag_manifest(),
        profile=profile,
        request=request,
        user_id="user_1",
    )

    assert result.totalCandidates == 1
    assert result.returnedCount == 1
    assert result.candidateMeals[0].mealId == "meal_vegan"
    candidate = result.candidateMeals[0]
    assert candidate.tags["Diet"] == ["Vegan"]
    assert candidate.skuSnapshot[0].productId == "111"


def test_generate_candidate_pool_respects_declined_ids_and_limit():
    manifest = _build_manifest()
    profile = DummyProfile(
        selected_tags={"EthicsReligious": ["ethics_halal"]},
        disliked_tags={},
    )
    request = CandidateFilterRequest(limit=1, declinedMealIds=["meal_vegan"])
    result = generate_candidate_pool(
        manifest=manifest,
        tag_manifest=load_tag_manifest(),
        profile=profile,
        request=request,
        user_id="user_1",
    )

    assert result.totalCandidates == 1
    assert result.returnedCount == 1
    assert result.candidateMeals[0].mealId == "meal_halal"


def test_generate_candidate_pool_enforces_heat_limit_for_mild_users():
    manifest = _build_manifest()
    profile = DummyProfile(
        selected_tags={"HeatSpice": ["heat_mild"]},
        disliked_tags={},
    )
    request = CandidateFilterRequest()
    result = generate_candidate_pool(
        manifest=manifest,
        tag_manifest=load_tag_manifest(),
        profile=profile,
        request=request,
        user_id="user_2",
    )

    # Vegan bowl is mild, hot curry should be excluded.
    returned_ids = {meal.mealId for meal in result.candidateMeals}
    assert "meal_hot" not in returned_ids
    assert "meal_vegan" in returned_ids
