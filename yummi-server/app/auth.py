from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

from .config import get_settings


_http = httpx.Client(timeout=5)
_bearer = HTTPBearer(auto_error=False)


class JWKSCache:
    def __init__(self) -> None:
        self._jwks: Optional[Dict[str, Any]] = None
        self._exp_ts: float = 0.0

    def get(self, url: str) -> Dict[str, Any]:
        now = time.time()
        if self._jwks is None or now >= self._exp_ts:
            resp = _http.get(url)
            resp.raise_for_status()
            self._jwks = resp.json()
            # cache for 10 minutes
            self._exp_ts = now + 600
        return self._jwks  # type: ignore[return-value]


_jwks_cache = JWKSCache()


def _verify_jwt(token: str) -> Dict[str, Any]:
    settings = get_settings()
    if settings.auth_disable_verification:
        # Dev mode: do not verify signature. Not for production.
        try:
            return jwt.get_unverified_claims(token)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

    if not settings.clerk_issuer:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Auth issuer not configured")

    jwks_url = settings.clerk_jwks_url or settings.clerk_issuer.rstrip("/") + "/.well-known/jwks.json"
    jwks = _jwks_cache.get(jwks_url)

    try:
        unverified = jwt.get_unverified_header(token)
        kid = unverified.get("kid")
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found")
        claims = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            audience=settings.clerk_audience,
            issuer=settings.clerk_issuer,
        )
        return claims
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"JWT verification failed: {e}")


def get_current_principal(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Dict[str, Any]:
    if not creds or not creds.scheme.lower() == "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = creds.credentials
    claims = _verify_jwt(token)
    # normalize common fields
    principal = {
        "sub": claims.get("sub"),
        "email": claims.get("email") or claims.get("email_address"),
        "claims": claims,
    }
    if not principal["sub"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: no sub")
    return principal

