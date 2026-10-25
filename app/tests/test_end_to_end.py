"""Real local Supabase/Auth integration with explicitly synthetic research only.

Never runs against a hosted project. All created meetings are test-owned and removed.
"""

import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from atp.config import Settings
from atp.models import MeetingInput
from atp.web import create_app
from atp.workflow import Workflow


@pytest.fixture
def local_app():
    settings = Settings()
    users_path = Path(settings.output_dir) / "setup" / "local-users.json"
    if not (
        settings.fixture_mode
        and settings.app_mode == "local"
        and settings.supabase_url.startswith("http://127.0.0.1:")
        and users_path.exists()
    ):
        pytest.skip("Isolierter lokaler Supabase-Fixture-Stack nicht eingerichtet.")
    users = json.loads(users_path.read_text(encoding="utf-8-sig"))["users"]
    app = create_app(settings)
    created = []
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post("/v1/auth/login", json={k: users[0][k] for k in ("email", "password")})
        assert login.status_code == 200, login.json()
        yield app, client, settings, users, created
        client.headers["Origin"] = settings.app_base_url
        for meeting_id in created:
            client.delete("/v1/meetings/" + meeting_id)
        if app.state.repo:
            workflow = Workflow(settings, app.state.repo, app.state.storage)
            for _ in range(len(created) + 5):
                if not workflow.purge_once():
                    break


