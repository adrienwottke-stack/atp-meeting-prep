"""Read-only controlled HTTP checks: I/O concurrency, outages and request IDs."""

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from atp.config import Settings
from atp.models import Actor
from atp.web import create_app


def app_with_repo(repo, authenticate=None):
    settings = Settings(
        _env_file=None,
        fixture_mode=True,
        database_url="unit-only",
        worker_database_url="unit-only",
        supabase_url="https://unit.example",
        supabase_anon_key="synthetic-public-key",
    )
    app = create_app(settings)

    async def auth(_):
        return Actor(user_id=str(uuid4()), org_id=str(uuid4()))

    app.state.repo = repo
    app.state.auth = SimpleNamespace(authenticate=authenticate or auth)
    app.state.storage = SimpleNamespace()
    return app


async def test_slow_database_route_does_not_block_health_request():
    entered, release = threading.Event(), threading.Event()

    def meetings(*_):
        entered.set()
        assert release.wait(5)
        return []

    app = app_with_repo(SimpleNamespace(list_meetings=meetings))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        task = asyncio.create_task(client.get("/v1/meetings", headers={"Authorization": "Bearer unit"}))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            health = await asyncio.wait_for(client.get("/health"), 2)
            assert health.status_code == 200 and not task.done()
        finally:
            release.set()
        assert (await task).status_code == 200


@pytest.mark.parametrize("failure,expected", [("UNAUTHENTICATED", 401), ("AUTH_UNAVAILABLE", 503)])
async def test_invalid_identity_and_service_outage_are_distinct(failure, expected):
    async def unavailable(_):
        raise ValueError(failure)

    app = app_with_repo(SimpleNamespace(), unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        response = await client.get("/v1/meetings", headers={"Authorization": "Bearer unit"})
    assert response.status_code == expected
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


async def test_early_middleware_rejection_includes_server_request_id():
    app = app_with_repo(SimpleNamespace())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        response = await client.post(
            "/v1/preparations", headers={"Origin": "https://foreign.example", "X-Request-ID": "untrusted"}
        )
    assert response.status_code == 403
    assert response.json()["request_id"] == response.headers["X-Request-ID"] != "untrusted"


async def test_waiting_questions_are_repaired_on_read_and_accepted_by_answers_api():
    from test_clarification_answers import data, question

    from atp.answers import resolve_answers

    original = [question(field=field) for field in ("goal", "relationship", "relationship_context")]
    for q in original:
        q.update(options=[], answer_types=["provide", "select", "leave_unknown"])
    original[2]["question"] = "Was ist der konkrete Anlass des Setup-Termins?"
    run = {"status": "needs_clarification", "version": 2, "clarifications": original}
    saved = []

    def answer(actor, run_id, version, answers):
        assert version == 2
        changed, resolved, _ = resolve_answers(data(), original, answers, {}, actor.user_id)
        saved.append(changed)
        return {"status": "queued", "version": 3, "clarifications": resolved}

    app = app_with_repo(SimpleNamespace(get_run=lambda *_: run, answer=answer))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://unit.example",
        headers={"Authorization": "Bearer unit"},
    ) as client:
        response = await client.get("/v1/runs/waiting")
        assert response.status_code == 200
        questions = response.json()["clarifications"]
        assert questions[1]["options"] == ["Neukontakt", "Bestandskunde", "Laufendes Projekt"]
        assert questions[1]["answer_types"] == ["select", "leave_unknown"]
        assert "bestehende Verpflichtung" in questions[2]["question"]
        supplied = {
            q["question_id"]: {"action": "select", "value": q["options"][0]}
            for q in questions
            if q["field"] != "goal"
        }
        supplied["K04:goal"] = {"action": "provide", "value": "Mehr Kunden gewinnen"}
        response = await client.post(
            "/v1/runs/waiting/answers",
            json={
                "expected_version": 2,
                "answers": supplied,
            },
        )
        assert response.status_code == 200 and response.json()["status"] == "queued"
    assert saved[0]["relationship"] == "new_contact"
    assert original[1]["options"] == []  # Read repair requires no database migration.


async def test_invalid_answer_returns_actionable_public_error():
    def answer(*_):
        raise ValueError("INVALID_ANSWER")

    app = app_with_repo(SimpleNamespace(answer=answer))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        response = await client.post(
            "/v1/runs/waiting/answers",
            headers={"Authorization": "Bearer unit"},
            json={"expected_version": 2, "answers": {"K04:relationship": "other"}},
        )
    assert response.status_code == 422 and response.json()["code"] == "INVALID_ANSWER"
    assert "angebotene Antwort" in response.json()["message"]
