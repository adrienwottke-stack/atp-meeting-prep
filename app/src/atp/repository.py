"""Postgres persistence and fenced, resumable jobs.

Normal requests always run as ``authenticated`` with transaction-local JWT claims.
The separate worker login is the only route that can write canonical research results.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .history import history_timing
from .job_support import active_organization_jobs
from .models import Actor


def _clean(value):
    return json.loads(
        json.dumps(value, default=lambda x: x.isoformat() if isinstance(x, datetime) else str(x))
    )


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _valid_uuid(value):
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _usage(current: dict, incoming: dict) -> dict:
    """Counters never move backwards, including on stale/retried checkpoints."""
    merged = dict(current)
    for key, value in incoming.items():
        if isinstance(value, (int, float)):
            merged[key] = max(current.get(key, 0), value)
        elif key == "phase_seconds":
            merged[key] = {k: max(current.get(key, {}).get(k, 0), v) for k, v in value.items()}
            merged[key] = {**current.get(key, {}), **merged[key]}
        elif key == "errors":
            merged[key] = list(dict.fromkeys([*current.get(key, []), *value]))
        else:
            merged[key] = value
    return merged


class Repository:
    def __init__(self, settings, memory_adapter=None):
        self.settings = settings
        self._memory_adapter = memory_adapter

    def memory(self):
        """Single composition point for a future ATP database/API adapter."""
        if self._memory_adapter is None:
            from .memory_store import PostgresMemoryStore

            self._memory_adapter = PostgresMemoryStore(self)
        return self._memory_adapter

    def connection(self, actor=None, *, worker=False):
        """Explicit public transaction boundary for related storage adapters."""
        return self._connection(actor, worker=worker)

    @contextmanager
    def _connection(self, actor: Actor | None = None, *, worker=False):
        dsn = self.settings.worker_database_url if worker else self.settings.database_url
        if not dsn:
            raise ValueError("DATABASE_NOT_CONFIGURED")
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as connection:
            if not worker:
                if actor is None or not _valid_uuid(actor.user_id):
                    raise ValueError("UNAUTHENTICATED")
                connection.execute("SET LOCAL ROLE authenticated")
                claims = {"sub": actor.user_id, "role": "authenticated"}
                connection.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
            yield connection

    def resolve_actor(self, user_id: str, token: str = "", session_id: str | None = None) -> Actor | None:
        if not _valid_uuid(user_id):
            return None
        candidate = Actor(user_id=user_id, org_id=str(UUID(int=0)), token=token)
        with self._connection(candidate) as db:
            rows = db.execute(
                "select org_id,role from atp.memberships where user_id=%s and active order by created_at",
                (user_id,),
            ).fetchall()
            # Multi-organisation selection needs an explicit validated organisation switcher.
            if len(rows) != 1:
                return None
            if getattr(self.settings, "auth_verify_session", True):
                if not _valid_uuid(session_id):
                    return None
                if not db.execute("select atp_private.session_active(%s) as ok", (session_id,)).fetchone()[
                    "ok"
                ]:
                    return None
            return Actor(user_id=user_id, org_id=str(rows[0]["org_id"]), role=rows[0]["role"], token=token)

    def save_draft(self, actor, preview, warnings, usage) -> str:
        with self._connection(actor) as db:
            row = db.execute(
                "insert into atp.drafts(owner_id,org_id,preview,warnings,usage) values(%s,%s,%s,%s,%s) returning id",
                (actor.user_id, actor.org_id, Jsonb(_clean(preview)), Jsonb(warnings), Jsonb(_clean(usage))),
            ).fetchone()
            return str(row["id"])

    def get_draft(self, actor, draft_id) -> dict | None:
        if not _valid_uuid(draft_id):
            return None
        with self._connection(actor) as db:
            row = db.execute(
                "select id,preview,warnings,usage,created_at from atp.drafts where id=%s", (draft_id,)
            ).fetchone()
            return _clean(row) if row else None

    def _create_run(self, db, actor, meeting_id, input_dict, profile, parse_usage=None):
        version = db.execute(
            "select coalesce(max(result_version),0)+1 as version from atp.runs where meeting_id=%s",
            (meeting_id,),
        ).fetchone()["version"]
        profile_version = str(profile.get("profile_id") or "")
        if not profile_version:
            raise ValueError("PROFILE_NOT_CONFIGURED")
        digest = _hash(profile)
        # The administration command uses the corresponding exclusive profile lock.
        # This protects the active selection through commit without granting UPDATE
        # on the profile registry to ordinary users merely to take a row lock.
        db.execute(
            "select pg_advisory_xact_lock_shared(hashtextextended(%s,0))", ("profile:" + actor.org_id,)
        )
        saved = db.execute(
            "select version,content_hash from atp.profile_versions where org_id=%s and active",
            (actor.org_id,),
        ).fetchone()
        if not saved:
            raise ValueError("PROFILE_NOT_ACTIVE")
        if saved["version"] != profile_version or saved["content_hash"] != digest:
            raise ValueError("PROFILE_VERSION_CONFLICT")
        run_id = str(uuid4())
        memory_snapshot = {}
        if self.settings.memory_available(actor):
            memory_snapshot = self.memory().snapshot(
                actor,
                input_dict.get("context_ids", []),
                input_dict.get("meeting_type", "setup"),
                db,
                query=input_dict.get("goal", ""),
                company=input_dict.get("company"),
            )
        elif input_dict.get("context_ids"):
            raise ValueError("MEMORY_DISABLED")
        usage = _clean(parse_usage or {})
        db.execute(
            "insert into atp.runs(id,meeting_id,org_id,created_by,result_version,input_snapshot,clarified_input,profile,profile_version,profile_hash,usage,memory_snapshot) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id,
                meeting_id,
                actor.org_id,
                actor.user_id,
                version,
                Jsonb(input_dict),
                Jsonb(input_dict),
                Jsonb(profile),
                profile_version,
                digest,
                Jsonb(usage),
                Jsonb(memory_snapshot),
            ),
        )
        if memory_snapshot:
            self.memory().link(db, memory_snapshot, company_run_id=run_id)
        db.execute(
            "insert into atp.jobs(run_id,org_id,usage,queue) values(%s,%s,%s,%s)",
            (run_id, actor.org_id, Jsonb(usage), self.settings.worker_queue),
        )
        return {
            "meeting_id": str(meeting_id),
            "run_id": run_id,
            "status": "queued",
            "result_version": version,
        }

    def create_preparation(self, actor, input_dict, profile, idempotency_key, parse_usage=None) -> dict:
        input_dict, profile = _clean(input_dict), _clean(profile)
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        digest = _hash({"input": input_dict, "profile": profile, "parse_usage": _clean(parse_usage or {})})
        with self._connection(actor) as db:
            # A key race is serialized before any meeting is created.
            db.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{actor.user_id}:{idempotency_key}",),
            )
            previous = db.execute(
                "select * from atp.idempotency_keys where owner_id=%s and key=%s",
                (actor.user_id, idempotency_key),
            ).fetchone()
            if previous:
                if previous["payload_hash"] != digest:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                run = db.execute("select status from atp.runs where id=%s", (previous["run_id"],)).fetchone()
                if not run:
                    raise ValueError("NOT_FOUND")
                return {
                    "meeting_id": str(previous["meeting_id"]),
                    "run_id": str(previous["run_id"]),
                    "status": run["status"],
                }
            meeting_id = str(uuid4())
            db.execute(
                "insert into atp.meetings(id,owner_id,org_id,input,company_name,scheduled_at) values(%s,%s,%s,%s,%s,%s)",
                (
                    meeting_id,
                    actor.user_id,
                    actor.org_id,
                    Jsonb(input_dict),
                    input_dict.get("company", {}).get("name", ""),
                    input_dict.get("scheduled_at"),
                ),
            )
            for person in input_dict.get("people", []):
                db.execute(
                    "insert into atp.participants(id,meeting_id,data) values(%s,%s,%s)",
                    (person["participant_id"], meeting_id, Jsonb(person)),
                )
            result = self._create_run(db, actor, meeting_id, input_dict, profile, parse_usage)
            db.execute(
                "insert into atp.idempotency_keys(owner_id,key,payload_hash,meeting_id,run_id) values(%s,%s,%s,%s,%s)",
                (actor.user_id, idempotency_key, digest, meeting_id, result["run_id"]),
            )
            return result

    def list_meetings(self, actor, query="") -> list[dict]:
        with self._connection(actor) as db:
            rows = db.execute(
                "select * from atp.meetings where company_name ilike %s or coalesce(scheduled_at::text,'') ilike %s order by updated_at desc limit 200",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
            grouped = {}
            if rows:
                runs = db.execute(
                    "select r.meeting_id,r.id as run_id,r.status,r.phase,r.result_version,r.created_at,"
                    "r.briefing_id,r.pdf_status,j.status as job_status,j.lease_until,j.usage_saved_at,"
                    "j.usage as job_usage from atp.runs r left join atp.jobs j on j.run_id=r.id "
                    "where r.meeting_id=any(%s::uuid[]) order by r.result_version desc",
                    ([str(row["id"]) for row in rows],),
                ).fetchall()
                now = datetime.now(UTC)
                for run in runs:
                    timing = history_timing(run, self.settings.deadline_seconds, now)
                    for internal in ("job_status", "lease_until", "usage_saved_at", "job_usage"):
                        run.pop(internal, None)
                    run.update(timing)
                    grouped.setdefault(str(run["meeting_id"]), []).append(
                        {k: v for k, v in run.items() if k != "meeting_id"}
                    )
            output = []
            for row in rows:
                runs = grouped.get(str(row["id"]), [])
                output.append(
                    {
                        **_clean(row),
                        "meeting_id": str(row["id"]),
                        "runs": _clean(runs),
                        "latest_run_id": str(runs[0]["run_id"]) if runs else None,
                        "status": runs[0]["status"] if runs else "draft",
                    }
                )
            return output

    def get_meeting(self, actor, id) -> dict | None:
        if not _valid_uuid(id):
            return None
        with self._connection(actor) as db:
            row = db.execute("select * from atp.meetings where id=%s", (id,)).fetchone()
            if not row:
                return None
            row["meeting_id"] = str(row["id"])
            row["runs"] = db.execute(
                "select id as run_id,status,result_version,created_at,briefing_id,pdf_status from atp.runs where meeting_id=%s order by result_version desc",
                (id,),
            ).fetchall()
            return _clean(row)

    def can_edit(self, actor, meeting_id) -> bool:
        if not _valid_uuid(meeting_id):
            return False
        with self._connection(actor) as db:
            return db.execute("select atp_private.meeting_allowed(%s,true) as ok", (meeting_id,)).fetchone()[
                "ok"
            ]

    @contextmanager
    def storage_lock(self, meeting_id, timeout=5):
        """Serialise PDF writes/finalisation and deletion across web/worker processes.

        Callers recheck the current object/lease *inside* this lock before writing.
        A process crash releases the transaction lock automatically.
        """
        if not _valid_uuid(meeting_id):
            raise ValueError("NOT_FOUND")
        if timeout <= 0:
            raise ValueError("STORAGE_BUSY")
        try:
            with self._connection(worker=True) as db:
                db.execute(
                    "select set_config('lock_timeout',%s,true)", (str(max(1, int(timeout * 1000))) + "ms",)
                )
                db.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s,0))", ("storage:" + str(meeting_id),)
                )
                yield
        except psycopg.errors.LockNotAvailable as exc:
            raise ValueError("STORAGE_BUSY") from exc

    def get_run(self, actor, id) -> dict | None:
        if not _valid_uuid(id):
            return None
        with self._connection(actor) as db:
            row = db.execute("select * from atp.runs where id=%s", (id,)).fetchone()
            if not row:
                return None
            row["run_id"] = str(row["id"])
            row["input"] = row["clarified_input"]
            if row["status"] == "needs_clarification":
                checkpoint = db.execute("select checkpoint from atp.jobs where run_id=%s", (id,)).fetchone()
                row["independent_result"] = (checkpoint["checkpoint"] if checkpoint else {}).get(
                    "independent_result"
                )
            return _clean(row)

    def get_briefing(self, actor, id) -> dict | None:
        if not _valid_uuid(id):
            return None
        with self._connection(actor) as db:
            row = db.execute("select * from atp.briefings where id=%s", (id,)).fetchone()
            if not row:
                return None
            decision = db.execute(
                "select data from atp.decisions where meeting_id=%s and run_id=%s order by created_at desc limit 1",
                (row["meeting_id"], row["run_id"]),
            ).fetchone()
            return {
                **row["result"],
                "decision": decision["data"] if decision else None,
                "pdf_status": row["pdf_status"],
                "pdf_path": row["pdf_path"],
                "pdf_error": row["pdf_error"],
            }

    def new_run(self, actor, meeting_id, input_dict, profile, idempotency_key=None) -> dict:
        if not _valid_uuid(meeting_id):
            raise ValueError("NOT_FOUND")
        input_dict, profile = _clean(input_dict), _clean(profile)
        if not idempotency_key or len(idempotency_key) > 200 or not idempotency_key.strip():
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        digest = _hash({"operation": "new_run", "meeting_id": meeting_id, "input": input_dict})
        with self._connection(actor) as db:
            db.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{actor.user_id}:{idempotency_key}",),
            )
            meeting = db.execute(
                "select * from atp.meetings where id=%s and atp_private.meeting_allowed(id,true) for update",
                (meeting_id,),
            ).fetchone()
            if not meeting:
                raise ValueError("NOT_FOUND")
            previous = db.execute(
                "select * from atp.idempotency_keys where owner_id=%s and key=%s",
                (actor.user_id, idempotency_key),
            ).fetchone()
            if previous:
                if previous["payload_hash"] != digest:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                run = db.execute(
                    "select status,result_version from atp.runs where id=%s", (previous["run_id"],)
                ).fetchone()
                if not run:
                    raise ValueError("NOT_FOUND")
                return {
                    "meeting_id": str(previous["meeting_id"]),
                    "run_id": str(previous["run_id"]),
                    "status": run["status"],
                    "result_version": run["result_version"],
                }
            db.execute(
                "update atp.meetings set input=%s,company_name=%s,scheduled_at=%s,updated_at=now() where id=%s",
                (
                    Jsonb(input_dict),
                    input_dict.get("company", {}).get("name", ""),
                    input_dict.get("scheduled_at"),
                    meeting_id,
                ),
            )
            db.execute("delete from atp.participants where meeting_id=%s", (meeting_id,))
            for person in input_dict.get("people", []):
                db.execute(
                    "insert into atp.participants(id,meeting_id,data) values(%s,%s,%s)",
                    (person["participant_id"], meeting_id, Jsonb(person)),
                )
            result = self._create_run(db, actor, meeting_id, input_dict, profile)
            db.execute(
                "insert into atp.idempotency_keys(owner_id,key,payload_hash,meeting_id,run_id) values(%s,%s,%s,%s,%s)",
                (actor.user_id, idempotency_key, digest, meeting_id, result["run_id"]),
            )
            return result

    def answer(self, actor, run_id, expected_version, answers: dict) -> dict:
        if not _valid_uuid(run_id):
            raise ValueError("NOT_FOUND")
        with self._connection(actor) as db:
            run = db.execute(
                "select * from atp.runs where id=%s and atp_private.meeting_allowed(meeting_id,true) for update",
                (run_id,),
            ).fetchone()
            if not run:
                raise ValueError("NOT_FOUND")
            if run["version"] != expected_version:
                raise ValueError("VERSION_CONFLICT")
            if run["status"] != "needs_clarification":
                raise ValueError("VERSION_CONFLICT")
            questions = run["clarifications"]
            valid = {q["question_id"] for q in questions}
            if not answers or not set(answers) <= valid:
                raise ValueError("INVALID_ANSWER")
            from .answers import resolve_answers

            clarified, questions, all_answers = resolve_answers(
                run["clarified_input"], questions, answers, run["answers"], actor.user_id
            )
            pending = any(q.get("answer") is None for q in questions)
            status = "needs_clarification" if pending else "queued"
            db.execute(
                "update atp.runs set answers=%s,clarifications=%s,clarified_input=%s,version=version+1,status=%s,updated_at=now() where id=%s",
                (Jsonb(all_answers), Jsonb(questions), Jsonb(clarified), status, run_id),
            )
            if not pending:
                db.execute(
                    "update atp.jobs set status='queued',lease_token=null,lease_until=null,updated_at=now() where run_id=%s and status='paused'",
                    (run_id,),
                )
            return {
                "run_id": run_id,
                "meeting_id": str(run["meeting_id"]),
                "status": status,
                "version": expected_version + 1,
                "clarifications": questions,
            }

    def save_decision(self, actor, meeting_id, decision: dict):
        if not _valid_uuid(meeting_id) or not _valid_uuid(decision.get("run_id")):
            raise ValueError("NOT_FOUND")
        with self._connection(actor) as db:
            briefing = db.execute(
                "select result from atp.briefings where meeting_id=%s and run_id=%s and atp_private.meeting_allowed(meeting_id,true)",
                (meeting_id, decision["run_id"]),
            ).fetchone()
            if not briefing:
                raise ValueError("NOT_FOUND")
            original = briefing["result"]["recommendation"]["value"]
            from .models import RecommendationValue

            confirmed = RecommendationValue(decision["confirmed_decision"]).value
            if confirmed != original and not decision.get("override_reason", "").strip():
                raise ValueError("OVERRIDE_REASON_REQUIRED")
            data = {
                "original_recommendation": original,
                "confirmed_decision": confirmed,
                "decided_by": actor.user_id,
                "decided_at": datetime.now(UTC).isoformat(),
                "override_reason": decision.get("override_reason", ""),
            }
            db.execute(
                "insert into atp.decisions(meeting_id,run_id,decided_by,data) values(%s,%s,%s,%s)",
                (meeting_id, decision["run_id"], actor.user_id, Jsonb(data)),
            )
            return data

    def delete_meeting(self, actor, id):
        if not _valid_uuid(id):
            raise ValueError("NOT_FOUND")
        with self._connection(actor) as db:
            if not db.execute("select atp_private.request_deletion(%s) as ok", (id,)).fetchone()["ok"]:
                raise ValueError("NOT_FOUND")

    def retry_pdf(self, actor, briefing_id, pdf_path, pdf_error):
        with self._connection(actor) as db:
            row = db.execute(
                "update atp.briefings set pdf_path=%s,pdf_error=%s,pdf_status=%s where id=%s returning run_id",
                (pdf_path, pdf_error, "available" if pdf_path and not pdf_error else "failed", briefing_id),
            ).fetchone()
            if not row:
                raise ValueError("NOT_FOUND")
            db.execute(
                "update atp.runs set pdf_status=%s where id=%s",
                ("available" if pdf_path and not pdf_error else "failed", row["run_id"]),
            )

    def resume_research_gaps(self) -> int:
        """Resume older research-only pauses without inventing user answers.

        Queue, deletion and row-lock guards match claiming. Mixed batches with
        genuine unanswered user questions remain paused.
        """
        from .clarification import CLARIFICATION_VERSION

        with self._connection(worker=True) as db:
            jobs = db.execute(
                """select j.id,j.run_id,j.checkpoint,r.clarifications
                from atp.jobs j join atp.runs r on r.id=j.run_id
                join atp.meetings m on m.id=r.meeting_id
                where j.queue=%s and j.status='paused' and r.status='needs_clarification'
                and m.deleted_at is null
                and not exists(select 1 from atp.deletion_tombstones t where t.meeting_id=m.id)
                and exists(select 1 from jsonb_array_elements(r.clarifications) q
                    where coalesce(q->>'answer','')='' and q->>'code'='K06')
                and not exists(select 1 from jsonb_array_elements(r.clarifications) q
                    where coalesce(q->>'answer','')='' and q->>'code' is distinct from 'K06')
                order by j.created_at for update of r,j skip locked limit 50""",
                (self.settings.worker_queue,),
            ).fetchall()
            for job in jobs:
                checkpoint = {**job["checkpoint"], "research_gap_policy": CLARIFICATION_VERSION}
                retained = [q for q in job["clarifications"] if q.get("code") != "K06"]
                db.execute(
                    "update atp.runs set status='queued',clarifications=%s,version=version+1,updated_at=now() where id=%s",
                    (Jsonb(retained), job["run_id"]),
                )
                db.execute(
                    "update atp.jobs set status='queued',checkpoint=%s,lease_token=null,lease_until=null,updated_at=now() where id=%s",
                    (Jsonb(checkpoint), job["id"]),
                )
            return len(jobs)

    def claim_job(self, worker_id) -> dict | None:
        self.resume_research_gaps()
        with self._connection(worker=True) as db:
            candidates = db.execute(
                "select j.*,r.meeting_id from atp.jobs j join atp.runs r on r.id=j.run_id join atp.meetings m on m.id=r.meeting_id where j.queue=%s and m.deleted_at is null and not exists(select 1 from atp.deletion_tombstones t where t.meeting_id=m.id) and (j.status='queued' or (j.status='running' and j.lease_until<now())) order by j.created_at for update of j skip locked limit 50",
                (self.settings.worker_queue,),
            ).fetchall()
            for job in candidates:
                locked = db.execute(
                    "select pg_try_advisory_xact_lock(hashtextextended(%s,0)) as ok", (str(job["org_id"]),)
                ).fetchone()["ok"]
                if not locked:
                    continue
                active = active_organization_jobs(db, job["org_id"], exclude_job=job["id"])
                if active >= self.settings.org_concurrency:
                    continue
                now = datetime.now(UTC)
                usage = job["usage"]
                if job["status"] == "running" and job["lease_until"] and job["usage_saved_at"]:
                    # The unobserved interval could have contained provider work. Charge the
                    # entire granted lease, including crash uncertainty, instead of resetting it.
                    usage["active_seconds"] = usage.get("active_seconds", 0) + max(
                        0, (job["lease_until"] - job["usage_saved_at"]).total_seconds()
                    )
                token = str(uuid4())
                db.execute(
                    "update atp.jobs set status='running',worker_id=%s,lease_token=%s,lease_until=now()+(%s*interval '1 second'),heartbeat_at=now(),usage_saved_at=now(),attempt=attempt+1,usage=%s,updated_at=now() where id=%s",
                    (worker_id, token, self.settings.lease_seconds, Jsonb(usage), job["id"]),
                )
                run = db.execute("select * from atp.runs where id=%s", (job["run_id"],)).fetchone()
                db.execute(
                    "update atp.runs set status='researching',usage=%s,updated_at=now() where id=%s",
                    (Jsonb(usage), job["run_id"]),
                )
                return _clean(
                    {
                        "id": job["id"],
                        "run_id": job["run_id"],
                        "lease_token": token,
                        "checkpoint": job["checkpoint"],
                        "phase": run["phase"],
                        "input": run["clarified_input"],
                        "profile": run["profile"],
                        "profile_hash": run["profile_hash"],
                        "profile_version": run["profile_version"],
                        "result_version": run["result_version"],
                        "meeting_id": run["meeting_id"],
                        "org_id": run["org_id"],
                        "usage": usage,
                        "answers": run["answers"],
                        "clarifications": run["clarifications"],
                        "memory_snapshot": run.get("memory_snapshot", {}),
                        "created_by": str(run["created_by"]),
                        "attempt": job["attempt"] + 1,
                        "claimed_at": now,
                    }
                )
            return None

    def heartbeat(self, job_id, lease_token) -> bool:
        with self._connection(worker=True) as db:
            row = db.execute(
                "update atp.jobs j set lease_until=now()+(%s*interval '1 second'),heartbeat_at=now(),updated_at=now() where j.id=%s and j.lease_token=%s and j.status='running' and j.lease_until>now() and exists(select 1 from atp.runs r join atp.meetings m on m.id=r.meeting_id where r.id=j.run_id and m.deleted_at is null) returning j.id",
                (self.settings.lease_seconds, job_id, lease_token),
            ).fetchone()
            return row is not None

    def _owned_job(self, db, job_id, token):
        return db.execute(
            "select j.* from atp.jobs j join atp.runs r on r.id=j.run_id join atp.meetings m on m.id=r.meeting_id where j.id=%s and j.lease_token=%s and j.status='running' and j.lease_until>now() and m.deleted_at is null for update of j",
            (job_id, token),
        ).fetchone()

    def checkpoint(self, job_id, lease_token, phase, data: dict, usage: dict) -> bool:
        with self._connection(worker=True) as db:
            job = self._owned_job(db, job_id, lease_token)
            if not job:
                return False
            merged = _usage(job["usage"], _clean(usage))
            checkpoint = {**_clean(data), "phase": phase}
            db.execute(
                "update atp.jobs set checkpoint=%s,usage=%s,usage_saved_at=now(),updated_at=now() where id=%s",
                (Jsonb(checkpoint), Jsonb(merged), job_id),
            )
            status = (
                phase if phase in ("validating", "researching", "evaluating", "generating") else "researching"
            )
            db.execute(
                "update atp.runs set phase=%s,status=%s,usage=%s,updated_at=now() where id=%s",
                (phase, status, Jsonb(merged), job["run_id"]),
            )
            return True

    def pause_job(self, job_id, lease_token, questions: list[dict], checkpoint: dict, usage: dict):
        with self._connection(worker=True) as db:
            job = self._owned_job(db, job_id, lease_token)
            if not job:
                return False
            merged = _usage(job["usage"], _clean(usage))
            db.execute(
                "update atp.jobs set status='paused',checkpoint=%s,usage=%s,usage_saved_at=now(),lease_token=null,lease_until=null,updated_at=now() where id=%s",
                (Jsonb(_clean(checkpoint)), Jsonb(merged), job_id),
            )
            db.execute(
                "update atp.runs set status='needs_clarification',clarifications=%s,version=version+1,usage=%s,updated_at=now() where id=%s",
                (Jsonb(_clean(questions)), Jsonb(merged), job["run_id"]),
            )
            return True

    def finish_job(
        self, job_id, lease_token, result: dict, pdf_path: str | None, pdf_error: str | None
    ) -> bool:
        with self._connection(worker=True) as db:
            # Memory erasure locks contexts before meeting/job rows.
            metadata = db.execute(
                "select r.meeting_id,r.memory_snapshot from atp.jobs j join atp.runs r on r.id=j.run_id where j.id=%s",
                (job_id,),
            ).fetchone()
            if not metadata:
                return False
            for context in sorted(
                metadata.get("memory_snapshot", {}).get("contexts", []), key=lambda c: c["id"]
            ):
                db.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s,0))", ("memory:" + context["id"],)
                )
            meeting = db.execute(
                "select * from atp.meetings where id=%s and deleted_at is null for update",
                (metadata["meeting_id"],),
            ).fetchone()
            if not meeting:
                return False
            job = self._owned_job(db, job_id, lease_token)
            if not job:
                return False
            run = db.execute("select * from atp.runs where id=%s", (job["run_id"],)).fetchone()
            memory_snapshot = run.get("memory_snapshot", {})
            if memory_snapshot:
                self.memory().validate_snapshot(
                    Actor(user_id=str(run["created_by"]), org_id=str(run["org_id"])), memory_snapshot
                )
            result = _clean(result)
            if (
                str(result.get("run_id")) != str(run["id"])
                or str(result.get("meeting_id")) != str(run["meeting_id"])
                or result.get("result_version") != run["result_version"]
            ):
                raise ValueError("RESULT_ID_MISMATCH")
            if (
                result.get("profile_hash") != run["profile_hash"]
                or result.get("profile_version") != run["profile_version"]
            ):
                raise ValueError("RESULT_PROFILE_MISMATCH")
            status = "available" if pdf_path and not pdf_error else "failed"
            db.execute(
                "insert into atp.briefings(id,run_id,meeting_id,org_id,result_version,result,pdf_path,pdf_error,pdf_status) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict(run_id) do nothing",
                (
                    result["briefing_id"],
                    run["id"],
                    run["meeting_id"],
                    run["org_id"],
                    run["result_version"],
                    Jsonb(result),
                    pdf_path,
                    pdf_error,
                    status,
                ),
            )
            existing = db.execute("select id from atp.briefings where run_id=%s", (run["id"],)).fetchone()
            for source in result.get("sources", []):
                db.execute(
                    "insert into atp.sources(run_id,id,data) values(%s,%s,%s) on conflict do nothing",
                    (run["id"], source["source_id"], Jsonb(source)),
                )
            for fact in result.get("facts", []):
                db.execute(
                    "insert into atp.facts(run_id,id,data) values(%s,%s,%s) on conflict do nothing",
                    (run["id"], fact["fact_id"], Jsonb(fact)),
                )
            usage = _usage(job["usage"], result.get("usage", {}))
            db.execute(
                "update atp.runs set status=%s,phase=%s,usage=%s,briefing_id=%s,pdf_status=%s,updated_at=now() where id=%s",
                (result["status"], result["status"], Jsonb(usage), existing["id"], status, run["id"]),
            )
            db.execute(
                "update atp.jobs set status='completed',usage=%s,lease_token=null,lease_until=null,updated_at=now() where id=%s",
                (Jsonb(usage), job_id),
            )
            return True

    def fail_job(self, job_id, lease_token, code):
        with self._connection(worker=True) as db:
            job = self._owned_job(db, job_id, lease_token)
            if not job:
                return False
            db.execute(
                "update atp.jobs set status='failed',lease_token=null,lease_until=null,updated_at=now() where id=%s",
                (job_id,),
            )
            db.execute(
                "update atp.runs set status='failed',error=%s,updated_at=now() where id=%s",
                (str(code)[:100], job["run_id"]),
            )
            return True

    def claim_deletion(self) -> dict | None:
        with self._connection(worker=True) as db:
            row = db.execute(
                "select * from atp.deletion_jobs where status='queued' or (status='running' and lease_until<now()) order by created_at for update skip locked limit 1"
            ).fetchone()
            if not row:
                return None
            db.execute(
                "update atp.deletion_jobs set status='running',attempt=attempt+1,lease_until=now()+interval '90 seconds' where meeting_id=%s",
                (row["meeting_id"],),
            )
            db.execute(
                "insert into atp.deletion_tombstones(meeting_id,org_id,requested_at) values(%s,%s,%s) on conflict do nothing",
                (row["meeting_id"], row["org_id"], row["created_at"]),
            )
            return _clean(row)

    def finish_deletion(self, meeting_id):
        with self._connection(worker=True) as db:
            job = db.execute(
                "select * from atp.deletion_jobs where meeting_id=%s and status='running' for update",
                (meeting_id,),
            ).fetchone()
            if not job:
                return
            meeting = db.execute("select deleted_at from atp.meetings where id=%s", (meeting_id,)).fetchone()
            if not meeting or not meeting["deleted_at"]:
                raise ValueError("DELETION_NOT_REQUESTED")
            db.execute("delete from atp.meetings where id=%s", (meeting_id,))
            db.execute(
                "update atp.deletion_tombstones set purged_at=now() where meeting_id=%s", (meeting_id,)
            )

    def enqueue_storage_cleanup(self, path, org_id, meeting_id, run_id):
        try:
            expected = "/".join([str(UUID(str(x))) for x in (org_id, meeting_id, run_id)] + ["result.pdf"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("INVALID_STORAGE_PATH") from exc
        if path != expected:
            raise ValueError("INVALID_STORAGE_PATH")
        with self._connection(worker=True) as db:
            db.execute(
                "insert into atp.storage_cleanup(path,org_id,meeting_id,run_id) values(%s,%s,%s,%s) "
                "on conflict(path) do update set next_attempt=least(atp.storage_cleanup.next_attempt,now())",
                (path, org_id, meeting_id, run_id),
            )

    def pending_storage_cleanup(self) -> dict | None:
        with self._connection(worker=True) as db:
            row = db.execute(
                "select * from atp.storage_cleanup where next_attempt<=now() order by next_attempt,created_at limit 1"
            ).fetchone()
            return _clean(row) if row else None

    def finish_storage_cleanup(self, path):
        with self._connection(worker=True) as db:
            db.execute("delete from atp.storage_cleanup where path=%s", (path,))

    def defer_storage_cleanup(self, path):
        with self._connection(worker=True) as db:
            db.execute(
                "update atp.storage_cleanup set attempt=attempt+1,next_attempt=now()+interval '60 seconds' where path=%s",
                (path,),
            )

    def pdf_path_in_use(self, path) -> bool:
        with self._connection(worker=True) as db:
            return db.execute(
                "select exists(select 1 from atp.briefings b join atp.meetings m on m.id=b.meeting_id "
                "where b.pdf_path=%s and b.pdf_status='available' and m.deleted_at is null "
                "and not exists(select 1 from atp.deletion_tombstones t where t.meeting_id=m.id)) as ok",
                (path,),
            ).fetchone()["ok"]
