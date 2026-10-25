"""The retired standalone project flow cannot be started or read through old URLs."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_http_reliability import app_with_repo

from atp.memory_store import PostgresMemoryStore
from atp.memory_workflow import MemoryWorkflow


async def test_retired_project_urls_are_unavailable_even_with_memory_enabled():
    app = app_with_repo(SimpleNamespace())
    app.state.settings.memory_enabled = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://unit.example",
        headers={"Authorization": "Bearer unit"},
    ) as client:
        response = await client.get("/projects")
        assert response.status_code == 307 and response.headers["location"] == "/knowledge"
        assert (await client.post("/v1/project-preparations", json={})).status_code == 404
        assert (await client.get(f"/v1/memory/tasks/{uuid4()}/pdf")).status_code == 404
        help_page = await client.get("/help")
        assert help_page.status_code == 200
        assert "Projektbriefing erstellen" not in help_page.text
        assert "Wissen & Learnings" in help_page.text


def test_store_rejects_project_creation_before_any_database_access():
    store = PostgresMemoryStore(SimpleNamespace(settings=SimpleNamespace()))
    with pytest.raises(ValueError, match="NOT_FOUND"):
        store.start_task(None, "project", {}, "retired-test")
    assert MemoryWorkflow.fixture({"kind": "learning"}).text


async def test_worker_rejects_old_project_jobs_without_model_or_storage_access():
    worker = MemoryWorkflow.__new__(MemoryWorkflow)
    saved = []
    worker.save = lambda job, **values: saved.append(values)
    await worker.process({"kind": "project"})
    assert saved == [{"error": "NOT_FOUND"}]
