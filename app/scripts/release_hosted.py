"""Prepare a committed, sanitized ATP image; remote changes require --apply.

Dry-run is the default and performs only local Git reads. Runtime secrets remain
on the server. This helper never calls prepare_hosted.py or runs DB migrations.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import io
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path

HOST = "root@127.0.0.1"
REMOTE = "/opt/atp"
ROOT = Path(__file__).resolve().parents[2]
BUILD_FILES = {"app/Dockerfile", "app/pyproject.toml", "app/uv.lock", "app/scripts/build_handbook.py"}
MODEL_KEYS = frozenset({"MODEL_RESEARCH", "MODEL_REVIEW"})
MODEL_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"


def validate_model_overrides(overrides: dict) -> dict:
    if not isinstance(overrides, dict) or not set(overrides) <= MODEL_KEYS:
        raise ValueError("Only MODEL_RESEARCH and MODEL_REVIEW may be overridden")
    if any(
        not isinstance(value, str) or not re.fullmatch(MODEL_ID_PATTERN, value)
        for value in overrides.values()
    ):
        raise ValueError("Model identifiers must contain only safe model-name characters")
    return dict(overrides)


def update_runtime_models(content: bytes, overrides: dict) -> bytes:
    """Replace explicit model settings without decoding or changing other bytes."""
    overrides = validate_model_overrides(overrides)
    if not overrides:
        return content
    lines = content.splitlines(keepends=True)
    assignment = re.compile(
        rb"^(?:\xef\xbb\xbf)?[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*"
    )
    seen, output = set(), []
    newline = next(
        (
            b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b"\r"
            for line in lines
            if line.endswith((b"\r", b"\n"))
        ),
        b"\n",
    )
    for line in lines:
        match = assignment.match(line)
        key = match.group(1).decode("ascii") if match else None
        if key in MODEL_KEYS:
            if key in seen:
                raise ValueError("Duplicate model setting in runtime configuration")
            seen.add(key)
        if key in overrides:
            ending = (
                b"\r\n"
                if line.endswith(b"\r\n")
                else b"\n"
                if line.endswith(b"\n")
                else b"\r"
                if line.endswith(b"\r")
                else b""
            )
            line = line[: match.end()] + overrides[key].encode("ascii") + ending
        output.append(line)
    for key in sorted(set(overrides) - seen):
        if output and not output[-1].endswith((b"\r", b"\n")):
            output[-1] += newline
        output.append(key.encode("ascii") + b"=" + overrides[key].encode("ascii") + newline)
    return b"".join(output)


# This script is delivered over stdin. No runtime secrets leave the remote host.
REMOTE_APPLY = (
    "import re\n"
    + "MODEL_KEYS = frozenset({'MODEL_RESEARCH', 'MODEL_REVIEW'})\n"
    + "MODEL_ID_PATTERN = "
    + repr(MODEL_ID_PATTERN)
    + "\n"
    + inspect.getsource(validate_model_overrides)
    + "\n"
    + inspect.getsource(update_runtime_models)
    + "\n"
    + r'''
import fcntl,hashlib,json,os,pathlib,re,shutil,subprocess,sys,time
release,expected,archive_hash,image_id,commit,overrides_hex=sys.argv[1:]
if not re.fullmatch(r'[0-9a-f]{4,4096}',overrides_hex): raise RuntimeError('INVALID_MODEL_OVERRIDES')
model_overrides=validate_model_overrides(json.loads(bytes.fromhex(overrides_hex)))
root=pathlib.Path('/opt/atp'); os.chdir(root)
lock=(root/'.release.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
stage=root/'releases'/release
def run(args,**kw):
    return subprocess.run(args,text=True,capture_output=True,check=True,**kw)
def current():
    values=dict(line.split('=',1) for line in (root/'.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    return values.get('ATP_RELEASE')
def queues():
    code="""import json,os,psycopg
with psycopg.connect(os.environ['WORKER_DATABASE_URL'],connect_timeout=10) as db:
 db.execute('set transaction read only')
 tables={r[0] for r in db.execute("select table_name from information_schema.tables where table_schema='atp'").fetchall()}
 counts={}
 for table in ['jobs','memory_tasks','deletion_jobs']:
  if table in tables:
   counts[table]=db.execute("select count(*) from atp."+table+" where status in ('queued','running')").fetchone()[0]
 if 'storage_cleanup' in tables:
  counts['storage_cleanup']=db.execute("select count(*) from atp.storage_cleanup where next_attempt<=now()").fetchone()[0]
 print(json.dumps(counts))
