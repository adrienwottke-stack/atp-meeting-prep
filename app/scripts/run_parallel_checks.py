"""Run isolated regressions without changing the current application configuration."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

from atp.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-config", type=Path, required=True)
    options, pytest_args = parser.parse_known_args()
    settings = Settings(
        _env_file=None,
        **{k.lower(): v for k, v in dotenv_values(options.local_config).items() if v is not None},
    )
    if (
        not settings.fixture_mode
        or settings.app_mode != "local"
        or settings.supabase_url != "http://127.0.0.1:55431"
    ):
        raise SystemExit("Only the isolated ATP local fixture backend is permitted.")
    for dsn in (settings.database_url, settings.worker_database_url):
        connection = conninfo_to_dict(dsn)
        if connection.get("host") != "127.0.0.1" or connection.get("port") != "55432":
            raise SystemExit("Local database identity mismatch.")
    root = Path(__file__).resolve().parents[1]
    settings.output_dir = root / "output"
    settings.worker_queue = "clarification-" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    settings.memory_enabled = False
    source = options.local_config.resolve().parent / "output" / "setup"
    target = settings.output_dir / "setup"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("local-users.json", "supabase-status.json"):
        if (source / name).resolve() != (target / name).resolve():
            shutil.copy2(source / name, target / name)
    environment = dict(os.environ)
    for key in type(settings).model_fields:
        value = getattr(settings, key)
        environment[key.upper()] = (
            "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
        )
    return subprocess.run(
        [sys.executable, "-m", "pytest", *pytest_args], cwd=root, env=environment, check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
