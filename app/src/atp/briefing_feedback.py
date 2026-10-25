"""Feedback at the briefing, with explicit reuse as private company knowledge."""

from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

from .memory_models import BriefingFeedbackInput, EntryInput
from .repository import _clean


def company_memory_key(company):
    """Match an exact website host, never a company name or a parent domain."""
    try:
        host = (urlsplit(str(company.get("website", ""))).hostname or "").lower().rstrip(".")
        host = host.removeprefix("www.").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""
    return "atp:company:v1:" + sha256(host.encode()).hexdigest() if host else ""


class BriefingFeedbackStore:
    def __init__(self, repository):
        self.repo = repository
        self.memory = repository.memory()

    def _briefing(self, db, briefing_id, edit=False):
        row = db.execute(
            "select b.id,b.result->'input' as input,"
            "atp_private.meeting_allowed(b.meeting_id,true) as editable "
            "from atp.briefings b where b.id=%s "
            "and (not %s or atp_private.meeting_allowed(b.meeting_id,true))",
            (briefing_id, edit),
        ).fetchone()
        if not row:
            raise ValueError("NOT_FOUND")
        return row

    @staticmethod
    def _items(db, briefing_id):
        return db.execute(
            "select f.*,e.id as entry_id,e.status='confirmed' as remembered,"
            "(c.owner_id=auth.uid() and c.external_id like 'atp:company:v1:%%') as automatic_reuse "
            "from atp.memory_feedback f join atp.memory_contexts c on c.id=f.context_id "
            "left join atp.memory_entries e "
            "on e.id=f.id and e.context_id=f.context_id "
            "where f.briefing_id=%s and f.expires_at>now() "
            "order by f.created_at desc,f.id",
            (briefing_id,),
        ).fetchall()

    def list(self, actor, briefing_id):
        with self.repo.connection(actor) as db:
            briefing = self._briefing(db, briefing_id)
            return _clean({"items": self._items(db, briefing_id), "editable": briefing["editable"]})

    def _company_context(self, db, actor, company):
        key = company_memory_key(company)
        if not key:
            raise ValueError("INVALID_INPUT")
        db.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"company-feedback:{actor.org_id}:{actor.user_id}:{key}",),
        )
        row = db.execute(
            "select id from atp.memory_contexts where owner_id=%s and org_id=%s "
            "and kind='company' and external_id=%s and deleted_at is null and expires_at>now() "
            "order by created_at,id limit 1",
            (actor.user_id, actor.org_id, key),
        ).fetchone()
        if row:
            return str(row["id"])
        cid = str(uuid4())
        db.execute(
            "insert into atp.memory_contexts"
            "(id,org_id,owner_id,kind,name,description,external_id,expires_at) "
            "values(%s,%s,%s,'company',%s,%s,%s,%s)",
            (
                cid,
                actor.org_id,
                actor.user_id,
                company["name"][:200],
                "Eigene Rückmeldungen aus Gesprächsvorbereitungen für " + company["website"],
                key,
                self.memory.deadline(),
            ),
        )
        return cid

    def _remember(self, db, actor, feedback):
        cid, fid = str(feedback["context_id"]), str(feedback["id"])
        self.memory.require(db, cid, edit=True)
        old = db.execute(
            "select * from atp.memory_entries where id=%s and context_id=%s", (fid, cid)
        ).fetchone()
        if old and old["status"] == "confirmed":
            return _clean(old)
        labels = {
            "correction": "Korrektur",
            "outcome": "Gesprächsergebnis",
            "helpful": "Hilfreicher Ansatz",
            "unhelpful": "Verbesserung für die Vorbereitung",
        }
        text = labels[feedback["kind"]] + ": " + feedback["text"].strip()
        if feedback["reference"]:
            text += "\nBezug im ursprünglichen Briefing: " + feedback["reference"]
        entry = EntryInput(
            text=text,
            kind="learning",
            status="confirmed",
            source="Eigener Hinweis aus Briefing " + str(feedback["briefing_id"]),
            information_as_of=feedback["created_at"].date().isoformat(),
            expected_version=old["version"] if old else None,
        )
        return self.memory.save_entry(
            actor,
            cid,
            entry,
            entry_id=fid if old else None,
            new_id=fid,
            db=db,
            expires_at=feedback["expires_at"],
        )

    def save(self, actor, briefing_id, value: BriefingFeedbackInput):
        with self.repo.connection(actor) as db:
            briefing = self._briefing(db, briefing_id, edit=True)
            fid = str(value.submission_id)
            db.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("feedback:" + fid,))
            previous = db.execute("select * from atp.memory_feedback where id=%s", (fid,)).fetchone()
            if previous:
                if (
                    str(previous["briefing_id"]) != briefing_id
                    or str(previous["created_by"]) != actor.user_id
                    or any(previous[k] != getattr(value, k) for k in ("kind", "text", "reference"))
                ):
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                row = previous
            else:
                cid = self._company_context(db, actor, briefing["input"]["company"])
                row = db.execute(
                    "insert into atp.memory_feedback"
                    "(id,org_id,context_id,created_by,kind,text,reference,briefing_id,expires_at) "
                    "values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                    (
                        fid,
                        actor.org_id,
                        cid,
                        actor.user_id,
                        value.kind,
                        value.text,
                        value.reference,
                        briefing_id,
                        self.memory.deadline(),
                    ),
                ).fetchone()
            if value.remember:
                self._remember(db, actor, row)
            # No memory_link is added to a historical run: its snapshot and access remain intact.
            return _clean(next(x for x in self._items(db, briefing_id) if str(x["id"]) == fid))

    def remember(self, actor, briefing_id, feedback_id):
        with self.repo.connection(actor) as db:
            briefing = self._briefing(db, briefing_id, edit=True)
            db.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("feedback:" + feedback_id,))
            row = db.execute(
                "select * from atp.memory_feedback where id=%s and briefing_id=%s and expires_at>now()",
                (feedback_id, briefing_id),
            ).fetchone()
            if not row:
                raise ValueError("NOT_FOUND")
            # Legacy/shared context feedback keeps its existing explicit learning flow.
            if not db.execute(
                "select 1 from atp.memory_contexts where id=%s and owner_id=%s and external_id=%s",
                (row["context_id"], actor.user_id, company_memory_key(briefing["input"]["company"])),
            ).fetchone():
                raise ValueError("NOT_FOUND")
            self._remember(db, actor, row)
            return _clean(next(x for x in self._items(db, briefing_id) if str(x["id"]) == feedback_id))