def base_input(**overrides):
    return MeetingInput.model_validate(
        {
            "company": {"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
            "people": [
                {"name": "Lea Sommer", "role_hint": "Geschäftsführerin"},
                {"name": "Tobias Kern", "role_hint": "Leiter IT"},
            ],
            "meeting_type": "setup",
            "relationship": "new_contact",
            "confirmed": True,
            "public_identifiers_confirmed": True,
            **overrides,
        }
    ).model_dump(mode="json")


def start(local_app, data=None):
    app, client, settings, users, created = local_app
    response = client.post(
        "/v1/preparations",
        json=data or base_input(),
        headers={"Origin": settings.app_base_url, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.json()
    result = response.json()
    created.append(result["meeting_id"])
    return result


def process(local_app, target_run):
    app, client, settings, _, _ = local_app
    workflow = Workflow(settings, app.state.repo, app.state.storage)
    for _ in range(100):
        job = app.state.repo.claim_job("integration-" + str(uuid4()))
        if job is None:
            break
        asyncio.run(workflow.process(job))
        if job["run_id"] == target_run:
            return client.get("/v1/runs/" + target_run).json()
    pytest.fail("Der gestartete Auftrag konnte nicht übernommen werden.")


def test_T01_T14_full_flow_versions_two_users_pdf_decision_delete(local_app):
    app, client, settings, users, created = local_app
    started = start(local_app)
    run = process(local_app, started["run_id"])
    assert run["status"] == "completed", run
    bid = run["briefing_id"]
    result = client.get("/v1/briefings/" + bid).json()
    assert result["fixture_mode"] is True
    assert len(result["people"]) == 2 and len(result["questions"]) >= 5 and len(result["storyline"]) == 4
    assert result["icp"]["status"] == "matching"
    pdf = client.get("/v1/briefings/" + bid + "/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    import pymupdf

    doc = pymupdf.open(stream=pdf.content, filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)
    assert "Musterwerk GmbH" in text and "Lea Sommer" in text and "Tobias Kern" in text
    assert "Geschäftsmodell" in text
    headers = {"Origin": settings.app_base_url}
    denied = client.post(
        "/v1/meetings/" + started["meeting_id"] + "/decision",
        headers=headers,
        json={"run_id": started["run_id"], "confirmed_decision": "Ausnahme prüfen"},
    )
    assert denied.status_code == 422
    decision = client.post(
        "/v1/meetings/" + started["meeting_id"] + "/decision",
        headers=headers,
        json={
            "run_id": started["run_id"],
            "confirmed_decision": "Ausnahme prüfen",
            "override_reason": "Synthetischer Test einer menschlichen Abweichung.",
        },
    )
    assert decision.status_code == 200
    assert (
        client.get("/v1/briefings/" + bid).json()["decision"]["original_recommendation"]
        == "Gespräch wahrnehmen"
    )
    new_run = client.post(
        "/v1/meetings/" + started["meeting_id"] + "/runs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=result["input"],
    )
    assert new_run.status_code == 202
    assert new_run.json()["result_version"] == 2
    run2 = process(local_app, new_run.json()["run_id"])
    assert run2["status"] == "completed"
    assert run2["briefing_id"] != bid
    assert client.get("/v1/briefings/" + bid).json()["result_version"] == 1
    with TestClient(app, raise_server_exceptions=False) as other:
        assert other.get("/v1/briefings/" + bid).status_code == 401
        assert (
            other.post("/v1/auth/login", json={k: users[1][k] for k in ("email", "password")}).status_code
            == 200
        )
        assert other.get("/v1/briefings/" + bid).status_code == 404
        assert other.get("/v1/briefings/" + bid + "/pdf").status_code == 404
        assert other.get("/v1/runs/" + started["run_id"]).status_code == 404
        assert all(
            m["meeting_id"] != started["meeting_id"] for m in other.get("/v1/meetings").json()["items"]
        )
    second = start(local_app)
    assert client.delete("/v1/meetings/" + started["meeting_id"], headers=headers).status_code == 202
    assert client.get("/v1/briefings/" + bid).status_code == 404
    assert client.get("/v1/briefings/" + bid + "/pdf").status_code == 404
    assert client.get("/v1/runs/" + second["run_id"]).status_code == 200


def test_T01_free_text_preview_confirm_preserves_fields(local_app):
    _, client, settings, _, created = local_app
    headers = {"Origin": settings.app_base_url}
    parsed = client.post(
        "/v1/input/parse",
        headers=headers,
        json={
            "text": "Musterwerk GmbH, https://musterwerk.example; Lea Sommer Geschäftsführerin und Tobias Kern Leiter IT; Setup; Neukontakt"
        },
    )
    assert parsed.status_code == 200, parsed.json()
    data = parsed.json()
    preview = data["preview"]
    assert not preview["confirmed"] and preview["company"]["website"] == "https://musterwerk.example"
    assert [p["name"] for p in preview["people"]] == ["Lea Sommer", "Tobias Kern"]
    preview.update(confirmed=True, public_identifiers_confirmed=True, internal_context="TEST-INTERN-4711")
    response = client.post(
        "/v1/preparations",
        headers={**headers, "Idempotency-Key": str(uuid4()), "X-Draft-ID": data["draft_id"]},
        json=preview,
    )
    assert response.status_code == 202, response.json()
    created.append(response.json()["meeting_id"])
    run = process(local_app, response.json()["run_id"])
    assert run["status"] == "completed" and run["usage"]["model_requests"] == 3
    result = client.get("/v1/briefings/" + run["briefing_id"]).json()
    assert result["input"]["internal_context"] == "TEST-INTERN-4711"
    assert all("TEST-INTERN-4711" not in s["excerpt"] for s in result["sources"])


def test_T02_missing_core_no_job_and_idempotent_start(local_app):
    _, client, settings, _, created = local_app
    headers = {"Origin": settings.app_base_url, "Idempotency-Key": str(uuid4())}
    before = len(client.get("/v1/meetings").json()["items"])
    data = base_input()
    data["company"]["website"] = ""
    assert client.post("/v1/preparations", headers=headers, json=data).status_code == 422
    assert len(client.get("/v1/meetings").json()["items"]) == before
    data = base_input()
    one = client.post("/v1/preparations", headers=headers, json=data)
    two = client.post("/v1/preparations", headers=headers, json=data)
    assert one.status_code == two.status_code == 202
    assert one.json()["run_id"] == two.json()["run_id"]
    created.append(one.json()["meeting_id"])
    data["goal"] = "Anderes Ziel"
    assert client.post("/v1/preparations", headers=headers, json=data).status_code == 409


def test_T03_company_clarification_resumes_without_input_loss(local_app):
    _, client, settings, _, _ = local_app
    data = base_input(company={"name": "Andere Einheit GmbH", "website": "https://musterwerk.example"})
    started = start(local_app, data)
    run = process(local_app, started["run_id"])
    assert run["status"] == "needs_clarification"
    question = run["clarifications"][0]
    assert question["code"] == "K02" and run["usage"]["model_requests"] == 0
    answer = {
        "expected_version": run["version"],
        "answers": {question["question_id"]: "Firma und Website gehören zusammen"},
    }
    headers = {"Origin": settings.app_base_url}
    accepted = client.post("/v1/runs/" + started["run_id"] + "/answers", headers=headers, json=answer)
    assert accepted.status_code == 200
    assert (
        client.post("/v1/runs/" + started["run_id"] + "/answers", headers=headers, json=answer).status_code
        == 409
    )
    resumed = process(local_app, started["run_id"])
    for _ in range(2):
        if resumed["status"] != "needs_clarification":
            break
        assert all(q["code"] == "K06" for q in resumed["clarifications"] if not q.get("answer"))
        response = client.post(
            "/v1/runs/" + started["run_id"] + "/answers",
            headers=headers,
            json={
                "expected_version": resumed["version"],
                "answers": {
                    q["question_id"]: {"action": "leave_unknown"}
                    for q in resumed["clarifications"]
                    if not q.get("answer")
                },
            },
        )
        assert response.status_code == 200
        resumed = process(local_app, started["run_id"])
    assert resumed["status"] == "partial"
    assert resumed["input"]["company"]["name"] == "Andere Einheit GmbH"


def test_five_controlled_runtime_measurements(local_app):
    measurements = []
    for _ in range(5):
        started = start(local_app)
        clock = perf_counter()
        run = process(local_app, started["run_id"])
        assert run["status"] == "completed", run
        measurements.append(
            {
                "run_id": started["run_id"],
                "wall_seconds": round(perf_counter() - clock, 3),
                "usage": run["usage"],
                "status": run["status"],
                "fixture_mode": True,
            }
        )
    output = Path(local_app[2].output_dir) / "testberichte"
    output.mkdir(parents=True, exist_ok=True)
    (output / "laufzeit-fixtures.json").write_text(
        json.dumps(
            {
                "note": "Fünf kontrollierte lokale Fixtureläufe. Keine Live-Laufzeitabnahme.",
                "measurements": measurements,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert all(m["wall_seconds"] < 180 for m in measurements)


def test_T12_restart_from_research_checkpoint_fences_old_worker(local_app, monkeypatch):

    from atp.budget import Budget
    from atp.models import ResearchQuery

    app, client, settings, _, _ = local_app
    started = start(local_app)
    repo = app.state.repo
    first = repo.claim_job("controlled-crash-before-synthesis")
    assert first["run_id"] == started["run_id"]
    workflow = Workflow(settings, repo, app.state.storage)
    data = MeetingInput.model_validate(first["input"])
    budget = Budget(settings)
    bundle = asyncio.run(workflow.provider.research(ResearchQuery.from_input(data), budget))
    checkpoint = {"phase": "evaluating", "research": bundle.model_dump(mode="json")}
    assert repo.checkpoint(first["id"], first["lease_token"], "evaluating", checkpoint, budget.snapshot())
    with repo._connection(worker=True) as db:
        db.execute("update atp.jobs set lease_until=now()-interval '1 second' where id=%s", (first["id"],))
    second = repo.claim_job("controlled-replacement-worker")
    assert second["id"] == first["id"] and second["lease_token"] != first["lease_token"]
    assert repo.checkpoint(first["id"], first["lease_token"], "generating", {}, {}) is False

    async def no_research(*args, **kwargs):
        raise AssertionError("Gespeicherte Recherche darf nicht erneut ausgeführt werden.")

    monkeypatch.setattr(workflow.provider, "research", no_research)
    asyncio.run(workflow.process(second))
    run = client.get("/v1/runs/" + started["run_id"]).json()
    assert run["status"] == "completed" and run["usage"]["model_requests"] == 2


def test_PDF_failure_keeps_briefing_and_can_retry(local_app, monkeypatch):
    import atp.pdf

    app, client, settings, _, _ = local_app
    started = start(local_app)
    render = atp.pdf.render_pdf

    def broken(result):
        raise RuntimeError("Controlled PDF failure")

    monkeypatch.setattr(atp.pdf, "render_pdf", broken)
    run = process(local_app, started["run_id"])
    assert run["status"] == "completed" and run["pdf_status"] == "failed"
    bid = run["briefing_id"]
    assert client.get("/v1/briefings/" + bid).status_code == 200
    assert client.get("/v1/briefings/" + bid + "/pdf").status_code == 409
    monkeypatch.setattr(atp.pdf, "render_pdf", render)
    retry = client.post("/v1/briefings/" + bid + "/pdf/retry", headers={"Origin": settings.app_base_url})
    assert retry.status_code == 200, retry.json()
    assert client.get("/v1/briefings/" + bid + "/pdf").status_code == 200


def test_T12_synthesis_failure_produces_honest_partial(local_app, monkeypatch):
    from atp.providers import ProviderError

    app, client, settings, _, _ = local_app
    started = start(local_app)
    job = app.state.repo.claim_job("controlled-synthesis-failure")
    workflow = Workflow(settings, app.state.repo, app.state.storage)

    async def broken(*args, **kwargs):
        raise ProviderError("controlled_provider_timeout")

    monkeypatch.setattr(workflow.provider, "synthesize", broken)
    asyncio.run(workflow.process(job))
    run = client.get("/v1/runs/" + started["run_id"]).json()
    assert run["status"] == "partial"
    result = client.get("/v1/briefings/" + run["briefing_id"]).json()
    assert result["fixture_mode"] is True and result["limits"]
    assert len(result["questions"]) >= 5 and result["sources"]


def test_storage_upload_failure_keeps_briefing(local_app, monkeypatch):
    app, client, settings, _, _ = local_app
    started = start(local_app)

    def broken(*args, **kwargs):
        raise OSError("Controlled disk/upload failure")

    monkeypatch.setattr(app.state.storage, "put_pdf", broken)
    run = process(local_app, started["run_id"])
    assert run["status"] == "completed" and run["pdf_status"] == "failed"
    assert client.get("/v1/briefings/" + run["briefing_id"]).status_code == 200


@pytest.mark.parametrize("delete_phase", ["before_publish", "after_write"])
def test_deletion_export_race_never_recreates_orphan_pdf(local_app, monkeypatch, delete_phase):
    import atp.pdf

    app, client, settings, _, _ = local_app
    started = start(local_app)
    repo, storage = app.state.repo, app.state.storage
    owner = asyncio.run(app.state.auth.authenticate(client.cookies.get("atp_access")))
    workflow = Workflow(settings, repo, storage)
    job = repo.claim_job("controlled-deletion-race")
    if delete_phase == "before_publish":
        render = atp.pdf.render_pdf

        def render_then_purge(result):
            pdf = render(result)
            repo.delete_meeting(owner, started["meeting_id"])
            assert workflow.purge_once()
            return pdf

        monkeypatch.setattr(atp.pdf, "render_pdf", render_then_purge)
    else:
        put = storage.put_pdf

        def write_then_delete(*args, **kwargs):
            path = put(*args, **kwargs)
            repo.delete_meeting(owner, started["meeting_id"])
            return path

        monkeypatch.setattr(storage, "put_pdf", write_then_delete)
    asyncio.run(workflow.process(job))
    assert client.get("/v1/runs/" + started["run_id"]).status_code == 404
    path = f"{job['org_id']}/{job['meeting_id']}/{job['run_id']}/result.pdf"
    assert not storage._path(path)[1].exists()


def test_upload_and_cleanup_failure_preserves_briefing_and_persists_cleanup(local_app, monkeypatch):
    app, client, settings, _, _ = local_app
    started = start(local_app)

    def broken(*args, **kwargs):
        raise OSError("Controlled storage outage")

    monkeypatch.setattr(app.state.storage, "put_pdf", broken)
    monkeypatch.setattr(app.state.storage, "delete_pdf", broken)
    run = process(local_app, started["run_id"])
    assert run["status"] == "completed" and run["pdf_status"] == "failed"
    assert client.get("/v1/briefings/" + run["briefing_id"]).status_code == 200
    with app.state.repo._connection(worker=True) as db:
        pending = db.execute(
            "select path from atp.storage_cleanup where meeting_id=%s", (started["meeting_id"],)
        ).fetchone()
    assert pending is not None


def test_explicit_admin_share_read_edit_revoke(local_app, monkeypatch):
    import argparse

    import atp.manage

    app, client, settings, users, _ = local_app
    started = start(local_app)
    run = process(local_app, started["run_id"])
    bid = run["briefing_id"]
    monkeypatch.setattr(atp.manage, "getpass", lambda prompt: users[0]["password"])
    args = argparse.Namespace(
        command="share",
        email=users[0]["email"],
        meeting_id=started["meeting_id"],
        user_id=users[1]["id"],
        permission="read",
    )
    asyncio.run(atp.manage.execute(args))
    with TestClient(app, raise_server_exceptions=False) as other:
        assert (
            other.post("/v1/auth/login", json={k: users[1][k] for k in ("email", "password")}).status_code
            == 200
        )
        assert other.get("/v1/briefings/" + bid).status_code == 200
        assert other.get("/v1/briefings/" + bid + "/pdf").status_code == 200
        assert (
            other.post(
                "/v1/briefings/" + bid + "/pdf/retry", headers={"Origin": settings.app_base_url}
            ).status_code
            == 404
        )
        args.permission = "edit"
        asyncio.run(atp.manage.execute(args))
        assert (
            other.post(
                "/v1/briefings/" + bid + "/pdf/retry", headers={"Origin": settings.app_base_url}
            ).status_code
            == 200
        )
        args.command = "unshare"
        asyncio.run(atp.manage.execute(args))
        assert other.get("/v1/briefings/" + bid).status_code == 404


def test_retention_preview_and_apply_are_explicit(local_app):
    from atp.maintenance import expire_records

    app, client, settings, _, _ = local_app
    old = start(local_app)
    fresh = start(local_app)
    repo = app.state.repo
    with repo._connection(worker=True) as db:
        db.execute(
            "update atp.meetings set created_at=now()-interval '20 days' where id=%s", (old["meeting_id"],)
        )
    preview = expire_records(repo, 10, apply=False)
    assert preview["meetings"] >= 1 and preview["applied"] is False
    assert client.get("/v1/runs/" + old["run_id"]).status_code == 200
    expire_records(repo, 10, apply=True)
    assert client.get("/v1/runs/" + old["run_id"]).status_code == 404
    assert client.get("/v1/runs/" + fresh["run_id"]).status_code == 200


def test_T10_profile_activation_preserves_old_run_snapshot(local_app, monkeypatch):
    import argparse

    import atp.manage
    from atp.profiles import load_profile

    app, client, settings, users, _ = local_app
    first = start(local_app)
    original = load_profile()
    changed = {**original, "profile_id": "ATP-local-test-B"}
    monkeypatch.setattr(
        atp.manage, "load_profile", lambda version: changed if version == "ATP-local-test-B" else original
    )
    monkeypatch.setattr(atp.manage, "getpass", lambda prompt: users[0]["password"])
    args = argparse.Namespace(
        command="profile-activate",
        email=users[0]["email"],
        version="ATP-local-test-B",
        reason="Kontrollierter T10-Profiltest",
    )
    try:
        asyncio.run(atp.manage.execute(args))
        second = start(local_app)
        with app.state.repo._connection(worker=True) as db:
            old = db.execute(
                "select profile_version,profile_hash from atp.runs where id=%s", (first["run_id"],)
            ).fetchone()
            new = db.execute(
                "select profile_version,profile_hash from atp.runs where id=%s", (second["run_id"],)
            ).fetchone()
        assert old["profile_version"] == original["profile_id"]
        assert new["profile_version"] == "ATP-local-test-B" and old["profile_hash"] != new["profile_hash"]
    finally:
        args.version = original["profile_id"]
        asyncio.run(atp.manage.execute(args))


def test_T12_actual_new_worker_process_resumes_persisted_checkpoint(local_app):
    import subprocess
    import sys

    from atp.budget import Budget
    from atp.models import ResearchQuery

    app, client, settings, _, _ = local_app
    started = start(local_app)
    repo = app.state.repo
    first = repo.claim_job("process-before-restart")
    workflow = Workflow(settings, repo, app.state.storage)
    budget = Budget(settings)
    bundle = asyncio.run(
        workflow.provider.research(
            ResearchQuery.from_input(MeetingInput.model_validate(first["input"])), budget
        )
    )
    assert repo.checkpoint(
        first["id"],
        first["lease_token"],
        "evaluating",
        {"research": bundle.model_dump(mode="json")},
        budget.snapshot(),
    )
    with repo._connection(worker=True) as db:
        db.execute("update atp.jobs set lease_until=now()-interval '1 second' where id=%s", (first["id"],))
    completed = subprocess.run(
        [sys.executable, "-m", "atp.worker", "--once"],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0
    run = client.get("/v1/runs/" + started["run_id"]).json()
    assert run["status"] == "completed" and run["usage"]["model_requests"] == 2


def test_K03_only_profile_waits_company_result_remains_visible(local_app, monkeypatch):
    from atp.models import Clarification

    app, client, settings, _, _ = local_app
    started = start(local_app)
    workflow = Workflow(settings, app.state.repo, app.state.storage)
    research = workflow.provider.research

    async def ambiguous(query, budget):
        bundle = await research(query, budget)
        person = query.people[0]["participant_id"]
        bundle.facts = [f for f in bundle.facts if f.subject_ref != person]
        bundle.clarifications.append(
            Clarification(
                question_id="K03:" + person,
                code="K03",
                field="people",
                participant_id=person,
                reason="Kontrollierte mehrdeutige Identität",
                options=["Als ungeklärt überspringen"],
                handling="skippable_profile",
            )
        )
        return bundle

    monkeypatch.setattr(workflow.provider, "research", ambiguous)
    job = app.state.repo.claim_job("controlled-dependent-profile")
    asyncio.run(workflow.process(job))
    run = client.get("/v1/runs/" + started["run_id"]).json()
    assert run["status"] == "needs_clarification"
    preview = run["independent_result"]
    assert preview["company"] and len(preview["people"]) == 2
    assert preview["people"][0]["status"] == "ambiguous"
    assert preview["people"][1]["status"] == "confirmed"


def test_K04_preview_holds_only_dependent_recommendation(local_app):
    _, client, _, _, _ = local_app
    started = start(local_app, base_input(meeting_type="project_review", relationship="new_contact"))
    run = process(local_app, started["run_id"])
    assert run["status"] == "needs_clarification"
    preview = run["independent_result"]
    assert preview["recommendation"]["value"] == "Vorab klären"
    assert preview["company"] and len(preview["people"]) == 2


def test_pdf_retry_failure_status_is_written_inside_storage_lock(local_app, monkeypatch):
    app, client, settings, _, _ = local_app
    started = start(local_app)
    run = process(local_app, started["run_id"])
    repo = app.state.repo
    original = repo.retry_pdf
    observations = []

    def checked(actor, briefing_id, pdf_path, pdf_error):
        with repo._connection(worker=True) as db:
            acquired = db.execute(
                "select pg_try_advisory_xact_lock(hashtextextended(%s,0)) as ok",
                ("storage:" + started["meeting_id"],),
            ).fetchone()["ok"]
        observations.append(not acquired)
        return original(actor, briefing_id, pdf_path, pdf_error)

    def broken(*args, **kwargs):
        raise OSError("Controlled retry upload failure")

    monkeypatch.setattr(repo, "retry_pdf", checked)
    monkeypatch.setattr(app.state.storage, "put_pdf", broken)
    response = client.post(
        "/v1/briefings/" + run["briefing_id"] + "/pdf/retry", headers={"Origin": settings.app_base_url}
    )
    assert response.status_code == 503
    assert observations == [True]
    assert client.get("/v1/briefings/" + run["briefing_id"]).json()["pdf_status"] == "failed"
