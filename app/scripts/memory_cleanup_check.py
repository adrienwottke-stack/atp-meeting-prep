"""Targeted hosted check: an unlinked deleted context leaves no owner/share metadata."""

import json

from atp.config import Settings
from atp.memory_models import ContextInput, EntryInput
from atp.models import Actor
from atp.repository import Repository


def main():
    settings = Settings()
    user_id = settings.memory_user_ids.split(",")[0]
    repo = Repository(settings)
    with repo._connection(worker=True) as db:
        membership = db.execute(
            "select org_id from atp.memberships where user_id=%s and active", (user_id,)
        ).fetchone()
    actor = Actor(user_id=user_id, org_id=str(membership["org_id"]))
    store = repo.memory()
    context = store.save_context(
        actor, ContextInput(name="Löschmetadaten-Abnahme (synthetisch)", kind="project")
    )
    cid = context["id"]
    try:
        store.save_entry(actor, cid, EntryInput(text="Synthetischer Löschtest.", status="confirmed"))
        store.share(actor, cid, user_id, "read")
        store.invalidate(actor, cid)
        with repo._connection(worker=True) as db:
            counts = [
                db.execute(
                    "select count(*) as n from atp." + table + " where " + column + "=%s", (cid,)
                ).fetchone()["n"]
                for table, column in [
                    ("memory_contexts", "id"),
                    ("memory_entries", "context_id"),
                    ("memory_shares", "context_id"),
                    ("memory_entry_versions", "context_id"),
                ]
            ]
        assert counts == [0, 0, 0, 0]
        print(
            json.dumps(
                {
                    "status": "passed",
                    "check": "unlinked_context_and_owner_share_version_metadata_purged",
                    "remaining_rows": counts,
                }
            )
        )
    finally:
        with repo._connection(worker=True) as db:
            db.execute("delete from atp.memory_contexts where id=%s", (cid,))


if __name__ == "__main__":
    main()
