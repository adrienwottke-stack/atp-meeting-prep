"""Run tests against the isolated local fixture backend while keeping cloud active."""

import os
import runpy
import subprocess
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]


def main():
    settings = runpy.run_path(str(ROOT / "scripts/bootstrap_cloud.py"), run_name="cloud_helpers")[
        "file_settings"
    ]((ROOT / ".env.local").read_bytes())
    if settings.supabase_url != "http://127.0.0.1:55431" or not settings.fixture_mode:
        raise SystemExit("Isolated local fixture configuration required.")
    for dsn in (settings.database_url, settings.worker_database_url):
        connection = conninfo_to_dict(dsn)
        if connection.get("host") != "127.0.0.1" or connection.get("port") != "55432":
            raise SystemExit("Local database identity mismatch.")
    environment = dict(os.environ)
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        environment[name.upper()] = (
            "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
        )
    return subprocess.run(
        [sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=ROOT, env=environment, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
