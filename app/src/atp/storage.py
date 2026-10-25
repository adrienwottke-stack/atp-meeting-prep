"""Private PDF storage. Paths are generated from server-owned UUIDs only."""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

import httpx


def _uuid(value):
    return str(UUID(str(value)))


class Storage:
    def __init__(self, settings):
        self.settings = settings
        self.base = (Path(settings.output_dir) / "briefings").resolve()

    @property
    def _backend(self):
        backend = getattr(self.settings, "effective_storage_backend", None)
        if backend is not None:
            return backend
        backend = getattr(self.settings, "storage_backend", "auto")
        if backend == "auto":
            return "local" if self.settings.app_mode == "local" else "supabase"
        return backend

    def _path(self, path: str):
        pieces = PurePosixPath(path).parts
        if len(pieces) != 4 or pieces[3] != "result.pdf":
            raise ValueError("INVALID_STORAGE_PATH")
        try:
            normalized = "/".join([*[_uuid(x) for x in pieces[:3]], "result.pdf"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("INVALID_STORAGE_PATH") from exc
        if normalized != path:
            raise ValueError("INVALID_STORAGE_PATH")
        target = (self.base / path).resolve()
        if not target.is_relative_to(self.base):
            raise ValueError("INVALID_STORAGE_PATH")
        return normalized, target

    def _client(self, timeout=None):
        key = getattr(self.settings, "storage_api_key", None)
        if key is None:
            key = getattr(self.settings, "supabase_secret_key", "") or getattr(
                self.settings, "supabase_service_role_key", ""
            )
        if not self.settings.supabase_url or not key:
            raise ValueError("STORAGE_NOT_CONFIGURED")
        headers = {"apikey": key}
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = "Bearer " + key
        return httpx.Client(
            base_url=self.settings.supabase_url.rstrip("/") + "/storage/v1/",
            headers=headers,
            timeout=20 if timeout is None else max(0.1, min(20, timeout)),
            trust_env=False,
            follow_redirects=False,
        )

    def put_pdf(self, org_id, meeting_id, run_id, data: bytes, *, timeout=None) -> str:
        if timeout is not None and timeout <= 0:
            raise ValueError("STORAGE_DEADLINE")
        if not data.startswith(b"%PDF-") or len(data) > 10 * 1024 * 1024:
            raise ValueError("INVALID_PDF")
        path = "/".join([_uuid(org_id), _uuid(meeting_id), _uuid(run_id), "result.pdf"])
        path, target = self._path(path)
        if self._backend == "local":
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name("." + str(uuid4()) + ".tmp")
            try:
                temporary.write_bytes(data)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            with self._client(timeout) as client:
                response = client.post(
                    "object/" + self.settings.storage_bucket + "/" + path,
                    content=data,
                    headers={"Content-Type": "application/pdf", "x-upsert": "true"},
                )
                response.raise_for_status()
        return path

    def get_pdf(self, path) -> bytes:
        path, target = self._path(path)
        if self._backend == "local":
            return target.read_bytes()
        with self._client() as client:
            response = client.get("object/authenticated/" + self.settings.storage_bucket + "/" + path)
            response.raise_for_status()
            return response.content

    def delete_pdf(self, path):
        """Remove one server-generated run object, e.g. after a lost lease."""
        path, target = self._path(path)
        if self._backend == "local":
            target.unlink(missing_ok=True)
        else:
            with self._client() as client:
                response = client.request(
                    "DELETE", "object/" + self.settings.storage_bucket, json={"prefixes": [path]}
                )
                if response.status_code != 404:
                    response.raise_for_status()

    def delete_meeting(self, org_id, meeting_id):
        prefix = _uuid(org_id) + "/" + _uuid(meeting_id)
        target = (self.base / prefix).resolve()
        if not target.is_relative_to(self.base) or target == self.base:
            raise ValueError("INVALID_STORAGE_PATH")
        if self._backend == "local":
            if target.exists():
                shutil.rmtree(target)
            return
        with self._client() as client:
            paths = []

            def collect(folder, depth=0):
                if depth > 2:
                    raise ValueError("INVALID_STORAGE_PATH")
                offset = 0
                while True:
                    response = client.post(
                        "object/list/" + self.settings.storage_bucket,
                        json={"prefix": folder, "limit": 100, "offset": offset},
                    )
                    response.raise_for_status()
                    entries = response.json()
                    for entry in entries:
                        name = entry.get("name", "")
                        if "/" in name or name in ("", ".", ".."):
                            raise ValueError("INVALID_STORAGE_PATH")
                        item = folder + "/" + name
                        if entry.get("id"):
                            paths.append(item)
                        else:
                            collect(item, depth + 1)
                    if len(entries) < 100:
                        break
                    offset += 100

            collect(prefix)
            for offset in range(0, len(paths), 100):
                response = client.request(
                    "DELETE",
                    "object/" + self.settings.storage_bucket,
                    json={"prefixes": paths[offset : offset + 100]},
                )
                response.raise_for_status()
