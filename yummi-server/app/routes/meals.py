from __future__ import annotations

from fastapi import APIRouter

from ..schemas import MealManifest, MealArchetype
from ..services.meals import get_meal_manifest, get_meal_archetype

router = APIRouter()


@router.get("/meals", response_model=MealManifest)
def list_meals() -> MealManifest:
    return get_meal_manifest()


@router.get("/meals/{archetype_uid}", response_model=MealArchetype)
def get_meals_for_archetype(archetype_uid: str) -> MealArchetype:
    return get_meal_archetype(archetype_uid)
