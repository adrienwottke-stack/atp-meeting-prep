"""Export a pinned cloud configuration for Hetzner, without modifying local .env."""

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import dotenv_values

from atp.config import Settings

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "output/setup"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    manifest = json.loads((SETUP / "cloud-project.json").read_text(encoding="utf-8-sig"))
    account = json.loads((SETUP / "cloud-admin-login.json").read_text(encoding="utf-8-sig"))
    if manifest["ref"] != args.project_ref or account["project_ref"] != args.project_ref:
        raise SystemExit("Project mismatch")
    environment = {k: v for k, v in dotenv_values(ROOT / ".env.cloud").items() if v is not None}
    if environment.get("SUPABASE_URL") != f"https://{args.project_ref}.supabase.co":
        raise SystemExit("Cloud environment mismatch")
    storage = dotenv_values(SETUP / "cloud-storage.env")
    key = storage.get("SUPABASE_SECRET_KEY") or storage.get("SUPABASE_SERVICE_ROLE_KEY")
    if key:
        environment["SUPABASE_SECRET_KEY"] = key if key.startswith("sb_secret_") else ""
        environment["SUPABASE_SERVICE_ROLE_KEY"] = "" if key.startswith("sb_secret_") else key
    environment.update(
        {
            "APP_MODE": "hosted",
            "APP_BASE_URL": "https://" + args.host,
            "STORAGE_BACKEND": "supabase",
            "FIXTURE_MODE": "false",
            "REAL_DATA_APPROVED": "true",
            "RETENTION_DAYS": "90",
            "INTEGRATIONS_ENABLED": "false",
            "OUTPUT_DIR": "/app/output",
        }
    )
    for name in ("DATABASE_URL", "WORKER_DATABASE_URL"):
        parts = urlsplit(environment[name])
        if parts.hostname != f"db.{args.project_ref}.supabase.co":
            raise SystemExit("Database project mismatch")
        query = dict(parse_qsl(parts.query))
        query.update(sslmode="verify-full", sslrootcert="/app/supabase-ca.crt")
        environment[name] = urlunsplit(parts._replace(query=urlencode(query)))
    settings = Settings(_env_file=None, **{k.lower(): v for k, v in environment.items()})
    if settings.setup_issues():
        raise SystemExit("Hosted configuration incomplete")
    (SETUP / "hosted-runtime.env").write_text(
        "\n".join(k + "=" + json.dumps(v) for k, v in environment.items()) + "\n", encoding="utf-8"
    )
    (SETUP / "hosted-compose.env").write_text(
        f"ATP_RELEASE={args.release}\nATP_HOST={args.host}\n", encoding="utf-8"
    )
    (SETUP / "hosted-access.txt").write_text(
        f"ATP: https://{args.host}\nE-Mail: {account['email']}\nPasswort: {account['password']}\n"
        f"Projekt: {args.project_ref}\nKeine E-Mail versendet. Datei vertraulich aufbewahren.\n",
        encoding="utf-8",
    )
    print("Hosted configuration validated and exported; access file saved without printing credentials.")


if __name__ == "__main__":
    main()
