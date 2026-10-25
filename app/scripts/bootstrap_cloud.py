"""Explicit ATP cloud setup; credentials and CLI output stay in ignored output/setup."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "output" / "setup"
MANIFEST = SETUP / "cloud-project.json"
ADMIN = SETUP / "cloud-admin.json"
ORG = "dozdfhvhttzctdekhklg"
CLI = "supabase@2.121.0"
CA_URL = "https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt"
CA_SHA256 = "807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def cli(*args, timeout=180):
    # Invoke Node directly: Windows .cmd wrappers can interpret '&' in DB URLs.
    npx = shutil.which("npx.cmd") or shutil.which("npx")
    script = Path(npx).parent / "node_modules/npm/bin/npx-cli.js"
    if not script.is_file():
        raise RuntimeError("NATIVE_NPX_ENTRYPOINT_NOT_FOUND")
    command = [shutil.which("node.exe") or shutil.which("node"), str(script), "--yes", CLI, *args]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        write_json(
            SETUP / "cloud-cli-error.json",
            {
                "command_group": list(args[:2]),
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        raise RuntimeError("CLI_FAILED: see ignored output/setup/cloud-cli-error.json")
    return result.stdout


def project():
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ref = value.get("ref")
    if (
        value.get("organization_id") != ORG
        or value.get("name") != "ATP"
        or not isinstance(ref, str)
        or not re.fullmatch(r"[a-z]{20}", ref)
        or value.get("id") != ref
        or value.get("region") != "eu-central-1"
        or value.get("supabase_url") != f"https://{ref}.supabase.co"
    ):
        raise RuntimeError("WRONG_PROJECT")
    return value


def file_settings(payload):
    """Validate exactly these bytes, with every OS/environment default overridden."""
    from atp.config import Settings

    values = {
        name: field.get_default(call_default_factory=True) for name, field in Settings.model_fields.items()
    }
    values.update(
        {
            key.lower(): val
            for key, val in dotenv_values(stream=io.StringIO(payload.decode("utf-8"))).items()
            if key.lower() in values and val is not None
        }
    )
    return Settings(_env_file=None, **values)


def cloud_settings():
    value = project()
    payload = (ROOT / ".env.cloud").read_bytes()
    settings = file_settings(payload)
    if (
        settings.supabase_url != value["supabase_url"]
        or settings.supabase_jwt_issuer != value["supabase_url"] + "/auth/v1"
        or settings.effective_storage_backend != "supabase"
    ):
        raise RuntimeError("CLOUD_CONFIGURATION_MISMATCH")
    for dsn_value, name in [
        (settings.database_url, "database_url_sha256"),
        (settings.worker_database_url, "worker_database_url_sha256"),
    ]:
        if hashlib.sha256(dsn_value.encode()).hexdigest() != value[name]:
            raise RuntimeError("CLOUD_DATABASE_IDENTITY_MISMATCH")
    return value, settings, payload


def create():
    if MANIFEST.exists():
        return project()
    organizations = json.loads(cli("orgs", "list", "-o", "json"))
    if not any(o["id"] == ORG for o in organizations):
        raise RuntimeError("SELECTED_ORGANIZATION_NOT_ACCESSIBLE")
    existing = json.loads(cli("projects", "list", "-o", "json"))
    if any(p["organization_id"] == ORG and p["name"] == "ATP" for p in existing):
        raise RuntimeError("ATP_ALREADY_EXISTS_WITHOUT_LOCAL_MANIFEST")
    credentials = {
        "organization_id": ORG,
        "db_password": secrets.token_urlsafe(40),
        "web_password": secrets.token_urlsafe(40),
        "worker_password": secrets.token_urlsafe(40),
    }
    write_json(ADMIN, credentials)
    value = json.loads(
        cli(
            "projects",
            "create",
            "ATP",
            "--org-id",
            ORG,
            "--region",
            "eu-central-1",
            "--db-password",
            credentials["db_password"],
            "-o",
            "json",
            "--yes",
            timeout=240,
        )
    )
    if value.get("organization_id") != ORG or value.get("name") != "ATP":
        raise RuntimeError("CREATED_PROJECT_IDENTITY_MISMATCH")
    value["ref"] = value.get("ref", value["id"])
    value["supabase_url"] = f"https://{value['ref']}.supabase.co"
    write_json(MANIFEST, value)
    credentials["project_ref"] = value["ref"]
    write_json(ADMIN, credentials)
    return value


def dsn(role, password, ref):
    certificate = SETUP / "supabase-root.crt"
    query = urlencode({"sslmode": "verify-full", "sslrootcert": str(certificate.resolve())})
    return f"postgresql://{role}:{quote(password, safe='')}@db.{ref}.supabase.co:5432/postgres?{query}"


def scram(password):
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 4096)
    client = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored = hashlib.sha256(client).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(value):
        return base64.b64encode(value).decode()

    return f"SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}"


def configure():
    value = project()
    with httpx.Client(timeout=20, trust_env=False) as client:
        certificate = client.get(CA_URL)
        certificate.raise_for_status()
    if hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.text)).hexdigest() != CA_SHA256:
        raise RuntimeError("REVIEW_SUPABASE_CA_CHANGE")
    (SETUP / "supabase-root.crt").write_text(certificate.text, encoding="ascii")
    admin = json.loads(ADMIN.read_text(encoding="utf-8"))
    ref = value["ref"]
    if admin["project_ref"] != ref:
        raise RuntimeError("ADMIN_PROJECT_MISMATCH")
    admin_url = dsn("postgres", admin["db_password"], ref)
    # All migrations keep their canonical on-disk version numbers.
    result = cli("db", "push", "--db-url", admin_url, "--skip-vault", "--yes", timeout=180)
    (SETUP / "cloud-migrations.log").write_text(result, encoding="utf-8")
    with psycopg.connect(admin_url, connect_timeout=15) as db:
        if db.execute("select count(*) from atp.meetings").fetchone()[0]:
            raise RuntimeError("UNEXPECTED_EXISTING_MEETINGS_DURING_BOOTSTRAP")
        for role, password in [("atp_web", admin["web_password"]), ("atp_worker", admin["worker_password"])]:
            db.execute(
                sql.SQL("alter role {} login password {}").format(
                    sql.Identifier(role), sql.Literal(scram(password))
                )
            )
        value["tls_verified"] = bool(
            db.execute("select ssl from pg_stat_ssl where pid=pg_backend_pid()").fetchone()[0]
        )
    keys = json.loads(cli("projects", "api-keys", "--project-ref", ref, "--reveal", "-o", "json"))
    write_json(SETUP / "cloud-api-keys.json", keys)
    modern_public = next(
        (k["api_key"] for k in keys if k.get("type") == "publishable" and not k.get("disabled")), ""
    )
    modern_secret = next(
        (k["api_key"] for k in keys if k.get("type") == "secret" and not k.get("disabled")), ""
    )
    legacy_public = next((k["api_key"] for k in keys if k.get("name") == "anon"), "")
    legacy_secret = next((k["api_key"] for k in keys if k.get("name") == "service_role"), "")
    if not (modern_public or legacy_public) or not (modern_secret or legacy_secret):
        raise RuntimeError("PROJECT_API_KEYS_MISSING")
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(value["supabase_url"] + "/auth/v1/.well-known/jwks.json")
        response.raise_for_status()
        algorithms = {k.get("alg") for k in response.json().get("keys", [])}
    if algorithms != {"ES256"}:
        raise RuntimeError("REVIEW_ACTUAL_CLOUD_SIGNING_ALGORITHM")
    environment = dict(dotenv_values(ROOT / ".env"))
    environment.update(
        {
            "APP_MODE": "local",
            "APP_BASE_URL": "http://127.0.0.1:8000",
            "STORAGE_BACKEND": "supabase",
            "SUPABASE_URL": value["supabase_url"],
            "SUPABASE_ANON_KEY": modern_public or legacy_public,
            "SUPABASE_SECRET_KEY": modern_secret,
            "SUPABASE_SERVICE_ROLE_KEY": "" if modern_secret else legacy_secret,
            "SUPABASE_JWT_SECRET": "",
            "SUPABASE_JWT_ALGORITHM": "ES256",
            "SUPABASE_JWT_ISSUER": value["supabase_url"] + "/auth/v1",
            "AUTH_VERIFY_SESSION": "true",
            "STORAGE_BUCKET": "briefings",
            "DATABASE_URL": dsn("atp_web", admin["web_password"], ref),
            "WORKER_DATABASE_URL": dsn("atp_worker", admin["worker_password"], ref),
        }
    )
    for name, key in [
        ("database_url_sha256", "DATABASE_URL"),
        ("worker_database_url_sha256", "WORKER_DATABASE_URL"),
    ]:
        value[name] = hashlib.sha256(environment[key].encode()).hexdigest()
    write_json(MANIFEST, value)
    lines = [
        key + "=" + json.dumps(str(val), ensure_ascii=False)
        for key, val in environment.items()
        if val is not None
    ]
    (ROOT / ".env.cloud").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return value


def auth_config():
    value = project()
    target = SETUP / "cloud-config" / "supabase"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.toml").write_text(
        'project_id = "atp-cloud"\n'
        '[api]\nenabled = true\nschemas = ["public", "graphql_public"]\n'
        '[auth]\nenabled = true\nsite_url = "http://127.0.0.1:8000"\n'
        'additional_redirect_urls = ["http://127.0.0.1:8000"]\n'
        "jwt_expiry = 900\nenable_signup = false\nenable_anonymous_sign_ins = false\n"
        "minimum_password_length = 12\n"
        "[auth.email]\nenable_signup = true\nenable_confirmations = true\n",
        encoding="utf-8",
    )
    diff = cli("config", "diff", "--project-ref", value["ref"], "--workdir", str(target.parent))
    (SETUP / "cloud-auth-config.diff").write_text(diff, encoding="utf-8")
    result = cli("config", "push", "--project-ref", value["ref"], "--workdir", str(target.parent), "--yes")
    (SETUP / "cloud-auth-config.log").write_text(result, encoding="utf-8")


def activate():
    _, s, payload = cloud_settings()
    for dsn_value, role in [(s.database_url, "atp_web"), (s.worker_database_url, "atp_worker")]:
        with psycopg.connect(dsn_value, connect_timeout=15) as db:
            if db.execute("select current_user").fetchone()[0] != role:
                raise RuntimeError("RUNTIME_DATABASE_ROLE_MISMATCH")
            if not db.execute("select ssl from pg_stat_ssl where pid=pg_backend_pid()").fetchone()[0]:
                raise RuntimeError("TLS_REQUIRED")
    if s.setup_issues():
        raise RuntimeError("CLOUD_SETUP_INCOMPLETE")
    backup = ROOT / ".env.local"
    current = dotenv_values(ROOT / ".env")
    if current.get("SUPABASE_URL") == "http://127.0.0.1:55431" and not backup.exists():
        shutil.copyfile(ROOT / ".env", backup)
    temporary = ROOT / ".env.activating"
    temporary.write_bytes(payload)
    os.replace(temporary, ROOT / ".env")


def admin_user(email):
    value, settings, _ = cloud_settings()
    if not email or "@" not in email or any(c.isspace() for c in email):
        raise RuntimeError("ADMIN_EMAIL_REQUIRED")
    path = SETUP / "cloud-admin-login.json"
    if path.exists():
        account = json.loads(path.read_text(encoding="utf-8"))
        if account.get("project_ref") != value["ref"] or account.get("email") != email:
            raise RuntimeError("EXISTING_ADMIN_ACCOUNT_MISMATCH")
    else:
        account = {
            "project_ref": value["ref"],
            "email": email,
            "password": secrets.token_urlsafe(36),
            "id": str(uuid4()),
            "org_id": str(uuid4()),
        }
        write_json(path, account)
    headers = {"apikey": settings.storage_api_key}
    if not settings.storage_api_key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + settings.storage_api_key
    with httpx.Client(
        base_url=settings.supabase_url + "/auth/v1/", headers=headers, timeout=20, trust_env=False
    ) as client:
        response = client.get("admin/users/" + account["id"])
        if response.status_code == 404:
            response = client.post(
                "admin/users",
                json={
                    "id": account["id"],
                    "email": account["email"],
                    "password": account["password"],
                    "email_confirm": True,
                },
            )
        response.raise_for_status()
        if response.json()["id"] != account["id"]:
            raise RuntimeError("ADMIN_USER_ID_MISMATCH")
    from psycopg.types.json import Jsonb

    from atp.profiles import load_profile, profile_hash

    profile = load_profile(settings.atp_profile_version)
    with psycopg.connect(settings.worker_database_url, connect_timeout=15) as db:
        db.execute(
            "insert into atp.memberships(user_id,org_id,role) values(%s,%s,'admin') "
            "on conflict(user_id,org_id) do nothing",
            (account["id"], account["org_id"]),
        )
        existing = db.execute(
            "select content_hash from atp.profile_versions where org_id=%s and version=%s",
            (account["org_id"], profile["profile_id"]),
        ).fetchone()
        if existing and existing[0] != profile_hash(profile):
            raise RuntimeError("PROFILE_HASH_CONFLICT")
        if not existing:
            db.execute(
                "insert into atp.profile_versions(org_id,version,content_hash,content,active,activated_by) "
                "values(%s,%s,%s,%s,true,%s)",
                (
                    account["org_id"],
                    profile["profile_id"],
                    profile_hash(profile),
                    Jsonb(profile),
                    account["id"],
                ),
            )
            db.execute(
                "insert into atp.profile_activations(org_id,version,content_hash,activated_by,reason) "
                "values(%s,%s,%s,%s,%s)",
                (
                    account["org_id"],
                    profile["profile_id"],
                    profile_hash(profile),
                    account["id"],
                    "Technische Cloud-Ersteinrichtung; fachliche ATP-Freigabe und Realdatenbetrieb bleiben offen.",
                ),
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "configure", "auth-config", "activate", "admin-user"])
    parser.add_argument("--email")
    args = parser.parse_args()
    try:
        result = {
            "create": create,
            "configure": configure,
            "auth-config": auth_config,
            "activate": activate,
            "admin-user": lambda: admin_user(args.email),
        }[args.action]()
        print(
            json.dumps(
                {
                    "action": args.action,
                    "ok": True,
                    "project_ref": result["ref"] if isinstance(result, dict) else project()["ref"],
                }
            )
        )
    except Exception as exc:
        # Library exceptions can contain connection strings. Persist privately, never echo.
        write_json(SETUP / "cloud-bootstrap-error.json", {"type": type(exc).__name__, "message": str(exc)})
        print(
            json.dumps(
                {
                    "action": args.action,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "detail_file": "output/setup/cloud-bootstrap-error.json",
                }
            )
        )
        raise SystemExit(1) from None
