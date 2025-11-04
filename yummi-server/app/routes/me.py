from __future__ import annotations

from fastapi import APIRouter, Depends
from ..auth import get_current_principal


router = APIRouter()


@router.get("/me")
def me(principal = Depends(get_current_principal)):
    # return normalized principal; DB upsert can be added later
    return {
        "sub": principal.get("sub"),
        "email": principal.get("email"),
        "claims": principal.get("claims"),
    }

