import json
from pathlib import Path
from time import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest

from atp.auth import Auth
from atp.config import Settings
from atp.models import Actor
from atp.repository import Repository


class MembershipRepository:
    def resolve_actor(self, user_id, token="", session_id=None):
        # user_metadata and app_metadata are not consulted for application roles.
        return Actor(user_id=user_id, org_id=str(uuid4()), role="member", token=token)


def configuration():
    return SimpleNamespace(
        supabase_jwt_algorithm="HS256",
        supabase_jwt_secret="a-synthetic-unit-test-signing-secret-at-least-sixty-four-characters-long",
        supabase_jwt_issuer="https://auth.example/auth/v1",
        supabase_jwt_audience="authenticated",
    )


def token(settings, **overrides):
    claims = {
        "sub": str(uuid4()),
        "iss": settings.supabase_jwt_issuer,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": int(time()),
        "exp": int(time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, settings.supabase_jwt_secret, algorithm="HS256")


@pytest.mark.parametrize(
    "change",
    [
        {"iss": "https://other.example"},
        {"aud": "other"},
        {"exp": 0},
        {"role": "service_role"},
        {"is_anonymous": True},
    ],
)
async def test_invalid_identity_claims_are_rejected(change):
    settings = configuration()
    auth = Auth(settings, MembershipRepository())
    with pytest.raises(ValueError, match="UNAUTHENTICATED"):
        await auth.authenticate(token(settings, **change))


async def test_configured_algorithm_has_no_fallback_and_metadata_cannot_grant_role():
    settings = configuration()
    auth = Auth(settings, MembershipRepository())
    actor = await auth.authenticate(
        token(settings, user_metadata={"role": "admin"}, app_metadata={"role": "admin"})
    )
    assert actor.role == "member"
    wrong = jwt.encode({"sub": str(uuid4())}, settings.supabase_jwt_secret, algorithm="HS384")
    with pytest.raises(ValueError, match="UNAUTHENTICATED"):
        await auth.authenticate(wrong)
    with pytest.raises(ValueError, match="UNAUTHENTICATED"):
        await auth.authenticate("not.a.token")


async def test_real_local_supabase_auth_two_users():
    settings = Settings()
    root = Path(__file__).resolve().parents[1]
    credentials_path = root / "output" / "setup" / "local-users.json"
    if (
        settings.app_mode != "local"
        or settings.supabase_url != "http://127.0.0.1:55431"
        or not credentials_path.exists()
    ):
        pytest.skip("Isolated local Supabase Auth not configured.")
    credentials = json.loads(credentials_path.read_text())
    auth = Auth(settings, Repository(settings))
    actors = []
    for user in credentials["users"]:
        response = await auth.login(user["email"], user["password"])
        actor = await auth.authenticate(response["access_token"])
        assert actor.user_id == user["id"] and actor.org_id == credentials["org_id"]
        actors.append(actor)
    assert actors[0].user_id != actors[1].user_id
    assert actors[0].role == "admin" and actors[1].role == "member"
    with pytest.raises(ValueError, match="UNAUTHENTICATED"):
        await auth.login(credentials["users"][0]["email"], "incorrect-password")
