"""Real local Postgres/RLS tests. Never replace persistence with an in-memory fake."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from atp.config import Settings
from atp.models import Actor, MeetingInput
from atp.profiles import load_profile, profile_hash
from atp.repository import Repository
from atp.storage import Storage


@pytest.fixture
def database_case(tmp_path):
    settings = Settings()
    status_path = Path(__file__).resolve().parents[1] / "output" / "setup" / "supabase-status.json"
    if (
        settings.app_mode != "local"
        or not status_path.exists()
        or "127.0.0.1:55432" not in settings.database_url
    ):
        pytest.skip("Isolated local Supabase not configured; run scripts/setup_local.ps1.")
    status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    if status["API_URL"] != "http://127.0.0.1:55431":
        pytest.skip("Refusing a non-ATP local database.")
    org = str(uuid4())
    users = [str(uuid4()), str(uuid4())]
    profile = load_profile()
    with psycopg.connect(status["DB_URL"], autocommit=True) as admin:
        for user in users:
            admin.execute(
                "insert into auth.users(id,email,aud,role) values(%s,%s,'authenticated','authenticated')",
                (user, user + "@repository.example.test"),
            )
            admin.execute("insert into atp.memberships(user_id,org_id) values(%s,%s)", (user, org))
        admin.execute(
            "insert into atp.profile_versions(org_id,version,content_hash,content,active,activated_by) values(%s,%s,%s,%s,true,%s)",
            (org, profile["profile_id"], profile_hash(profile), Jsonb(profile), users[0]),
        )
        actors = [Actor(user_id=user, org_id=org) for user in users]
        data = MeetingInput(
            company={"name": "Integration GmbH", "website": "https://integration.example"},
            people=[
                {"name": "Person A", "participant_id": "person-a"},
                {"name": "Person B", "participant_id": "person-b"},
            ],
            meeting_type="setup",
            relationship="new_contact",
            confirmed=True,
            public_identifiers_confirmed=True,
        ).model_dump(mode="json")
        case = SimpleNamespace(
            repo=Repository(settings),
            settings=settings,
            admin=admin,
            actors=actors,
            profile=profile,
            data=data,
            org=org,
            tmp_path=tmp_path,
        )
        try:
            yield case
        finally:
            admin.execute("delete from atp.storage_cleanup where org_id=%s", (org,))
            admin.execute("delete from atp.meetings where org_id=%s", (org,))
            admin.execute("delete from atp.deletion_tombstones where org_id=%s", (org,))
            admin.execute("delete from atp.profile_activations where org_id=%s", (org,))
            admin.execute("delete from atp.profile_versions where org_id=%s", (org,))
            admin.execute("delete from atp.drafts where org_id=%s", (org,))
            for user in users:
                admin.execute("delete from auth.users where id=%s", (user,))


def create(case):
    result = case.repo.create_preparation(case.actors[0], case.data, case.profile, str(uuid4()))
    case.admin.execute("update atp.jobs set created_at='1900-01-01' where run_id=%s", (result["run_id"],))
    return result


def test_rls_two_users_and_explicit_read_edit_share(database_case):
    c = database_case
    created = create(c)
    a, b = c.actors
    assert c.repo.get_meeting(b, created["meeting_id"]) is None
    assert c.repo.get_run(b, created["run_id"]) is None
    assert c.repo.list_meetings(b) == []
    with pytest.raises(ValueError, match="NOT_FOUND"):
        c.repo.delete_meeting(b, created["meeting_id"])
    # Same organisation gives no access; the explicit grant does.
    with c.repo._connection(a) as db:
        db.execute(
            "insert into atp.shares(meeting_id,user_id,permission,granted_by) values(%s,%s,'read',%s)",
            (created["meeting_id"], b.user_id, a.user_id),
        )
    assert c.repo.get_run(b, created["run_id"])
    with pytest.raises(ValueError, match="NOT_FOUND"):
        c.repo.new_run(b, created["meeting_id"], c.data, c.profile, str(uuid4()))
    with c.repo._connection(a) as db:
        db.execute(
            "delete from atp.shares where meeting_id=%s and user_id=%s", (created["meeting_id"], b.user_id)
        )
        db.execute(
            "insert into atp.shares(meeting_id,user_id,permission,granted_by) values(%s,%s,'edit',%s)",
            (created["meeting_id"], b.user_id, a.user_id),
        )
    assert c.repo.new_run(b, created["meeting_id"], c.data, c.profile, str(uuid4()))["result_version"] == 2
    c.admin.execute("update atp.memberships set active=false where user_id=%s", (b.user_id,))
    assert c.repo.get_meeting(b, created["meeting_id"]) is None


def test_idempotency_and_immutable_snapshots(database_case):
    c = database_case
    key = str(uuid4())
    first = c.repo.create_preparation(c.actors[0], c.data, c.profile, key)
    assert c.repo.create_preparation(c.actors[0], c.data, c.profile, key)["run_id"] == first["run_id"]
    modified = {**c.data, "goal": "Different goal"}
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        c.repo.create_preparation(c.actors[0], modified, c.profile, key)
    # Opaque participant IDs can be reused in another meeting without collisions.
    assert (
        c.repo.create_preparation(c.actors[0], c.data, c.profile, str(uuid4()))["meeting_id"]
        != first["meeting_id"]
    )
    with pytest.raises(psycopg.Error, match="IMMUTABLE_SNAPSHOT"):
        c.admin.execute(
            "update atp.runs set input_snapshot=%s where id=%s", (Jsonb(modified), first["run_id"])
        )


def test_real_checkpoint_recovery_budget_and_lease_fencing(database_case):
    c = database_case
    created = create(c)
    first = c.repo.claim_job("worker-one")
    assert first["run_id"] == created["run_id"]
    assert c.repo.checkpoint(
        first["id"],
        first["lease_token"],
        "evaluating",
        {"research": {"saved": True}},
        {"model_requests": 2, "search_calls": 1, "active_seconds": 7},
    )
    c.admin.execute(
        "update atp.jobs set lease_until=now()-interval '1 second',usage_saved_at=now()-interval '100 seconds' where id=%s",
        (first["id"],),
    )
    second = c.repo.claim_job("worker-two")
    assert second["lease_token"] != first["lease_token"]
    assert second["checkpoint"]["research"]["saved"]
    assert second["usage"]["active_seconds"] >= 105
    assert not c.repo.heartbeat(first["id"], first["lease_token"])
    assert not c.repo.checkpoint(first["id"], first["lease_token"], "generating", {}, {"model_requests": 0})
    assert not c.repo.finish_job(first["id"], first["lease_token"], {}, None, None)
    assert c.repo.checkpoint(
        second["id"], second["lease_token"], "generating", second["checkpoint"], {"model_requests": 1}
    )
    assert c.repo.get_run(c.actors[0], created["run_id"])["usage"]["model_requests"] == 2


def test_clarification_cas_preserves_snapshot_and_resumes(database_case):
    c = database_case
    created = create(c)
    job = c.repo.claim_job("clarification-worker")
    question = {
        "question_id": "K04:relationship",
        "code": "K04",
        "field": "relationship",
        "reason": "Unknown",
        "options": ["Neukontakt", "Laufendes Projekt"],
        "handling": "blocking_dependent_output",
    }
    assert c.repo.pause_job(
        job["id"],
        job["lease_token"],
        [question],
        {"saved": "checkpoint"},
        {"model_requests": 1, "active_seconds": 3},
    )
    run = c.repo.get_run(c.actors[0], created["run_id"])
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        c.repo.answer(
            c.actors[0], created["run_id"], run["version"] - 1, {"K04:relationship": "Laufendes Projekt"}
        )
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        c.repo.answer(c.actors[0], created["run_id"], run["version"], {"K04:relationship": "injected-role"})
    response = c.repo.answer(
        c.actors[0], created["run_id"], run["version"], {"K04:relationship": "Laufendes Projekt"}
    )
    assert response["status"] == "queued"
    resumed = c.repo.claim_job("resumed-worker")
    assert resumed["input"]["relationship"] == "ongoing_project"
    assert resumed["checkpoint"]["saved"] == "checkpoint"
    stored = c.repo.get_run(c.actors[0], created["run_id"])
    assert stored["input_snapshot"]["relationship"] == "new_contact"
    assert resumed["usage"]["model_requests"] == 1


def test_deletion_suppresses_access_tombstone_restore_and_purges(database_case):
    c = database_case
    created = create(c)
    job = c.repo.claim_job("delete-worker")
    storage = Storage(c.settings.model_copy(update={"output_dir": c.tmp_path}))
    path = storage.put_pdf(c.org, created["meeting_id"], created["run_id"], b"%PDF-1.4\nsynthetic-test")
    c.repo.delete_meeting(c.actors[0], created["meeting_id"])
    assert c.repo.get_meeting(c.actors[0], created["meeting_id"]) is None
    assert c.repo.get_run(c.actors[0], created["run_id"]) is None
    assert not c.repo.heartbeat(job["id"], job["lease_token"])
    # Restore simulation: the tombstone still prevents owner access.
    c.admin.execute("update atp.meetings set deleted_at=null where id=%s", (created["meeting_id"],))
    assert c.repo.get_meeting(c.actors[0], created["meeting_id"]) is None
    assert c.repo.list_meetings(c.actors[0]) == []
    c.admin.execute("update atp.meetings set deleted_at=now() where id=%s", (created["meeting_id"],))
    deletion = c.repo.claim_deletion()
    assert deletion["meeting_id"] == created["meeting_id"]
    storage.delete_meeting(c.org, created["meeting_id"])
    storage.delete_meeting(c.org, created["meeting_id"])
    c.repo.finish_deletion(created["meeting_id"])
    c.repo.finish_deletion(created["meeting_id"])
    with pytest.raises(FileNotFoundError):
        storage.get_pdf(path)
    assert (
        c.admin.execute("select count(*) from atp.runs where id=%s", (created["run_id"],)).fetchone()[0] == 0
    )
    assert c.admin.execute(
        "select purged_at is not null from atp.deletion_tombstones where meeting_id=%s",
        (created["meeting_id"],),
    ).fetchone()[0]


@pytest.mark.parametrize("choice", ["Profil bestätigen", "Person offen lassen"])
def test_k03_keeps_two_original_participants(database_case, choice):
    c = database_case
    created = create(c)
    job = c.repo.claim_job("k03-worker")
    question = {
        "question_id": "K03:person-a",
        "code": "K03",
        "field": "people",
        "participant_id": "person-a",
        "reason": "Two identities",
        "options": ["Profil bestätigen", "Person offen lassen"],
        "handling": "person_only",
    }
    c.repo.pause_job(job["id"], job["lease_token"], [question], {}, {})
    run = c.repo.get_run(c.actors[0], created["run_id"])
    c.repo.answer(c.actors[0], created["run_id"], run["version"], {"K03:person-a": choice})
    stored = c.repo.get_run(c.actors[0], created["run_id"])
    assert stored["input"]["people"] == c.data["people"]
    assert stored["input_snapshot"]["people"] == c.data["people"]
    assert len(stored["input"]["people"]) == 2


def test_active_profile_required_and_normal_role_never_bypasses_rls(database_case):
    c = database_case
    with c.repo._connection(c.actors[0]) as db:
        row = db.execute(
            "select current_user,session_user,current_setting('request.jwt.claims')::jsonb as claims"
        ).fetchone()
        assert row["current_user"] == "authenticated" and row["session_user"] == "atp_web"
        assert row["claims"]["sub"] == c.actors[0].user_id
    c.admin.execute("update atp.profile_versions set active=false where org_id=%s", (c.org,))
    with pytest.raises(ValueError, match="PROFILE_NOT_ACTIVE"):
        create(c)


def test_local_storage_blocks_path_escape(tmp_path):
    storage = Storage(SimpleNamespace(output_dir=tmp_path, app_mode="local"))
    with pytest.raises(ValueError, match="INVALID_STORAGE_PATH"):
        storage.get_pdf("../../private/result.pdf")
    with pytest.raises(ValueError):
        storage.put_pdf("bad-id", str(uuid4()), str(uuid4()), b"%PDF-test")


def test_storage_cleanup_survives_parent_purge_and_defers(database_case):
    c = database_case
    created = create(c)
    path = f"{c.org}/{created['meeting_id']}/{created['run_id']}/result.pdf"
    c.repo.enqueue_storage_cleanup(path, c.org, created["meeting_id"], created["run_id"])
    c.repo.enqueue_storage_cleanup(path, c.org, created["meeting_id"], created["run_id"])
    assert (
        c.admin.execute("select count(*) from atp.storage_cleanup where path=%s", (path,)).fetchone()[0] == 1
    )
    assert not c.repo.pdf_path_in_use(path)
    c.admin.execute(
        "insert into atp.briefings(id,run_id,meeting_id,org_id,result_version,result,pdf_path,pdf_status) values(%s,%s,%s,%s,1,'{}',%s,'available')",
        (str(uuid4()), created["run_id"], created["meeting_id"], c.org, path),
    )
    assert c.repo.pdf_path_in_use(path)
    c.admin.execute("delete from atp.meetings where id=%s", (created["meeting_id"],))
    assert not c.repo.pdf_path_in_use(path)
    assert (
        c.admin.execute("select count(*) from atp.storage_cleanup where path=%s", (path,)).fetchone()[0] == 1
    )
    c.repo.defer_storage_cleanup(path)
    assert c.admin.execute(
        "select attempt,next_attempt>now() from atp.storage_cleanup where path=%s", (path,)
    ).fetchone() == (1, True)
    c.repo.finish_storage_cleanup(path)
    c.repo.finish_storage_cleanup(path)
    assert (
        c.admin.execute("select count(*) from atp.storage_cleanup where path=%s", (path,)).fetchone()[0] == 0
    )


def test_storage_lock_is_cross_connection_and_bounded(database_case):
    c = database_case
    meeting_id = str(uuid4())

    def contender():
        try:
            with c.repo.storage_lock(meeting_id, timeout=0.05):
                return "unexpected-acquisition"
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with c.repo.storage_lock(meeting_id):
            assert pool.submit(contender).result(timeout=3) == "STORAGE_BUSY"
        with c.repo.storage_lock(meeting_id, timeout=1):
            pass


def test_partial_clarification_does_not_recreate_confirmed_person(database_case):
    c = database_case
    c.data["people"] = []
    c.data["company"]["name"] = ""
    created = create(c)
    job = c.repo.claim_job("partial-clarification-worker")
    questions = [
        {
            "question_id": "K01:people",
            "code": "K01",
            "field": "people",
            "reason": "Missing",
            "options": ["Angabe ergänzen oder korrigieren"],
            "handling": "blocking_input",
        },
        {
            "question_id": "K01:company.name",
            "code": "K01",
            "field": "company.name",
            "reason": "Missing",
            "options": ["Angabe ergänzen oder korrigieren"],
            "handling": "blocking_input",
        },
    ]
    c.repo.pause_job(job["id"], job["lease_token"], questions, {}, {})
    run = c.repo.get_run(c.actors[0], created["run_id"])
    c.repo.answer(c.actors[0], created["run_id"], run["version"], {"K01:people": "Bestätigter Kontakt"})
    partial = c.repo.get_run(c.actors[0], created["run_id"])
    original_people = partial["input"]["people"]
    c.repo.answer(c.actors[0], created["run_id"], partial["version"], {"K01:company.name": "Bestätigte GmbH"})
    final = c.repo.get_run(c.actors[0], created["run_id"])
    assert final["input"]["people"] == original_people
    assert final["input"]["company"]["name"] == "Bestätigte GmbH"
