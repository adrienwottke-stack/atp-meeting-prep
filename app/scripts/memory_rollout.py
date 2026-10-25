"""Run on the existing host; no credentials are printed or transferred by this script."""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/opt/atp")


def run(*args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        print(json.dumps({"status": "failed", "command": args[:3], "returncode": result.returncode}))
        raise SystemExit(1)
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["disable", "enable", "rollback", "status"])
    parser.add_argument("--user-id", default="")
    args = parser.parse_args()
    if args.action == "status":
        print(run("docker", "compose", "ps", "--format", "{{.Service}} {{.State}} {{.Health}}"))
        return
    if not (ROOT / "runtime.before-memory.env").exists():
        shutil.copy2(ROOT / "runtime.env", ROOT / "runtime.before-memory.env")
        (ROOT / "runtime.before-memory.env").chmod(0o600)
        shutil.copy2(ROOT / ".env", ROOT / "release.before-memory.env")
    if args.action == "rollback":
        shutil.copy2(ROOT / "runtime.before-memory.env", ROOT / "runtime.env")
        shutil.copy2(ROOT / "release.before-memory.env", ROOT / ".env")
    else:
        code = "from atp.repository import Repository; from atp.config import Settings; import json; r=Repository(Settings()); c=r._connection(worker=True); d=c.__enter__(); print(json.dumps({'active': d.execute(\"select count(*) as n from atp.jobs where status='running' and lease_until>now()\").fetchone()['n']})); c.__exit__(None,None,None)"
        active = json.loads(run("docker", "compose", "exec", "-T", "worker", "python", "-c", code))
        if active["active"]:
            print(json.dumps({"status": "busy", "active_jobs": active["active"]}))
            raise SystemExit(2)
        path = ROOT / "runtime.env"
        content = re.sub(
            r"^(MEMORY_ENABLED|MEMORY_USER_IDS|WORKER_QUEUE)=.*\n?", "", path.read_text(), flags=re.M
        )
        if args.action == "enable" and not args.user_id:
            raise SystemExit("User ID required")
        content += (
            "\nMEMORY_ENABLED="
            + ("true" if args.action == "enable" else "false")
            + "\nMEMORY_USER_IDS="
            + args.user_id
            + "\nWORKER_QUEUE=live\n"
        )
        path.write_text(content)
        path.chmod(0o600)
        release = ROOT / ".env"
        release.write_text(
            re.sub(r"^ATP_RELEASE=.*$", "ATP_RELEASE=20261015-3-memory", release.read_text(), flags=re.M)
        )
    run("docker", "compose", "up", "-d", "web", "worker")
    print(json.dumps({"status": "updated", "memory_enabled": args.action == "enable", "action": args.action}))


if __name__ == "__main__":
    main()
