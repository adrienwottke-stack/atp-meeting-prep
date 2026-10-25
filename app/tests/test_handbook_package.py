import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pymupdf
import pytest


def module(name):
    path = Path(__file__).parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


export = module("export_package")
handbook = module("build_handbook")


@pytest.mark.parametrize(
    "path",
    [
        "app/.env",
        "app/.env.production",
        "app/output/setup/hosted-access.txt",
        "app/src/atp/output/data.json",
        "app/src/atp/private.key",
        "app/src/atp/.env",
        "app/.venv/a.py",
        "app/scripts/bootstrap_cloud.py",
        "docs/referenz/meeting.pdf",
        "../app/src/secret.py",
        "app/src/../../secret.py",
    ],
)
def test_export_forbids_runtime_private_and_unreviewed_files(path):
    assert not export.allowed(path)


@pytest.mark.parametrize(
    "path",
    [
        "app/.env.example",
        "app/src/atp/worker.py",
        "app/src/atp/static/Switzer-Regular.ttf",
        "app/docs/handbook/00-einstieg.md",
        "app/supabase/migrations/001_schema.sql",
    ],
)
def test_export_allowed_source_paths(path):
    assert export.allowed(path)


def test_secret_findings_never_echo_the_secret():
    secret = b"sk-" + b"x" * 40
    with pytest.raises(ValueError) as failure:
        export.check_content("app/src/leak.py", secret)
    assert "leak.py" in str(failure.value)
    assert secret.decode() not in str(failure.value)
    with pytest.raises(ValueError, match="SECRET_PATTERN_DETECTED"):
        export.check_content("app/src/leak.py", b"postgresql://operator:actual-password@host/postgres")
    export.check_content(
        "app/.env.example", b"DATABASE_URL=postgresql://atp_web:REPLACE_PASSWORD@localhost/postgres"
    )


def make_repo(root):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)

    git("init", "-b", "main")
    git("config", "user.name", "Synthetic Package Test")
    git("config", "user.email", "package-test@example.invalid")
    for name in export.REQUIRED:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# controlled fixture\n", "utf-8")
    private = root / "app/.env"
    private.write_text("excluded-private-fixture", "utf-8")
    git("add", ".")
    git("commit", "-m", "synthetic source")
    return git


def test_export_uses_committed_bytes_excludes_private_and_matches_manifest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git = make_repo(source)
    (source / "app/src/atp/web.py").write_text("uncommitted change", "utf-8")
    output = tmp_path / "delivery.zip"
    manifest = export.export_package(source, "HEAD", output)
    with zipfile.ZipFile(output) as archive:
        assert "app/.env" not in archive.namelist()
        assert archive.read("app/src/atp/web.py") == b"# controlled fixture\n"
        assert "SHA256-MANIFEST.json" in archive.namelist()
        assert manifest["git_revision"] == git("rev-parse", "HEAD").decode().strip()
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
    second = tmp_path / "delivery-again.zip"
    export.export_package(source, "HEAD", second)
    assert output.read_bytes() == second.read_bytes()
    with pytest.raises(ValueError, match="OUTPUT_ALREADY_EXISTS"):
        export.export_package(source, "HEAD", output)


def test_export_rejects_git_symlink_entries_without_following_targets(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git = make_repo(source)
    target = source / "symlink-target.txt"
    target.write_text("../../private\n", "utf-8")
    blob = git("hash-object", "-w", "symlink-target.txt").decode().strip()
    git("update-index", "--add", "--cacheinfo", f"120000,{blob},app/src/leak.py")
    git("commit", "-m", "synthetic symlink index entry")
    with pytest.raises(ValueError, match="NON_REGULAR_FILE:app/src/leak.py"):
        export.export_package(source, "HEAD", tmp_path / "rejected.zip")
    assert not (tmp_path / "rejected.zip").exists()


def test_handbook_builds_all_chapters_links_and_matching_source_manifest(tmp_path):
    output = tmp_path / "handbook.pdf"
    result = handbook.build_handbook(output, revision="controlled-test-version")
    doc = pymupdf.open(output)
    text = "\n".join(page.get_text() for page in doc)
    assert len(doc) >= 6
    assert "kontrollierten vollständigen Auftrag" in text
    assert "vollständiger Restore" in text
    assert "unabhängige Übernahme" in text
    assert "controlled-test-version" in text
    assert doc.get_toc()
    assert sum(bool(page.get_links()) for page in doc) >= 1
    assert result["pdf_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert json.loads(output.with_suffix(".manifest.json").read_text("utf-8")) == result
    assert len(result["sources"]) == 4
