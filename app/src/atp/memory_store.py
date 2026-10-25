"""PostgreSQL MemoryStore. Ordinary reads/writes always use the authenticated role."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .memory_models import ContextInput, EntryInput, FeedbackInput
from .repository import _clean, _hash


class PostgresMemoryStore:
    def __init__(self, repository):
        self.repo = repository
        self.settings = repository.settings

    def deadline(self):
        # Fixture tests also have an explicit retention period; no endless memory.
        if not self.settings.retention_days:
            raise ValueError("MEMORY_RETENTION_REQUIRED")
        return datetime.now(UTC) + timedelta(days=self.settings.retention_days)

    def _db(self, actor, db):
        return nullcontext(db) if db is not None else self.repo.connection(actor)

    def require(self, db, context_id, edit=False, owner=False):
        UUID(str(context_id))
        name = "context_owned" if owner else "context_allowed"
        args = (str(context_id),) if owner else (str(context_id), edit)
        row = db.execute(f"select atp_private.{name}({','.join(['%s'] * len(args))}) as ok", args).fetchone()
        if not row["ok"]:
            raise ValueError("NOT_FOUND")

    def contexts(self, actor):
        with self.repo.connection(actor) as db:
            return _clean(
                db.execute(
                    "select *,atp_private.context_owned(id) as owned,atp_private.context_allowed(id,true) as editable from atp.memory_contexts order by updated_at desc limit 200"
                ).fetchall()
            )

    def context(self, actor, context_id, db=None, query=""):
        with self._db(actor, db) as conn:
            self.require(conn, context_id)
            c = conn.execute(
                "select *,atp_private.context_owned(id) as owned,atp_private.context_allowed(id,true) as editable from atp.memory_contexts where id=%s",
                (context_id,),
            ).fetchone()
            c["entries"] = conn.execute(
                "select * from atp.memory_entries where context_id=%s and (text ilike %s or source ilike %s) order by updated_at desc limit 200",
                (context_id, "%" + query + "%", "%" + query + "%"),
            ).fetchall()
            c["shares"] = conn.execute(
                "select * from atp.memory_shares where context_id=%s", (context_id,)
            ).fetchall()
            c["feedback"] = conn.execute(
                "select * from atp.memory_feedback where context_id=%s order by created_at desc limit 100",
                (context_id,),
            ).fetchall()
            return _clean(c)

    def save_context(self, actor, value: ContextInput, context_id=None):
        with self.repo.connection(actor) as db:
            if context_id:
                self.require(db, context_id, owner=True)
                row = db.execute(
                    "update atp.memory_contexts set name=%s,description=%s,external_id=%s,updated_at=now() where id=%s and kind=%s returning *",
                    (value.name.strip(), value.description, value.external_id, context_id, value.kind),
                ).fetchone()
                if not row:
                    raise ValueError("CONTEXT_KIND_IMMUTABLE")
            else:
                context_id = str(uuid4())
                db.execute(
                    "insert into atp.memory_contexts(id,org_id,owner_id,kind,name,description,external_id,expires_at) values(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        context_id,
                        actor.org_id,
                        actor.user_id,
                        value.kind,
                        value.name.strip(),
                        value.description,
                        value.external_id,
                        self.deadline(),
                    ),
                )
                row = db.execute("select * from atp.memory_contexts where id=%s", (context_id,)).fetchone()
            return _clean(row)

    def share(self, actor, context_id, user_id, permission=None):
        with self.repo.connection(actor) as db:
            db.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("memory:" + context_id,))
            self.require(db, context_id, owner=True)
            if permission:
                # Do not expose the member directory or permit cross-tenant recipients.
                if not db.execute(
                    "select 1 from atp.memberships where user_id=%s and org_id=%s and active",
                    (user_id, actor.org_id),
                ).fetchone():
                    # membership_self RLS intentionally hides other members; check via worker
                    with self.repo.connection(worker=True) as privileged:
                        if not privileged.execute(
                            "select 1 from atp.memberships where user_id=%s and org_id=%s and active",
                            (user_id, actor.org_id),
                        ).fetchone():
                            raise ValueError("NOT_FOUND")
                db.execute(
                    "delete from atp.memory_shares where context_id=%s and user_id=%s", (context_id, user_id)
                )
                db.execute(
                    "insert into atp.memory_shares(context_id,user_id,permission) values(%s,%s,%s)",
                    (context_id, user_id, permission),
                )
            else:
                db.execute(
                    "delete from atp.memory_shares where context_id=%s and user_id=%s", (context_id, user_id)
                )

    def save_entry(
        self, actor, context_id, value: EntryInput, entry_id=None, db=None, new_id=None, expires_at=None
    ):
        with self._db(actor, db) as conn:
            conn.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("memory:" + context_id,))
            self.require(conn, context_id, edit=True)
            if not value.text.strip():
                raise ValueError("EMPTY_ENTRY")
            if entry_id:
                old = conn.execute(
                    "select * from atp.memory_entries where id=%s and context_id=%s for update",
                    (entry_id, context_id),
                ).fetchone()
                if not old:
                    raise ValueError("NOT_FOUND")
                if value.expected_version != old["version"]:
                    raise ValueError("VERSION_CONFLICT")
                conn.execute(
                    "insert into atp.memory_entry_versions(entry_id,version,context_id,data) values(%s,%s,%s,%s) on conflict do nothing",
                    (entry_id, old["version"], context_id, Jsonb(_clean(old))),
                )
                row = conn.execute(
                    "update atp.memory_entries set text=%s,kind=%s,source=%s,information_as_of=%s,status=%s,applies_to=%s,version=version+1,updated_at=now(),confirmed_by=%s,confirmed_at=%s where id=%s returning *",
                    (
                        value.text.strip(),
                        value.kind,
                        value.source,
                        value.information_as_of,
                        value.status,
                        Jsonb(value.applies_to),
                        actor.user_id if value.status == "confirmed" else None,
                        datetime.now(UTC) if value.status == "confirmed" else None,
                        entry_id,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    "insert into atp.memory_entries(id,context_id,org_id,kind,text,source,information_as_of,status,applies_to,created_by,confirmed_by,confirmed_at,expires_at) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                    (
                        new_id or str(uuid4()),
                        context_id,
                        actor.org_id,
                        value.kind,
                        value.text.strip(),
                        value.source,
                        value.information_as_of,
                        value.status,
                        Jsonb(value.applies_to),
                        actor.user_id,
                        actor.user_id if value.status == "confirmed" else None,
                        datetime.now(UTC) if value.status == "confirmed" else None,
                        min(self.deadline(), expires_at) if expires_at else self.deadline(),
                    ),
                ).fetchone()
            return _clean(row)

    def confirm_learning(self, actor, task_id, value):
        with self.repo.connection(actor) as db:
            task = db.execute(
                "select * from atp.memory_tasks where id=%s and kind='learning' and status='completed'",
                (task_id,),
            ).fetchone()
            if not task:
                raise ValueError("NOT_FOUND")
            cid = str(task["context_id"])
            self.require(db, cid, edit=True)
            self.validate_snapshot(actor, task["memory_snapshot"], db)
            db.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("confirm:" + task_id,))
            existing = db.execute(
                "select * from atp.memory_entries where id=%s and context_id=%s", (task_id, cid)
            ).fetchone()
            value = value.model_copy(
                update={
                    "kind": "learning",
                    "status": "confirmed",
                    "source": "Geprüftes Learning aus Feedback " + task["input"]["feedback_id"],
                }
            )
            if existing:
                if existing["text"] != value.text:
                    raise ValueError("VERSION_CONFLICT")
                return _clean(existing)
            return self.save_entry(actor, cid, value, db=db, new_id=task_id)

    def snapshot(self, actor, context_ids, meeting_type, db=None, query="", company=None):
        ids = list(dict.fromkeys(str(UUID(str(x))) for x in context_ids))
        with self._db(actor, db) as conn:
            if company:
                from .briefing_feedback import company_memory_key

                # Only this user's deliberately remembered feedback is automatic.
                # Exact website hosts prevent accidental reuse for a namesake or subsidiary.
                automatic = conn.execute(
                    "select c.id from atp.memory_contexts c where c.owner_id=%s and c.org_id=%s "
                    "and c.kind='company' and c.external_id=%s "
                    "and exists(select 1 from atp.memory_entries e where e.context_id=c.id "
                    "and e.status='confirmed') order by c.created_at,c.id limit 1",
                    (actor.user_id, actor.org_id, company_memory_key(company)),
                ).fetchone()
                if automatic:
                    cid = str(automatic["id"])
                    # Direct company feedback must not be crowded out by large manual contexts.
                    ids = [cid, *(value for value in ids if value != cid)]
            contexts = []
            for cid in ids:
                self.require(conn, cid)
                contexts.append(
                    conn.execute(
                        "select id,name,kind from atp.memory_contexts where id=%s", (cid,)
                    ).fetchone()
                )
            # General lessons are also subject to context ACLs and become explicit run links.
            general = conn.execute(
                "select id,name,kind from atp.memory_contexts where kind='general' order by created_at limit 10"
            ).fetchall()
            all_contexts = {str(x["id"]): x for x in [*contexts, *general]}
            entries = []
            chars = 0
            for cid, c in all_contexts.items():
                rows = conn.execute(
                    "select id,context_id,kind,text,source,information_as_of,version,confirmed_at,applies_to from atp.memory_entries where context_id=%s and status='confirmed' order by ts_rank(to_tsvector('simple',text),plainto_tsquery('simple',%s)) desc,updated_at desc limit 20",
                    (cid, query[:3000]),
                ).fetchall()
                for row in rows:
                    if row["applies_to"] and meeting_type not in row["applies_to"]:
                        continue
                    if c["kind"] == "general" and row["kind"] != "learning":
                        continue
                    if len(entries) >= 20 or chars + len(row["text"]) > 12000:
                        break
                    row["context_name"] = c["name"]
                    entries.append(row)
                    chars += len(row["text"])
            used = set(ids) | {str(e["context_id"]) for e in entries}
            return _clean(
                {
                    "schema_version": 1,
                    "contexts": [c for cid, c in all_contexts.items() if cid in used],
                    "entries": entries,
                    "captured_at": datetime.now(UTC),
                    "selection": (
                        "explicit_contexts_company_feedback_and_permitted_general_lessons"
                        if company
                        else "explicit_contexts_and_permitted_general_lessons"
                    ),
                }
            )

    def validate_snapshot(self, actor, snapshot, db=None):
        with self._db(actor, db) as conn:
            for context in snapshot.get("contexts", []):
                self.require(conn, context["id"])
            for item in snapshot.get("entries", []):
                row = conn.execute(
                    "select version,status from atp.memory_entries where id=%s and context_id=%s",
                    (item["id"], item["context_id"]),
                ).fetchone()
                if not row or row["version"] != item["version"] or row["status"] != "confirmed":
                    raise ValueError("MEMORY_CHANGED")

    def link(self, db, snapshot, *, company_run_id=None, task_id=None):
        for c in snapshot.get("contexts", []):
            db.execute(
                "insert into atp.memory_links(context_id,company_run_id,task_id) values(%s,%s,%s)",
                (c["id"], company_run_id, task_id),
            )

    def feedback(self, actor, value: FeedbackInput):
        with self.repo.connection(actor) as db:
            cid = str(value.context_id)
            self.require(db, cid, edit=True)
            if value.briefing_id:
                row = db.execute(
                    "select b.id from atp.briefings b join atp.memory_links l on l.company_run_id=b.run_id where b.id=%s and l.context_id=%s",
                    (str(value.briefing_id), cid),
                ).fetchone()
                if not row:
                    raise ValueError("NOT_FOUND")
            row = db.execute(
                "insert into atp.memory_feedback(org_id,context_id,created_by,kind,text,reference,briefing_id,task_id,expires_at) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                (
                    actor.org_id,
                    cid,
                    actor.user_id,
                    value.kind,
                    value.text,
                    value.reference,
                    str(value.briefing_id) if value.briefing_id else None,
                    None,
                    self.deadline(),
                ),
            ).fetchone()
            return _clean(row)

    def start_task(self, actor, kind, input_data, key):
        if kind != "learning":
            raise ValueError("NOT_FOUND")
        if not key or len(key) > 200:
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        cid = str(input_data.get("context_id") or input_data.get("target_context_id"))
        digest = _hash({"kind": kind, "input": input_data})
        with self.repo.connection(actor) as db:
            self.require(db, cid, edit=True)
            db.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("memory:" + actor.user_id + ":" + key,),
            )
            previous = db.execute(
                "select id,status,payload_hash from atp.memory_tasks where created_by=%s and idempotency_key=%s",
                (actor.user_id, key),
            ).fetchone()
            if previous:
                if previous["payload_hash"] != digest:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return _clean(previous)
            pending = db.execute(
                "select count(*) as n from atp.memory_tasks where kind='learning' and created_by=%s and status in ('queued','running')",
                (actor.user_id,),
            ).fetchone()["n"]
            if pending >= 10:
                raise ValueError("RATE_LIMIT")
            f = db.execute(
                "select * from atp.memory_feedback where id=%s and expires_at>now()",
                (str(input_data["feedback_id"]),),
            ).fetchone()
            if not f:
                raise ValueError("NOT_FOUND")
            snapshot = {"contexts": [], "entries": []}
            # A learning with source material from another context retains its source ACL.
            for source_cid in dict.fromkeys([cid, str(f["context_id"])]):
                self.require(db, source_cid)
                snapshot["contexts"].append({"id": source_cid})
            input_data = {**input_data, "feedback": _clean(f)}
            tid = str(uuid4())
            db.execute(
                "insert into atp.memory_tasks(id,org_id,created_by,context_id,kind,input,memory_snapshot,idempotency_key,payload_hash,queue,expires_at) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    tid,
                    actor.org_id,
                    actor.user_id,
                    cid,
                    kind,
                    Jsonb(input_data),
                    Jsonb(snapshot),
                    key,
                    digest,
                    self.settings.worker_queue,
                    self.deadline(),
                ),
            )
            self.link(db, snapshot, task_id=tid)
            return {"id": tid, "status": "queued"}

    def tasks(self, actor, task_id=None):
        with self.repo.connection(actor) as db:
            if task_id:
                row = db.execute(
                    "select * from atp.memory_tasks where id=%s and kind='learning'", (task_id,)
                ).fetchone()
                if not row:
                    raise ValueError("NOT_FOUND")
                return _clean(row)
            return _clean(
                db.execute(
                    "select id,kind,context_id,status,created_at,error_code from atp.memory_tasks where kind='learning' order by created_at desc limit 100"
                ).fetchall()
            )

    def export(self, actor, ids):
        contexts = []
        with self.repo.connection(actor) as db:
            for cid in ids:
                self.require(db, cid, owner=True)
                c = self.context(actor, cid, db)
                c["entries"] = _clean(
                    db.execute(
                        "select * from atp.memory_entries where context_id=%s order by created_at", (cid,)
                    ).fetchall()
                )
                versions = db.execute(
                    "select v.* from atp.memory_entry_versions v join atp.memory_entries e on e.id=v.entry_id where e.context_id=%s",
                    (cid,),
                ).fetchall()
                contexts.append(
                    {
                        k: c[k]
                        for k in (
                            "id",
                            "kind",
                            "name",
                            "description",
                            "external_id",
                            "entries",
                            "created_at",
                            "expires_at",
                        )
                    }
                    | {"versions": _clean(versions)}
                )
        return {
            "schema_version": 1,
            "format": "atp-memory",
            "exported_at": datetime.now(UTC).isoformat(),
            "contexts": contexts,
        }

    def import_package(self, actor, package, mapping, apply=False):
        if (
            package.get("format") != "atp-memory"
            or package.get("schema_version") != 1
            or not isinstance(package.get("contexts"), list)
            or len(package["contexts"]) > 20
        ):
            raise ValueError("INVALID_IMPORT")
        preview = []
        with self.repo.connection(actor) as db:
            for source in package["contexts"]:
                sid = str(UUID(source["id"]))
                target = mapping.get(sid)
                if not target:
                    raise ValueError("IMPORT_MAPPING_REQUIRED")
                self.require(db, target, owner=True)
                c = db.execute("select kind from atp.memory_contexts where id=%s", (target,)).fetchone()
                if c["kind"] != source["kind"]:
                    raise ValueError("IMPORT_KIND_MISMATCH")
                if len(source.get("entries", [])) > 10000:
                    raise ValueError("INVALID_IMPORT")
                for e in source.get("entries", []):
                    eid = str(UUID(e["id"]))
                    version = int(e["version"])
                    value = EntryInput.model_validate(
                        {
                            k: e[k]
                            for k in ("text", "kind", "source", "information_as_of", "status", "applies_to")
                        }
                    )
                    if version < 1 or e.get("deleted_at"):
                        raise ValueError("INVALID_IMPORT")
                    prior = db.execute(
                        "select entry_id from atp.memory_imports where context_id=%s and source_id=%s and source_version=%s",
                        (target, eid, version),
                    ).fetchone()
                    preview.append(
                        {
                            "source_id": eid,
                            "target_context_id": target,
                            "action": "already_imported" if prior else "import",
                            "source_version": version,
                        }
                    )
                    if apply and not prior:
                        db.execute(
                            "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                            ("import:" + target + eid,),
                        )
                        if db.execute(
                            "select 1 from atp.memory_imports where context_id=%s and source_id=%s and source_version=%s",
                            (target, eid, version),
                        ).fetchone():
                            continue
                        origin_expiry = datetime.fromisoformat(e["expires_at"])
                        if origin_expiry.tzinfo is None or origin_expiry <= datetime.now(UTC):
                            raise ValueError("IMPORT_EXPIRED")
                        saved = self.save_entry(actor, target, value, db=db, expires_at=origin_expiry)
                        # Preserve origin, timestamps and version history as migration provenance.
                        history = {
                            "origin_context": sid,
                            "origin_id": eid,
                            "origin_version": version,
                            "origin_created_at": e.get("created_at"),
                            "origin_expires_at": e.get("expires_at"),
                            "versions": [v for v in source.get("versions", []) if v.get("entry_id") == eid],
                        }
                        db.execute(
                            "insert into atp.memory_entry_versions(entry_id,version,context_id,data) values(%s,0,%s,%s)",
                            (saved["id"], target, Jsonb(history)),
                        )
                        db.execute(
                            "insert into atp.memory_imports(context_id,source_id,source_version,entry_id) values(%s,%s,%s,%s)",
                            (target, eid, version, saved["id"]),
                        )
            return {"applied": apply, "entries": preview}

    def invalidate(self, actor, context_id, entry_id=None):
        """Erase source and derived copies; authenticated checks and locks precede privileged purge."""
        with self.repo.connection(actor) as db:
            self.require(db, context_id, owner=entry_id is None, edit=entry_id is not None)
            if (
                entry_id
                and not db.execute(
                    "select 1 from atp.memory_entries where id=%s and context_id=%s", (entry_id, context_id)
                ).fetchone()
            ):
                raise ValueError("NOT_FOUND")
            # Authorisation and erase are serialized with snapshot publication using a context lock.
            with self.repo.connection(worker=True) as privileged:
                privileged.execute(
                    "select set_config('request.jwt.claims',%s,true)", (json.dumps({"sub": actor.user_id}),)
                )
                privileged.execute(
                    "select pg_advisory_xact_lock(hashtextextended(%s,0))", ("memory:" + context_id,)
                )
                self.require(privileged, context_id, owner=entry_id is None, edit=entry_id is not None)
                self.erase(privileged, context_id, entry_id)

    def erase(self, db, context_id, entry_id=None):
        db.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("memory:" + str(context_id),))
        meetings = db.execute(
            "select distinct r.meeting_id,r.org_id from atp.memory_links l join atp.runs r on r.id=l.company_run_id where l.context_id=%s",
            (context_id,),
        ).fetchall()
        for m in meetings:
            db.execute(
                "insert into atp.deletion_tombstones(meeting_id,org_id) values(%s,%s) on conflict do nothing",
                (m["meeting_id"], m["org_id"]),
            )
            db.execute(
                "insert into atp.deletion_jobs(meeting_id,org_id) values(%s,%s) on conflict do nothing",
                (m["meeting_id"], m["org_id"]),
            )
            db.execute("update atp.meetings set deleted_at=now() where id=%s", (m["meeting_id"],))
            db.execute(
                "update atp.jobs set status='cancelled',checkpoint='{}',lease_token=null where run_id in (select id from atp.runs where meeting_id=%s)",
                (m["meeting_id"],),
            )
        tasks = db.execute(
            "select distinct t.* from atp.memory_tasks t left join atp.memory_links l on l.task_id=t.id where t.context_id=%s or l.context_id=%s",
            (context_id, context_id),
        ).fetchall()
        for t in tasks:
            if t["pdf_path"]:
                db.execute(
                    "insert into atp.storage_cleanup(path,org_id,meeting_id,run_id) values(%s,%s,%s,%s) on conflict do nothing",
                    (t["pdf_path"], t["org_id"], t["context_id"], t["id"]),
                )
            db.execute("delete from atp.memory_tasks where id=%s", (t["id"],))
        if entry_id:
            db.execute("delete from atp.memory_entries where id=%s and context_id=%s", (entry_id, context_id))
        else:
            db.execute("delete from atp.memory_shares where context_id=%s", (context_id,))
            db.execute("delete from atp.memory_feedback where context_id=%s", (context_id,))
            db.execute("delete from atp.memory_entries where context_id=%s", (context_id,))
            # Links remain until existing meeting purges complete; their context must remain denied.
            db.execute(
                "update atp.memory_contexts set deleted_at=now(),name='Gelöscht',description='',external_id='' where id=%s",
                (context_id,),
            )
            db.execute(
                "delete from atp.memory_contexts c where c.id=%s and not exists(select 1 from atp.memory_links l where l.context_id=c.id) and not exists(select 1 from atp.memory_tasks t where t.context_id=c.id)",
                (context_id,),
            )

    def expire(self, db):
        contexts = db.execute(
            "select id from atp.memory_contexts where deleted_at is null and expires_at<=now()"
        ).fetchall()
        for c in contexts:
            self.erase(db, c["id"])
        entries = db.execute(
            "select id,context_id from atp.memory_entries where expires_at<=now()"
        ).fetchall()
        for e in entries:
            self.erase(db, e["context_id"], e["id"])
        tasks = db.execute("select * from atp.memory_tasks where expires_at<=now() for update").fetchall()
        for t in tasks:
            if t["pdf_path"]:
                db.execute(
                    "insert into atp.storage_cleanup(path,org_id,meeting_id,run_id) values(%s,%s,%s,%s) on conflict do nothing",
                    (t["pdf_path"], t["org_id"], t["context_id"], t["id"]),
                )
            db.execute("delete from atp.memory_tasks where id=%s", (t["id"],))
        db.execute("delete from atp.memory_feedback where expires_at<=now()")
        db.execute(
            "delete from atp.memory_contexts c where c.deleted_at is not null and not exists(select 1 from atp.memory_links l where l.context_id=c.id) and not exists(select 1 from atp.memory_tasks t where t.context_id=c.id)"
        )
