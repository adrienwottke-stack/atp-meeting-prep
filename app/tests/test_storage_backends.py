from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from atp.config import Settings
from atp.storage import Storage


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    # These tests must not inherit machine configuration or load the real .env.
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.mark.parametrize(
    ("mode", "backend", "expected"),
    [
        ("local", "auto", "local"),
        ("hosted", "auto", "supabase"),
        ("local", "supabase", "supabase"),
        ("local", "local", "local"),
        ("hosted", "supabase", "supabase"),
    ],
)
def test_backend_resolution(mode, backend, expected):
    settings = Settings(
        _env_file=None, app_mode=mode, storage_backend=backend, app_base_url="https://app.example"
    )
    assert settings.effective_storage_backend == expected


def test_hosted_cannot_use_local_storage():
    with pytest.raises(ValidationError, match="Supabase Storage"):
        Settings(
            _env_file=None,
            app_mode="hosted",
            storage_backend="local",
            app_base_url="https://app.example",
        )


def test_invalid_storage_backend():
    with pytest.raises(ValidationError, match="STORAGE_BACKEND"):
        Settings(_env_file=None, storage_backend="public")


@pytest.mark.parametrize("key_field", ["supabase_secret_key", "supabase_service_role_key"])
def test_cloud_storage_requires_privileged_key_even_for_local_webapp(key_field):
    settings = Settings(
        _env_file=None,
        app_mode="local",
        storage_backend="supabase",
        fixture_mode=True,
        database_url="postgresql://example.invalid/web",
        worker_database_url="postgresql://example.invalid/worker",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="public-test-key",
    )
    assert any("SUPABASE_SECRET_KEY" in issue for issue in settings.setup_issues())
    with_key = settings.model_copy(update={key_field: "test-privileged-key"})
    assert with_key.setup_issues() == []


@pytest.mark.parametrize(
    ("modern", "legacy", "expected", "bearer"),
    [
        ("sb_secret_test", "legacy.jwt.test", "sb_secret_test", False),
        ("", "legacy.jwt.test", "legacy.jwt.test", True),
        ("", "sb_secret_in_legacy_setting", "sb_secret_in_legacy_setting", False),
    ],
)
def test_storage_client_headers(tmp_path, modern, legacy, expected, bearer):
    settings = Settings(
        _env_file=None,
        output_dir=tmp_path,
        supabase_url="https://example.supabase.co",
        supabase_secret_key=modern,
        supabase_service_role_key=legacy,
    )
    with Storage(settings)._client() as client:
        assert client.headers["apikey"] == expected
        assert ("authorization" in client.headers) is bearer
        if bearer:
            assert client.headers["authorization"] == "Bearer " + expected
    for key in (modern, legacy):
        if key:
            assert key not in repr(settings)


def test_local_webapp_cloud_storage_all_operations(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        app_mode="local",
        storage_backend="supabase",
        output_dir=tmp_path,
        supabase_url="https://example.supabase.co",
        supabase_secret_key="sb_secret_test",
    )
    storage = Storage(settings)
    org, meeting, run = (str(uuid4()) for _ in range(3))
    path = f"{org}/{meeting}/{run}/result.pdf"
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["apikey"] == "sb_secret_test"
        assert "authorization" not in request.headers
        if "/object/list/" in request.url.path:
            entries = (
                [{"name": "result.pdf", "id": "test-object"}]
                if run.encode() in request.content
                else [{"name": run, "id": None}]
            )
            return httpx.Response(200, json=entries)
        if request.method == "GET":
            return httpx.Response(200, content=b"%PDF-test")
        return httpx.Response(200, json={})

    real_client = httpx.Client

    def mock_client(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("atp.storage.httpx.Client", mock_client)
    assert storage.put_pdf(org, meeting, run, b"%PDF-test") == path
    assert storage.get_pdf(path) == b"%PDF-test"
    storage.delete_pdf(path)
    storage.delete_meeting(org, meeting)
    assert [request.method for request in requests] == ["POST", "GET", "DELETE", "POST", "POST", "DELETE"]
    assert requests[0].url.path.endswith("/object/briefings/" + path)
    assert requests[1].url.path.endswith("/object/authenticated/briefings/" + path)
    assert not (tmp_path / "briefings").exists()


def test_legacy_simple_namespace_local_storage(tmp_path):
    storage = Storage(SimpleNamespace(app_mode="local", output_dir=tmp_path))
    org, meeting, run = (str(uuid4()) for _ in range(3))
    path = storage.put_pdf(org, meeting, run, b"%PDF-test")
    assert storage.get_pdf(path) == b"%PDF-test"
    storage.delete_pdf(path)
    assert not (tmp_path / "briefings" / path).exists()
    storage.put_pdf(org, meeting, run, b"%PDF-test")
    storage.delete_meeting(org, meeting)
    assert not (tmp_path / "briefings" / org / meeting).exists()
