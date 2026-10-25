"""Explicit additive migration for the already selected cloud project. Never prints credentials."""

import argparse
import json
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]


def main():
    helpers = runpy.run_path(str(ROOT / "scripts/bootstrap_cloud.py"), run_name="cloud_helpers")
    admin = json.loads((ROOT / "output/setup/cloud-admin.json").read_text(encoding="utf-8"))
    if admin["project_ref"] != "edtudbpucezulbfrosum":
        raise RuntimeError("WRONG_PROJECT")
    dsn = helpers["dsn"]("postgres", admin["db_password"], admin["project_ref"])
    parser = argparse.ArgumentParser()
    parser.add_argument("--migration", default="*_agent_memory.sql")
    args = parser.parse_args()
    migration = next((ROOT / "supabase/migrations").glob(args.migration))
    version = migration.name.split("_")[0]
    with psycopg.connect(dsn, connect_timeout=15, row_factory=dict_row) as db:
        if db.execute(
            "select 1 from supabase_migrations.schema_migrations where version=%s", (version,)
        ).fetchone():
            print(json.dumps({"migration": version, "status": "already_applied"}))
            return
        tables = db.execute(
            "select tablename from pg_tables where schemaname='atp' order by tablename"
        ).fetchall()
        backup = {"created_at": datetime.now(UTC).isoformat(), "tables": {}}
        from psycopg import sql

        for t in tables:
            backup["tables"][t["tablename"]] = db.execute(
                sql.SQL("select * from atp.{}").format(sql.Identifier(t["tablename"]))
            ).fetchall()
        folder = ROOT / "output/setup/hosted-backup-20261015"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("memory-before-" + version + ".json")
        target.write_text(json.dumps(backup, default=str), encoding="utf-8")
        db.execute(migration.read_text(encoding="utf-8"))
        db.execute(
            "insert into supabase_migrations.schema_migrations(version,name,statements) values(%s,%s,%s)",
            (version, "agent_memory", [migration.read_text(encoding="utf-8")]),
        )
        checks = db.execute(
            "select tablename,rowsecurity from pg_tables where schemaname='atp' and tablename like 'memory_%'"
        ).fetchall()
        if len(checks) != 8 or not all(x["rowsecurity"] for x in checks):
            raise RuntimeError("RLS_CHECK_FAILED")
        print(
            json.dumps(
                {"migration": version, "status": "applied", "new_tables": len(checks), "all_rls": True}
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Keep provider/driver details in the already protected setup folder only.
        (ROOT / "output/setup/memory-migration-error.txt").write_text(str(exc), encoding="utf-8")
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        sys.exit(1)
