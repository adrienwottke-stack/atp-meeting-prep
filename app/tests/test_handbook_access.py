from pathlib import Path

import httpx

from atp.handbook import render_handbook


def test_usage_handbook_hides_operator_chapters_and_escapes_text(tmp_path):
    (tmp_path / "00-einstieg.md").write_text("# Einstieg\n<script>injected</script>", encoding="utf-8")
    (tmp_path / "02-betrieb.md").write_text("# OperatorOnly", encoding="utf-8")
    member = render_handbook(tmp_path)
    assert "&lt;script&gt;" in member and "<script>" not in member
    assert "OperatorOnly" not in member
    assert "OperatorOnly" in render_handbook(tmp_path, operator=True)


async def test_help_requires_authentication_and_member_cannot_download_operator_pdf():
    from types import SimpleNamespace

    from test_http_reliability import app_with_repo

    app = app_with_repo(SimpleNamespace())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        assert (await client.get("/help")).status_code == 401
        response = await client.get("/help", headers={"Authorization": "Bearer unit"})
        assert response.status_code == 200 and "<h1>" in response.text
        assert (
            await client.get("/v1/handbook/pdf", headers={"Authorization": "Bearer unit"})
        ).status_code == 404


def test_docker_handbook_build_inputs_are_explicit():
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("release_helper", root / "scripts/release_hosted.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    assert release.allowed_path("app/scripts/build_handbook.py")
    assert release.allowed_path("app/docs/handbook/01-nutzung.md")
    assert not release.allowed_path("app/docs/handbook/.env")
    assert not release.allowed_path("app/docs/handbook/../../private.md")
