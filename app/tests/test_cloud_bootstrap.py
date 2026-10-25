"""Offline guards for privileged cloud setup; no credentials or network required."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/bootstrap_cloud.py"
    spec = importlib.util.spec_from_file_location("cloud_bootstrap_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "SETUP", tmp_path / "output/setup")
    monkeypatch.setattr(module, "MANIFEST", tmp_path / "cloud-project.json")
    return module


def manifest(bootstrap):
    ref = "a" * 20
    return {
        "ref": ref,
        "id": ref,
        "name": "ATP",
        "organization_id": bootstrap.ORG,
        "region": "eu-central-1",
        "supabase_url": f"https://{ref}.supabase.co",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("ref", "bad-ref"),
        ("id", "b" * 20),
        ("name", "Other app"),
        ("organization_id", "other-organization"),
        ("region", "us-east-1"),
        ("supabase_url", "https://another.example"),
    ],
)
def test_project_identity_is_pinned_before_mutation(bootstrap, field, value):
    data = manifest(bootstrap)
    data[field] = value
    bootstrap.MANIFEST.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="WRONG_PROJECT"):
        bootstrap.project()


def test_file_validation_ignores_process_environment(bootstrap, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://unrelated.example")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-process-secret")
    monkeypatch.setenv("FIXTURE_MODE", "true")
    settings = bootstrap.file_settings(b'SUPABASE_URL="https://configured.example"\n')
    assert settings.supabase_url == "https://configured.example"
    assert settings.openai_api_key == ""
    assert settings.fixture_mode is False


def test_cli_uses_native_node_without_batch_shell_parsing(bootstrap, tmp_path, monkeypatch):
    npx = tmp_path / "npx.cmd"
    script = tmp_path / "node_modules/npm/bin/npx-cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("// test")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: str(npx) if name == "npx.cmd" else "node.exe")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    url = "postgresql://synthetic@db.example/postgres?sslmode=verify-full&sslrootcert=C%3A%2Fca.crt"
    assert bootstrap.cli("db", "push", "--db-url", url) == "{}"
    command, kwargs = calls[0]
    assert command[:2] == ["node.exe", str(script)]
    assert command[-1] == url
    assert not kwargs.get("shell", False)


def test_activation_writes_exact_validated_snapshot(bootstrap, tmp_path, monkeypatch):
    data = manifest(bootstrap)
    ref = data["ref"]
    web = f"postgresql://atp_web:synthetic@db.{ref}.supabase.co/postgres?sslmode=verify-full"
    worker = web.replace("atp_web:", "atp_worker:")
    data["database_url_sha256"] = hashlib.sha256(web.encode()).hexdigest()
    data["worker_database_url_sha256"] = hashlib.sha256(worker.encode()).hexdigest()
    bootstrap.MANIFEST.write_text(json.dumps(data))
    values = {
        "FIXTURE_MODE": "true",
        "STORAGE_BACKEND": "supabase",
        "SUPABASE_URL": data["supabase_url"],
        "SUPABASE_ANON_KEY": "synthetic-public",
        "SUPABASE_SECRET_KEY": "sb_secret_synthetic",
        "DATABASE_URL": web,
        "WORKER_DATABASE_URL": worker,
    }
    payload = "".join(key + "=" + json.dumps(value) + "\n" for key, value in values.items()).encode()
    candidate = tmp_path / ".env.cloud"
    candidate.write_bytes(payload)
    old = b'SUPABASE_URL="http://127.0.0.1:55431"\n'
    (tmp_path / ".env").write_bytes(old)

    class Connection:
        def __init__(self, dsn):
            self.role = "atp_worker" if "atp_worker:" in dsn else "atp_web"

        def __enter__(self):
            candidate.write_bytes(b'SUPABASE_URL="https://changed-after-validation.example"\n')
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query):
            return SimpleNamespace(fetchone=lambda: (self.role if query == "select current_user" else True,))

    monkeypatch.setattr(bootstrap.psycopg, "connect", lambda dsn, **kwargs: Connection(dsn))
    bootstrap.activate()
    assert (tmp_path / ".env").read_bytes() == payload
    assert (tmp_path / ".env.local").read_bytes() == old
