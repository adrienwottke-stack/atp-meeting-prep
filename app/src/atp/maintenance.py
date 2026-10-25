"""Explicit operational retention command; no scheduler is silently installed."""

import argparse
import json
from pathlib import Path

from .config import Settings
from .repository import Repository


def expire_records(repository, days, *, apply=False):
    if days is None or days < 1:
        raise ValueError("RETENTION_NOT_CONFIGURED")
    with repository._connection(worker=True) as db:
        rows = db.execute(
            "select id,org_id from atp.meetings where deleted_at is null and created_at < now()-(%s*interval '1 day') for update skip locked",
            (days,),
        ).fetchall()
        drafts = db.execute(
            "select count(*) as n from atp.drafts where created_at<now()-(%s*interval '1 day')", (days,)
        ).fetchone()["n"]
        if apply:
            if db.execute("select to_regclass('atp.memory_contexts') is not null as present").fetchone()[
                "present"
            ]:
                from .memory_store import PostgresMemoryStore

                PostgresMemoryStore(repository).expire(db)
            for row in rows:
                db.execute(
                    "insert into atp.deletion_tombstones(meeting_id,org_id) values(%s,%s) on conflict do nothing",
                    (row["id"], row["org_id"]),
                )
                db.execute(
                    "insert into atp.deletion_jobs(meeting_id,org_id) values(%s,%s) on conflict do nothing",
                    (row["id"], row["org_id"]),
                )
                db.execute(
                    "update atp.jobs set status='cancelled',lease_token=null,lease_until=null where run_id in (select id from atp.runs where meeting_id=%s)",
                    (row["id"],),
                )
                db.execute(
                    "update atp.meetings set deleted_at=now(),updated_at=now() where id=%s", (row["id"],)
                )
            db.execute("delete from atp.drafts where created_at<now()-(%s*interval '1 day')", (days,))
        return {"meetings": len(rows), "drafts": drafts, "applied": apply, "retention_days": days}


def main():
    parser = argparse.ArgumentParser(description="ATP Aufbewahrung und Wiederherstellungs-Löschliste")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Bestätigte Aufbewahrungsfrist tatsächlich anwenden; sonst Vorschau",
    )
    parser.add_argument("--export-tombstones", type=Path)
    args = parser.parse_args()
    settings = Settings()
    repo = Repository(settings)
    if args.export_tombstones:
        with repo._connection(worker=True) as db:
            rows = db.execute("select meeting_id,org_id,requested_at from atp.deletion_tombstones").fetchall()
        args.export_tombstones.parent.mkdir(parents=True, exist_ok=True)
        args.export_tombstones.write_text(json.dumps(rows, default=str, indent=2), encoding="utf-8")
        print(f"{len(rows)} datensparsame Löschsperren exportiert. Zugriff auf Datei beschränken.")
        return
    if not settings.fixture_mode and not settings.real_data_approved:
        raise SystemExit("Reale Aufbewahrungsregel benötigt dokumentierte Betriebsfreigabe.")
    try:
        counts = expire_records(repo, settings.retention_days, apply=args.apply)
    except ValueError:
        raise SystemExit("RETENTION_DAYS muss vor Anwendung fachlich festgelegt sein.") from None
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
