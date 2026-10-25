"""Supabase password authentication and explicit JWT verification."""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

import httpx
import jwt

from .models import Actor


class Auth:
    def __init__(self, settings, repository):
        self.settings = settings
        self.repository = repository
        self._jwks: dict = {}
        self._jwks_at = 0.0

    async def _key(self, token):
        algorithm = self.settings.supabase_jwt_algorithm
        if algorithm not in ("HS256", "ES256", "RS256"):
            raise ValueError("AUTH_NOT_CONFIGURED")
        header = jwt.get_unverified_header(token)
        if header.get("alg") != algorithm:
            raise ValueError("UNAUTHENTICATED")
        if algorithm == "HS256":
            if not self.settings.supabase_jwt_secret:
                raise ValueError("AUTH_NOT_CONFIGURED")
            return self.settings.supabase_jwt_secret
        kid = header.get("kid")
        if not kid:
            raise ValueError("UNAUTHENTICATED")
        if time.monotonic() - self._jwks_at > 300 or kid not in self._jwks:
            try:
                async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
                    response = await client.get(
                        self.settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
                    )
                    response.raise_for_status()
                    self._jwks = {
                        key["kid"]: key
                        for key in response.json().get("keys", [])
                        if key.get("alg") == algorithm and key.get("kid")
                    }
                    self._jwks_at = time.monotonic()
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                raise ValueError("AUTH_UNAVAILABLE") from exc
        key = self._jwks.get(kid)
        if not key:
            raise ValueError("UNAUTHENTICATED")
        return jwt.PyJWK.from_dict(key, algorithm=algorithm).key

    async def authenticate(self, token: str) -> Actor:
        if not token or len(token) > 20000 or not self.settings.supabase_jwt_issuer:
            raise ValueError("UNAUTHENTICATED")
        try:
            key = await self._key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=[self.settings.supabase_jwt_algorithm],
                issuer=self.settings.supabase_jwt_issuer,
                audience=self.settings.supabase_jwt_audience,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "role"]},
                leeway=5,
            )
            try:
                UUID(claims["sub"])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("UNAUTHENTICATED") from exc
            if claims.get("role") != "authenticated" or claims.get("is_anonymous", False):
                raise ValueError("UNAUTHENTICATED")
            actor = await asyncio.to_thread(
                self.repository.resolve_actor, claims["sub"], token, session_id=claims.get("session_id")
            )
            if actor is None:
                raise ValueError("UNAUTHENTICATED")
            return actor
        except (jwt.PyJWTError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError("UNAUTHENTICATED") from exc

    async def login(self, email, password) -> dict:
        if not self.settings.supabase_url or not self.settings.supabase_anon_key:
            raise ValueError("AUTH_NOT_CONFIGURED")
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
                response = await client.post(
                    self.settings.supabase_url.rstrip("/") + "/auth/v1/token?grant_type=password",
                    headers={"apikey": self.settings.supabase_anon_key},
                    json={"email": email, "password": password},
                )
                if response.status_code in (400, 401, 403):
                    raise ValueError("UNAUTHENTICATED")
                response.raise_for_status()
                data = response.json()
                await self.authenticate(data["access_token"])
                return {key: data[key] for key in ("access_token", "refresh_token", "expires_in")}
        except (httpx.HTTPError, KeyError) as exc:
            raise ValueError("AUTH_UNAVAILABLE") from exc

    async def logout(self, token):
        if not token:
            return
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(
                    self.settings.supabase_url.rstrip("/") + "/auth/v1/logout?scope=local",
                    headers={"apikey": self.settings.supabase_anon_key, "Authorization": "Bearer " + token},
                )
                if response.status_code not in (200, 204, 401, 403):
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError("AUTH_UNAVAILABLE") from exc
