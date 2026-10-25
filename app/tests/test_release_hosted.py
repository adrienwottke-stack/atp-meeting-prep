"""Release planning tests never connect to Docker or SSH."""

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_hosted.py"
spec = importlib.util.spec_from_file_location("release_hosted", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize("tag", ["../bad", "bad tag", "bad;command", "-option", "x/other", "x$(id)", ""])
def test_invalid_release_names_are_rejected(tag):
    with pytest.raises(ValueError):
        release.release_name(tag)


@pytest.mark.parametrize(
    "path",
    [
        "app/.env",
        "app/output/setup/hosted-access.txt",
        "app/src/.env",
        "app/src/atp/../../private.py",
        "app/src/atp/secret.pem",
        "app/tests/test_pdf.py",
        "app/.venv/bin/python",
        "app/src/atp/__pycache__/x.pyc",
    ],
)
def test_build_allowlist_excludes_secrets_and_outputs(path):
    assert not release.allowed_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "app/Dockerfile",
        "app/pyproject.toml",
        "app/uv.lock",
        "app/src/atp/web.py",
        "app/src/atp/static/atp-logo.png",
    ],
)
def test_only_required_build_inputs_are_selected(path):
    assert release.allowed_path(path)


def test_default_dry_run_calls_only_local_git_reads(monkeypatch, capsys):
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if "rev-parse" in args:
            return "a" * 40
        if "ls-tree" in args:
            return "\n".join(
                [*release.BUILD_FILES, "app/src/atp/web.py", "app/.env", "app/output/setup/hosted-access.txt"]
            )
        raise AssertionError("Non-Git command during dry run")

    monkeypatch.setattr(release, "run", fake_run)
    assert release.main(["--release", "20261015-4", "--expected-current-release", "20261015-3-memory"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "dry-run" and result["revision"] == "a" * 40
    assert all(command[0] == "git" for command in commands)
    assert not any(".env" in path or "output" in path for path in result["files"])


def test_apply_requires_expected_current_release_before_any_commands():
    with pytest.raises(ValueError, match="expected-current-release"):
        release.apply({"expected_current_release": None})


def test_cannot_reuse_existing_release_tag():
    with pytest.raises(ValueError, match="differ"):
        release.plan("HEAD", "same", "same")


def test_command_errors_never_echo_secrets(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "SECRET", "SECRET")
    )
    with pytest.raises(RuntimeError) as error:
        release.run(["ssh", "host"])
    assert "SECRET" not in str(error.value)


def test_remote_script_syntax_is_valid():
    compile(release.REMOTE_APPLY, "remote-release.py", "exec")


def _archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo("app/src/atp/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "/outside/private"
        tar.addfile(link)
    return buffer.getvalue()


def test_context_uses_regular_allowed_archive_members_only(tmp_path, monkeypatch):
    archive = _archive(
        {
            "app/Dockerfile": b"FROM python",
            "app/src/atp/main.py": b"pass",
            "app/.env": b"secret",
            "app/src/atp/../../outside.py": b"unsafe",
        }
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, archive)
    )
    release.build_context("a" * 40, tmp_path)
    assert {
        str(path.relative_to(tmp_path)).replace("\\", "/") for path in tmp_path.rglob("*") if path.is_file()
    } == {"Dockerfile", "src/atp/main.py"}


def test_context_rejects_secret_material_without_echoing_it(tmp_path, monkeypatch):
    secret = b"sk-proj-" + b"x" * 40
    archive = _archive({"app/src/atp/main.py": secret})
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, archive)
    )
    with pytest.raises(ValueError) as error:
        release.build_context("a" * 40, tmp_path)
    assert secret.decode() not in str(error.value)
    assert not (tmp_path / "src/atp/main.py").exists()


def test_model_updates_preserve_other_lines_prefixes_and_secret_bytes():
    original = (
        b"# runtime\r\nOPENAI_API_KEY=private-value-\xff\r\n"
        b"MODEL_RESEARCH_EXTRA=preserve-prefix-setting\r\n"
        b" export MODEL_RESEARCH = old-model\r\nMODEL_SYNTHESIS=keep\r\n"
        b"MODEL_REVIEW=old-review\r\n"
    )
    expected = original.replace(b"MODEL_RESEARCH = old-model", b"MODEL_RESEARCH = gpt-6.1-sol")
    expected = expected.replace(b"MODEL_REVIEW=old-review", b"MODEL_REVIEW=gpt-6.1-sol")
    assert (
        release.update_runtime_models(
            original, {"MODEL_RESEARCH": "gpt-6.1-sol", "MODEL_REVIEW": "gpt-6.1-sol"}
        )
        == expected
    )
    assert release.update_runtime_models(original, {}) == original


