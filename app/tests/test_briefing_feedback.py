"""Direct feedback must persist, respect RLS and reach the next real run snapshot."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from test_pdf import sample_result

from atp.briefing_feedback import BriefingFeedbackStore, company_memory_key
from atp.config import Settings
from atp.memory_models import BriefingFeedbackInput, ContextInput, EntryInput
from atp.models import Actor
from atp.profiles import load_profile, profile_hash
from atp.providers import FixtureProvider, Provider
from atp.repository import Repository
from atp.storage import Storage
from atp.web import create_app
from atp.workflow import Workflow


@pytest.fixture
def feedback_case():
    location = os.environ.get("ATP_TEST_LOCAL_STATUS")
    if not location:
        pytest.skip("Set ATP_TEST_LOCAL_STATUS to the isolated ATP local Supabase status file.")
    status = json.loads(Path(location).read_text(encoding="utf-8-sig"))
    if status["API_URL"] != "http://127.0.0.1:55431" or "127.0.0.1:55432/" not in status["DB_URL"]:
        pytest.fail("Refusing any database except the explicitly isolated local ATP database.")
    settings = Settings(
        _env_file=None,
        fixture_mode=True,
        memory_enabled=True,
        retention_days=90,
        database_url=status["DB_URL"],
        worker_database_url=status["DB_URL"],
        worker_queue="feedback-test-" + str(uuid4()),
        supabase_url="http://127.0.0.1:55431",
        supabase_anon_key="synthetic-preview-key",
    )
    repo = Repository(settings)
    profile = load_profile()
    orgs = [str(uuid4()), str(uuid4())]
    actors = [Actor(user_id=str(uuid4()), org_id=orgs[index // 2]) for index in range(3)]
    with psycopg.connect(status["DB_URL"], autocommit=True, row_factory=dict_row) as admin:
        for actor in actors:
            admin.execute(
                "insert into auth.users(id,email,aud,role) values(%s,%s,'authenticated','authenticated')",
                (actor.user_id, actor.user_id + "@feedback.example.test"),
            )
            admin.execute(
                "insert into atp.memberships(user_id,org_id) values(%s,%s)", (actor.user_id, actor.org_id)
            )
        for index, org in enumerate(orgs):
            admin.execute(
                "insert into atp.profile_versions(org_id,version,content_hash,content,active,activated_by) "
                "values(%s,%s,%s,%s,true,%s)",
                (
                    org,
                    profile["profile_id"],
                    profile_hash(profile),
                    Jsonb(profile),
                    actors[index * 2].user_id,
                ),
            )
        result = sample_result().model_dump(mode="json")
        data = result["input"]
        created = repo.create_preparation(actors[0], data, profile, str(uuid4()))
        result.update(
            briefing_id=str(uuid4()), **{k: created[k] for k in ("meeting_id", "run_id", "result_version")}
        )
        admin.execute(
            "insert into atp.briefings(id,run_id,meeting_id,org_id,result_version,result,pdf_status) "
            "values(%s,%s,%s,%s,1,%s,'failed')",
            (result["briefing_id"], created["run_id"], created["meeting_id"], orgs[0], Jsonb(result)),
        )
        app = create_app(settings)
        app.state.repo = repo
        app.state.storage = SimpleNamespace()

        async def authenticate(token):
            actor = next((actor for actor in actors if actor.user_id == token), None)
            if actor is None:
                raise ValueError("UNAUTHENTICATED")
            return actor

        app.state.auth = SimpleNamespace(authenticate=authenticate)
        case = SimpleNamespace(
            repo=repo,
            store=BriefingFeedbackStore(repo),
            profile=profile,
            data=data,
            result=result,
            admin=admin,
            actors=actors,
            app=app,
            settings=settings,
            base="/v1/briefings/" + result["briefing_id"] + "/feedback",
        )
        try:
            yield case
        finally:
            for org in orgs:
                admin.execute("delete from atp.meetings where org_id=%s", (org,))
                admin.execute("delete from atp.memory_contexts where org_id=%s", (org,))
                admin.execute("delete from atp.profile_versions where org_id=%s", (org,))
            for actor in actors:
                admin.execute("delete from auth.users where id=%s", (actor.user_id,))


def payload(**changes):
    return {
        "submission_id": str(uuid4()),
        "kind": "correction",
        "text": "Frau Sommer leitet den Einkauf.",
        "reference": "Frau Sommer leitet die IT.",
        "remember": True,
        **changes,
    }


def headers(actor):
    return {"Authorization": "Bearer " + actor.user_id}


def next_snapshot(case, data=None, actor=None):
    created = case.repo.create_preparation(
        actor or case.actors[0], data or case.data, case.profile, str(uuid4())
    )
    return case.admin.execute(
        "select memory_snapshot from atp.runs where id=%s", (created["run_id"],)
    ).fetchone()["memory_snapshot"]


def test_feedback_without_prior_context_survives_reload_and_reaches_next_run(feedback_case):
    c = feedback_case
    original = c.repo.get_briefing(c.actors[0], c.result["briefing_id"])
    with TestClient(c.app) as client:
        auth = headers(c.actors[0])
        assert client.get(c.base, headers=auth).json() == {"items": [], "editable": True}
        value = payload()
        response = client.post(c.base, headers=auth, json=value)
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved["remembered"] and saved["entry_id"] == value["submission_id"]
        assert client.post(c.base, headers=auth, json=value).json()["id"] == saved["id"]
        assert len(client.get(c.base, headers=auth).json()["items"]) == 1
        assert client.post(c.base, headers=auth, json={**value, "text": "Anderer Inhalt"}).status_code == 409
    snapshot = next_snapshot(c)
    assert len(snapshot["contexts"]) == 1 and len(snapshot["entries"]) == 1
    entry = snapshot["entries"][0]
    assert "leitet den Einkauf" in entry["text"] and "leitet die IT" in entry["text"]
    assert "Eigener Hinweis" in entry["source"] and c.result["briefing_id"] in entry["source"]
    assert c.repo.get_briefing(c.actors[0], c.result["briefing_id"]) == original
    old = c.admin.execute(
        "select memory_snapshot from atp.runs where id=%s", (c.result["run_id"],)
    ).fetchone()
    assert not old["memory_snapshot"]["entries"]
    assert not c.admin.execute(
        "select 1 from atp.memory_links where company_run_id=%s", (c.result["run_id"],)
    ).fetchone()
    other = deepcopy(c.data)
    other["company"]["website"] = "https://other-company.example"
    assert not next_snapshot(c, other)["entries"]
    assert not next_snapshot(c, actor=c.actors[1])["entries"]


def test_note_can_be_explicitly_remembered_later_and_not_duplicated(feedback_case):
    c = feedback_case
    with TestClient(c.app) as client:
        auth = headers(c.actors[0])
        saved = client.post(c.base, headers=auth, json=payload(kind="outcome", remember=False)).json()
        assert not saved["remembered"] and not next_snapshot(c)["entries"]
        url = c.base + "/" + saved["id"] + "/remember"
        assert client.post(url, headers=auth).json()["remembered"]
        assert client.post(url, headers=auth).json()["remembered"]
    assert len(next_snapshot(c)["entries"]) == 1


def test_feedback_routes_enforce_login_tenant_and_edit_permissions(feedback_case):
    c = feedback_case
    with TestClient(c.app) as client:
        assert client.get(c.base).status_code == 401
        assert client.post(c.base, json=payload()).status_code == 401
        saved = client.post(c.base, headers=headers(c.actors[0]), json=payload()).json()
        for actor in c.actors[1:]:
            assert client.get(c.base, headers=headers(actor)).status_code == 404
            assert client.post(c.base, headers=headers(actor), json=payload()).status_code == 404
            assert (
                client.post(c.base + "/" + saved["id"] + "/remember", headers=headers(actor)).status_code
                == 404
            )
        c.admin.execute(
            "insert into atp.shares(meeting_id,user_id,permission,granted_by) values(%s,%s,'read',%s)",
            (c.result["meeting_id"], c.actors[1].user_id, c.actors[0].user_id),
        )
        visible = client.get(c.base, headers=headers(c.actors[1]))
        assert visible.status_code == 200 and visible.json() == {"items": [], "editable": False}
        assert client.post(c.base, headers=headers(c.actors[1]), json=payload()).status_code == 404
        assert client.post(c.base, headers=headers(c.actors[0]), json=payload(text="  ")).status_code == 422
        c.settings.memory_enabled = False
        assert client.get(c.base, headers=headers(c.actors[0])).status_code == 404


def test_concurrent_feedback_creates_one_company_context_and_rollback_is_atomic(feedback_case, monkeypatch):
    c = feedback_case
    values = [BriefingFeedbackInput(**payload(text="Hinweis " + str(index))) for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(
            pool.map(lambda value: c.store.save(c.actors[0], c.result["briefing_id"], value), values)
        )
    assert saved[0]["context_id"] == saved[1]["context_id"]
    assert len(next_snapshot(c)["entries"]) == 2
    broken = BriefingFeedbackInput(**payload())
    original = c.repo.memory().save_entry

    def fail(*args, **kwargs):
        raise ValueError("TEST_WRITE_FAILURE")

    monkeypatch.setattr(c.repo.memory(), "save_entry", fail)
    with pytest.raises(ValueError, match="TEST_WRITE_FAILURE"):
        c.store.save(c.actors[0], c.result["briefing_id"], broken)
    assert not c.admin.execute(
        "select 1 from atp.memory_feedback where id=%s", (broken.submission_id,)
    ).fetchone()
    monkeypatch.setattr(c.repo.memory(), "save_entry", original)
    assert c.store.save(c.actors[0], c.result["briefing_id"], broken)["remembered"]


def test_automatic_feedback_combines_with_two_explicit_contexts_and_ignores_expiry(feedback_case):
    c = feedback_case
    item = c.store.save(c.actors[0], c.result["briefing_id"], BriefingFeedbackInput(**payload()))
    contexts = []
    for index in range(2):
        context = c.repo.memory().save_context(
            c.actors[0], ContextInput(name=f"Projekt {index}", kind="project")
        )
        c.repo.memory().save_entry(
            c.actors[0], context["id"], EntryInput(text=f"Projektwissen {index}", status="confirmed")
        )
        contexts.append(context["id"])
    snapshot = next_snapshot(c, {**c.data, "context_ids": contexts})
    assert len(snapshot["contexts"]) == 3 and len(snapshot["entries"]) == 3
    for index in range(21):
        c.repo.memory().save_entry(
            c.actors[0], contexts[0], EntryInput(text=f"Weiteres Projektwissen {index}", status="confirmed")
        )
    bounded = next_snapshot(c, {**c.data, "context_ids": contexts})
    assert len(bounded["entries"]) == 20
    assert bounded["entries"][0]["context_id"] == item["context_id"]
    expired = str(uuid4())
    c.admin.execute(
        "insert into atp.memory_contexts(id,org_id,owner_id,kind,name,external_id,created_at,expires_at) "
        "values(%s,%s,%s,'company','Abgelaufene Rückmeldungen',%s,now()-interval '2 days',now()-interval '1 day')",
        (expired, c.actors[0].org_id, c.actors[0].user_id, company_memory_key(c.data["company"])),
    )
    c.admin.execute(
        "insert into atp.memory_entries(context_id,org_id,kind,text,source,status,created_by,expires_at) "
        "values(%s,%s,'learning','ABGELAUFEN','Test','confirmed',%s,now()-interval '1 day')",
        (expired, c.actors[0].org_id, c.actors[0].user_id),
    )
    assert [entry["context_id"] for entry in next_snapshot(c)["entries"]] == [item["context_id"]]


def test_company_mapping_is_exact_and_ignores_names():
    key = company_memory_key({"website": "https://www.Example.COM/path", "name": "Eins"})
    assert key == company_memory_key({"website": "http://example.com/", "name": "Anders"})
    assert key != company_memory_key({"website": "https://sub.example.com/"})
    assert key != company_memory_key({"website": "https://example.org/"})
    assert not company_memory_key({"website": ""})


async def test_remembered_feedback_reaches_worker_synthesis_and_saved_result(
    feedback_case, tmp_path, monkeypatch
):
    c = feedback_case
    c.settings.output_dir = tmp_path / "briefings"
    c.store.save(c.actors[0], c.result["briefing_id"], BriefingFeedbackInput(**payload()))
    # Only the new run is claimed, within this fixture's unique queue.
    c.admin.execute("delete from atp.jobs where run_id=%s", (c.result["run_id"],))
    data = deepcopy(c.data)
    data["company"]["name"] = "Musterwerk GmbH"
    created = c.repo.create_preparation(c.actors[0], data, c.profile, str(uuid4()))
    workflow = Workflow(c.settings, c.repo, Storage(c.settings))
    captured = []
    original = FixtureProvider.synthesize

    async def synthesize(self, data, bundle, profile, icp, recommendation, meta, budget):
        captured.append(deepcopy(meta["memory"]))
        # Exercise the real Provider's synthesis input without an external model call.
        provider = Provider.__new__(Provider)
        provider.settings = c.settings

        async def capture_transport(**kwargs):
            assert "Einkauf" in kwargs["data"]["confirmed_memory"]["entries"][0]["text"]
            assert "keine Recherche" in kwargs["instructions"]
            raise RuntimeError("CAPTURED_SYNTHESIS_INPUT")

        async def request(_budget, **kwargs):
            return await capture_transport(**kwargs)

        provider._request = request
        with pytest.raises(RuntimeError, match="CAPTURED_SYNTHESIS_INPUT"):
            await provider.synthesize(data, bundle, profile, icp, recommendation, meta, budget)
        return await original(self, data, bundle, profile, icp, recommendation, meta, budget)

    monkeypatch.setattr(FixtureProvider, "synthesize", synthesize)
    job = c.repo.claim_job("feedback-worker-proof")
    assert str(job["run_id"]) == created["run_id"]
    await workflow.process(job)
    current = c.repo.get_run(c.actors[0], created["run_id"])
    assert captured, {key: current.get(key) for key in ("status", "phase", "clarifications", "error_code")}
    saved = c.admin.execute(
        "select result from atp.briefings where run_id=%s", (created["run_id"],)
    ).fetchone()
    assert captured and saved and saved["result"]["memory"]["entries"] == captured[0]["entries"]
    assert (
        c.admin.execute("select status from atp.jobs where id=%s", (job["id"],)).fetchone()["status"]
        == "completed"
    )


def test_legacy_context_feedback_keeps_explicit_reuse_and_never_promises_automatic_reuse(feedback_case):
    c = feedback_case
    legacy = c.repo.memory().save_context(
        c.actors[0], ContextInput(name="Bestehendes Projekt", kind="project")
    )
    fid = str(uuid4())
    c.admin.execute(
        "insert into atp.memory_feedback(id,org_id,context_id,created_by,kind,text,briefing_id,expires_at) "
        "values(%s,%s,%s,%s,'outcome','Bestehende Rückmeldung',%s,now()+interval '1 day')",
        (fid, c.actors[0].org_id, legacy["id"], c.actors[0].user_id, c.result["briefing_id"]),
    )
    with TestClient(c.app) as client:
        item = client.get(c.base, headers=headers(c.actors[0])).json()["items"][0]
        assert item["id"] == fid and not item["automatic_reuse"]
        assert client.post(c.base + "/" + fid + "/remember", headers=headers(c.actors[0])).status_code == 404
    assert not next_snapshot(c)["entries"]