"""
    return json.loads(run(['docker','compose','exec','-T','worker','python','-'],input=code).stdout)
def idle():
    counts=queues()
    if any(counts.values()): raise RuntimeError('ACTIVE_QUEUE')
    return counts
if current()!=expected: raise RuntimeError('CURRENT_RELEASE_CHANGED')
archive=stage/'atp-image.tar'
digest=hashlib.sha256()
with archive.open('rb') as stream:
 for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
if digest.hexdigest()!=archive_hash: raise RuntimeError('TRANSFER_HASH_MISMATCH')
counts=idle()
run(['docker','load','-i',str(archive)])
actual=run(['docker','image','inspect','--format','{{.Id}}','atp:'+release]).stdout.strip()
if actual!=image_id: raise RuntimeError('LOADED_IMAGE_MISMATCH')
revision=run(['docker','image','inspect','--format','{{index .Config.Labels "org.opencontainers.image.revision"}}','atp:'+release]).stdout.strip()
if revision!=commit: raise RuntimeError('IMAGE_REVISION_MISMATCH')
rollback=stage/'rollback'; rollback.mkdir(mode=0o700)
for name in ['.env','runtime.env']:
 shutil.copy2(root/name,rollback/name); os.chmod(rollback/name,0o600)
previous_images={name:run(['docker','inspect','--format','{{.Image}}',name]).stdout.strip() for name in ['atp-web-1','atp-worker-1']}
previous_tag_id=run(['docker','image','inspect','--format','{{.Id}}','atp:'+expected]).stdout.strip()
if set(previous_images.values())!={previous_tag_id}: raise RuntimeError('PREVIOUS_RELEASE_IMAGE_MISMATCH')
run(['docker','tag',previous_tag_id,'atp:rollback-'+release])
runtime_before=(root/'runtime.env').read_bytes()
runtime_hash=hashlib.sha256(runtime_before).hexdigest()
if hashlib.sha256((rollback/'runtime.env').read_bytes()).hexdigest()!=runtime_hash: raise RuntimeError('RUNTIME_CHANGED_DURING_BACKUP')
runtime_after=update_runtime_models(runtime_before,model_overrides)
runtime_expected_hash=hashlib.sha256(runtime_after).hexdigest()
manifest={'previous_release':expected,'release':release,'revision':commit,'archive_sha256':archive_hash,'image_id':image_id,'previous_images':previous_images,'runtime_sha256':runtime_hash,'runtime_sha256_before':runtime_hash,'runtime_sha256_after':runtime_expected_hash,'model_overrides':model_overrides,'queue_counts':counts}
(stage/'release.json').write_text(json.dumps(manifest,indent=2)); os.chmod(stage/'release.json',0o600)
stopped=False
runtime_restore_required=False
try:
 if current()!=expected: raise RuntimeError('CURRENT_RELEASE_CHANGED')
 idle()
 stopped=True
 run(['docker','compose','stop','web'])
 idle()
 run(['docker','compose','stop','worker'])
 if hashlib.sha256((root/'runtime.env').read_bytes()).hexdigest()!=runtime_hash: raise RuntimeError('RUNTIME_CHANGED_BEFORE_SWITCH')
 if model_overrides:
  runtime_restore_required=True
  temporary=root/'runtime.env.release-new'
  descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(descriptor,'wb') as stream:
   stream.write(runtime_after); stream.flush(); os.fsync(stream.fileno())
  temporary.replace(root/'runtime.env')
 text=(root/'.env').read_text()
 if len(re.findall(r'^ATP_RELEASE=.*$',text,re.M))!=1: raise RuntimeError('COMPOSE_RELEASE_NOT_UNIQUE')
 updated=re.sub(r'^ATP_RELEASE=.*$','ATP_RELEASE='+release,text,flags=re.M)
 temporary=root/'.env.release-new'; temporary.write_text(updated); os.chmod(temporary,0o600); temporary.replace(root/'.env')
 run(['docker','compose','up','-d','--no-deps','web','worker'])
 ready=False
 for attempt in range(60):
  health=run(['docker','inspect','--format','{{.State.Health.Status}}','atp-web-1']).stdout.strip()
  worker=run(['docker','inspect','--format','{{.State.Running}}|{{.RestartCount}}','atp-worker-1']).stdout.strip()
  if health=='healthy' and worker=='true|0':
   ready=True;break
  time.sleep(2)
 if not ready: raise RuntimeError('READINESS_FAILED')
 if hashlib.sha256((root/'runtime.env').read_bytes()).hexdigest()!=runtime_expected_hash: raise RuntimeError('RUNTIME_CHANGED')
 print(json.dumps({'status':'deployed','release':release,'revision':commit,'rollback':str(rollback),'image_id':image_id,'model_overrides':model_overrides}))
except Exception as exc:
 if stopped:
  shutil.copy2(rollback/'.env',root/'.env'); os.chmod(root/'.env',0o600)
  if runtime_restore_required:
   shutil.copy2(rollback/'runtime.env',root/'runtime.env'); os.chmod(root/'runtime.env',0o600)
  subprocess.run(['docker','compose','up','-d','--no-deps','web','worker'],capture_output=True)
 print(json.dumps({'status':'rollback_requested' if stopped else 'not_switched','error_type':type(exc).__name__,'rollback':str(rollback),'model_overrides':model_overrides}))
 sys.exit(1)
'''
)


def run(args: list[str], *, data: str | None = None) -> str:
    result = subprocess.run(args, input=data, text=True, capture_output=True)
    if result.returncode:
        # Do not echo arbitrary command output: remote tools may include secrets.
        raise RuntimeError(f"COMMAND_FAILED:{Path(args[0]).name}:{result.returncode}")
    return result.stdout.strip()


def release_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value) or ".." in value:
        raise ValueError("Release must be an unambiguous safe image tag")
    return value


def allowed_path(name: str) -> bool:
    path = Path(name)
    if name in BUILD_FILES:
        return True
    if re.fullmatch(r"app/docs/handbook/[0-9]{2}-[A-Za-z0-9_-]+\.md", name):
        return True
    if not name.startswith("app/src/") or ".." in path.parts:
        return False
    return not any(
        part.startswith(".") or part in {"__pycache__", "output", "node_modules"} for part in path.parts
    ) and path.suffix not in {".pyc", ".env", ".key", ".pem"}


def plan(commit: str, release: str, expected: str | None, *, model_overrides: dict | None = None) -> dict:
    model_overrides = validate_model_overrides({} if model_overrides is None else model_overrides)
    release_name(release)
    if expected:
        release_name(expected)
        if release == expected:
            raise ValueError("New release must differ from current release")
    revision = run(["git", "-C", str(ROOT), "rev-parse", "--verify", commit + "^{commit}"])
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid committed revision")
    names = run(["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", revision, "app"]).splitlines()
    files = [name for name in names if allowed_path(name)]
    if not BUILD_FILES <= set(files):
        raise ValueError("Missing committed build inputs")
    return {
        "mode": "dry-run",
        "host": HOST,
        "revision": revision,
        "image": "atp:" + release,
        "release": release,
        "expected_current_release": expected,
        "files": files,
        "model_overrides": model_overrides,
        "runtime_env": "only explicit model overrides changed; other bytes preserved; never exported"
        if model_overrides
        else "preserved on server; never exported",
        "remote_archive": REMOTE + "/releases/" + release + "/atp-image.tar",
        "steps": [
            "Archive allowed committed files",
            "Build linux/amd64 image and save archive",
            "Transfer and verify SHA256",
            "Require unchanged current release and idle queues",
            "Preserve private rollback env files and previous image IDs",
            "Stop web; recheck queues; stop worker",
            "Change ATP_RELEASE and only explicit model overrides; start web/worker"
            if model_overrides
            else "Change only ATP_RELEASE; start web/worker",
            "Verify health; restore prior release on failure",
        ],
    }


def build_context(revision: str, target: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", revision, "app"], capture_output=True, check=True
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if not member.isfile() or not allowed_path(member.name):
                continue
            # Only whitelisted regular files are copied; no symlinks or traversal.
            destination = target / Path(member.name).relative_to("app")
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError("Archive path escaped build context")
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(member).read()
            if re.search(
                rb"(?:sb_secret_|sk-proj-)[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----",
                data,
            ):
                raise ValueError("Secret-like material in build input; value suppressed")
            destination.write_bytes(data)


def apply(details: dict) -> dict:
    expected = details["expected_current_release"]
    if not expected:
        raise ValueError("--apply requires --expected-current-release")
    overrides = validate_model_overrides(details.get("model_overrides", {}))
    overrides_hex = json.dumps(overrides, sort_keys=True, separators=(",", ":")).encode("utf-8").hex()
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST]
    release = details["release"]
    stage = REMOTE + "/releases/" + release
    with tempfile.TemporaryDirectory(prefix="atp-release-") as directory:
        directory = Path(directory)
        context = directory / "context"
        context.mkdir()
        build_context(details["revision"], context)
        run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--build-arg",
                "ATP_COMMIT=" + details["revision"],
                "--label",
                "org.opencontainers.image.revision=" + details["revision"],
                "-t",
                details["image"],
                str(context),
            ]
        )
        image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", details["image"]])
        archive = directory / "atp-image.tar"
        run(["docker", "save", "-o", str(archive), details["image"]])
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        # Fail if this release directory already exists; never replace a rollback.
        run([*ssh, "umask 077; mkdir -p /opt/atp/releases && mkdir " + stage])
        run(
            [
                "scp",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                str(archive),
                HOST + ":" + stage + "/atp-image.tar",
            ]
        )
        result = run(
            [
                *ssh,
                "python3 - "
                + " ".join(
                    [release, expected, digest.hexdigest(), image_id, details["revision"], overrides_hex]
                ),
            ],
            data=REMOTE_APPLY,
        )
        return json.loads(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--release", required=True)
    parser.add_argument("--expected-current-release")
    parser.add_argument("--model-research")
    parser.add_argument("--model-review")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        overrides = {
            key: value
            for key, value in (("MODEL_RESEARCH", args.model_research), ("MODEL_REVIEW", args.model_review))
            if value is not None
        }
        details = plan(args.commit, args.release, args.expected_current_release, model_overrides=overrides)
        result = apply(details) if args.apply else details
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