def test_missing_model_key_is_added_with_existing_newline_and_other_model_is_untouched():
    original = b"TOKEN=private\r\nMODEL_RESEARCH=keep-this"
    assert (
        release.update_runtime_models(original, {"MODEL_REVIEW": "gpt-6.1-sol"})
        == original + b"\r\nMODEL_REVIEW=gpt-6.1-sol\r\n"
    )
    assert (
        release.update_runtime_models(b"", {"MODEL_REVIEW": "gpt-6.1-sol"}) == b"MODEL_REVIEW=gpt-6.1-sol\n"
    )
    bom = b"\xef\xbb\xbfMODEL_REVIEW=old\nTOKEN=private\n"
    assert (
        release.update_runtime_models(bom, {"MODEL_REVIEW": "new"})
        == b"\xef\xbb\xbfMODEL_REVIEW=new\nTOKEN=private\n"
    )


@pytest.mark.parametrize(
    "content",
    [
        b"MODEL_RESEARCH=one\nMODEL_RESEARCH=two\n",
        b"MODEL_REVIEW=one\r\n export MODEL_REVIEW =two\r\n",
    ],
)
def test_duplicate_model_keys_are_rejected_without_echoing_values(content):
    with pytest.raises(ValueError) as failure:
        release.update_runtime_models(content, {"MODEL_RESEARCH": "gpt-6.1-sol"})
    assert "one" not in str(failure.value) and "two" not in str(failure.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"MODEL_SYNTHESIS": "gpt-6.1-sol"},
        {"OPENAI_API_KEY": "private-value"},
        {"MODEL_REVIEW": "bad\nOPENAI_API_KEY=private-value"},
        {"MODEL_RESEARCH": "bad;command"},
        {"MODEL_REVIEW": "$(command)"},
        {"MODEL_REVIEW": "has space"},
        {"MODEL_REVIEW": ""},
        {"MODEL_REVIEW": None},
        [],
    ],
)
def test_model_override_allowlist_and_safe_identifiers(overrides):
    with pytest.raises(ValueError) as failure:
        release.validate_model_overrides(overrides)
    assert "private-value" not in str(failure.value)


