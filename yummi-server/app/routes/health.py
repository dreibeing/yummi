from __future__ import annotations

import os
from fastapi import APIRouter
from ..config import get_settings


router = APIRouter()


@router.get("/health")
def health():
    s = get_settings()
    return {
        "status": "ok",
        "service": s.app_name,
        "env": s.environment,
        "pid": os.getpid(),
    }