def test_dry_run_exposes_only_explicit_model_values_and_uses_local_git(monkeypatch, capsys):
    commands = []

    def fake_run(args, **kwargs):
        commands.append(args)
        if "rev-parse" in args:
            return "b" * 40
        if "ls-tree" in args:
            return "\n".join(release.BUILD_FILES)
        raise AssertionError("Dry run must not connect to services")

    monkeypatch.setattr(release, "run", fake_run)
    assert (
        release.main(
            [
                "--release",
                "20261015-5",
                "--expected-current-release",
                "20261015-4",
                "--model-research",
                "gpt-6.1-sol",
                "--model-review",
                "gpt-6.1-sol",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["model_overrides"] == {"MODEL_RESEARCH": "gpt-6.1-sol", "MODEL_REVIEW": "gpt-6.1-sol"}
    assert all(command[0] == "git" for command in commands)
    assert release.main(["--release", "safe-tag", "--model-review", "private-value\nINJECTION=1"]) == 1
    assert "private-value" not in capsys.readouterr().out


def test_remote_updater_uses_same_validation_and_exact_bytes_without_running_remote_actions():
    namespace = {}
    exec(release.REMOTE_APPLY.split("import fcntl,", 1)[0], namespace)
    original = b"SECRET=private\nMODEL_REVIEW=old\n"
    assert (
        namespace["update_runtime_models"](original, {"MODEL_REVIEW": "gpt-6.1-sol"})
        == b"SECRET=private\nMODEL_REVIEW=gpt-6.1-sol\n"
    )
    with pytest.raises(ValueError):
        namespace["validate_model_overrides"]({"OPENAI_API_KEY": "do-not-change"})


@pytest.mark.parametrize(
    "fail_after_update,overrides",
    [
        (False, {}),
        (False, {"MODEL_RESEARCH": "gpt-6.1-sol", "MODEL_REVIEW": "gpt-6.1-sol"}),
        (True, {"MODEL_RESEARCH": "gpt-6.1-sol", "MODEL_REVIEW": "gpt-6.1-sol"}),
    ],
)
def test_remote_transaction_hashes_and_both_file_rollback_in_local_sandbox(
    tmp_path,
    monkeypatch,
    capsys,
    fail_after_update,
    overrides,
):
    # Run the real remote transaction code against a temporary directory; Docker,
    # Postgres, SSH and Unix locking are controlled stand-ins, never contacted.
    stage = tmp_path / "releases/new-release"
    stage.mkdir(parents=True)
    image = stage / "atp-image.tar"
    image.write_bytes(b"controlled-image")
    original_env = b"ATP_RELEASE=old-release\nATP_HOST=controlled.example\n"
    original_runtime = b"OPENAI_API_KEY=PRIVATE_SENTINEL\r\nMODEL_RESEARCH=old-model\r\n"
    (tmp_path / ".env").write_bytes(original_env)
    (tmp_path / "runtime.env").write_bytes(original_runtime)
    old_mtime = (tmp_path / "runtime.env").stat().st_mtime_ns
    revision = "c" * 40
    updates = []

    def controlled_run(args, **kwargs):
        stdout = ""
        if args[:3] == ["docker", "compose", "exec"]:
            stdout = '{"jobs":0,"memory_tasks":0}'
        elif args[:3] == ["docker", "image", "inspect"]:
            if "revision" in args[4]:
                stdout = revision
            else:
                stdout = "sha256:old" if args[-1] == "atp:old-release" else "sha256:new"
        elif args[:2] == ["docker", "inspect"]:
            stdout = {
                "{{.Image}}": "sha256:old",
                "{{.State.Health.Status}}": "healthy",
                "{{.State.Running}}|{{.RestartCount}}": "true|0",
            }[args[3]]
        elif args[:3] == ["docker", "compose", "up"]:
            updates.append(args)
            if fail_after_update and len(updates) == 1:
                raise subprocess.CalledProcessError(
                    1, args, output="PRIVATE_SENTINEL", stderr="PRIVATE_SENTINEL"
                )
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", controlled_run)
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "remote-release",
            "new-release",
            "old-release",
            hashlib.sha256(image.read_bytes()).hexdigest(),
            "sha256:new",
            revision,
            json.dumps(overrides).encode().hex(),
        ],
    )
    monkeypatch.chdir(tmp_path)
    assert "pathlib.Path('/opt/atp')" in release.REMOTE_APPLY
    code = release.REMOTE_APPLY.replace(
        "pathlib.Path('/opt/atp')", "pathlib.Path(" + repr(str(tmp_path)) + ")"
    )
    if fail_after_update:
        with pytest.raises(SystemExit) as failure:
            exec(compile(code, "controlled-remote-release", "exec"), {})
        assert failure.value.code == 1
    else:
        exec(compile(code, "controlled-remote-release", "exec"), {})
    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    response = json.loads(output)
    assert response["model_overrides"] == overrides
    manifest = json.loads((stage / "release.json").read_text())
    expected_runtime = release.update_runtime_models(original_runtime, overrides)
    assert manifest["runtime_sha256_before"] == hashlib.sha256(original_runtime).hexdigest()
    assert manifest["runtime_sha256_after"] == hashlib.sha256(expected_runtime).hexdigest()
    assert "PRIVATE_SENTINEL" not in json.dumps(manifest)
    if fail_after_update:
        assert (tmp_path / ".env").read_bytes() == original_env
        assert (tmp_path / "runtime.env").read_bytes() == original_runtime
        assert response["status"] == "rollback_requested"
    else:
        assert b"ATP_RELEASE=new-release" in (tmp_path / ".env").read_bytes()
        assert (tmp_path / "runtime.env").read_bytes() == expected_runtime
        assert response["status"] == "deployed"
        if not overrides:
            assert (tmp_path / "runtime.env").stat().st_mtime_ns == old_mtime
