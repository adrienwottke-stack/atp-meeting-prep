"""Export a sanitized, allowlisted committed source package; never contact services."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ("app/src/", "app/supabase/migrations/", "app/docs/handbook/")
FILES = {
    ".gitattributes",
    ".gitignore",
    "app/pyproject.toml",
    "app/uv.lock",
    "app/Dockerfile",
    "app/.dockerignore",
    "app/.env.example",
    "app/supabase/config.toml",
    "app/deploy/compose.yaml",
    "app/deploy/atp-retention.service",
    "app/deploy/atp-retention.timer",
    "app/scripts/setup_local.ps1",
    "app/scripts/build_handbook.py",
    "app/scripts/export_package.py",
    "app/tests/test_rules.py",
    "app/tests/test_navigation_cleanup.py",
    "app/tests/test_frontend_modules.py",
    "app/tests/test_clarification_presentation.py",
    "app/tests/test_http_reliability.py",
    "app/tests/test_clarification_domain.py",
    "app/tests/test_briefing_content.py",
    "app/tests/test_leadership_components.py",
    "app/tests/test_pdf.py",
    "app/tests/fixtures/clarification-scenarios-v1.json",
    "docs/archive/initial-package/docs/ATP-Anforderungskatalog-Meeting-Vorbereitungs-Agent-v0.3.json",
}
REQUIRED = {
    "app/pyproject.toml",
    "app/uv.lock",
    "app/src/atp/web.py",
    "app/src/atp/worker.py",
    "app/scripts/setup_local.ps1",
}
FORBIDDEN = {"output", "setup", "__pycache__", ".venv", ".git", "node_modules", ".temp", ".branches"}
SECRET_PATTERNS = [
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(rb"\bsb_secret_[A-Za-z0-9_-]{16,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    re.compile(rb"postgres(?:ql)?://[^@\s:'\"]+:([^@\s'\"/]+)@"),
    re.compile(rb"(?i)(?:password|api_key|secret_key|access_token)\s*[=:]\s*['\"]?([A-Za-z0-9_+/=-]{28,})"),
]


def allowed(path):
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in path or ":" in path:
        return False
    if set(parsed.parts) & FORBIDDEN:
        return False
    if parsed.name.startswith(".env") and path != "app/.env.example":
        return False
    if parsed.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".log", ".sqlite", ".db"}:
        return False
    return path in FILES or any(path.startswith(prefix) for prefix in PREFIXES)


def check_content(path, data):
    # Binary visual assets cannot carry runtime config; filenames are still
    # allowlisted, and their exact bytes are covered by the package manifest.
    if PurePosixPath(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".ttf", ".woff", ".woff2", ".ico"}:
        return
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(data):
            candidate = match.group(0).upper()
            if any(marker in candidate for marker in (b"REPLACE_", b"PLACEHOLDER", b"SYNTHETIC_")):
                continue
            raise ValueError("SECRET_PATTERN_DETECTED:" + path)


def _git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def export_package(source_root: Path, revision: str, output: Path):
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    commit = (
        _git(source_root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
        .decode()
        .strip()
    )
    entries = _git(source_root, "ls-tree", "-r", "-z", commit).split(b"\x00")
    payload = {}
    for entry in entries:
        if not entry:
            continue
        info, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        if not allowed(path):
            continue
        mode, kind, object_id = info.split(b" ")
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ValueError("NON_REGULAR_FILE:" + path)
        data = _git(source_root, "cat-file", "blob", object_id.decode())
        check_content(path, data)
        payload[path] = data
    missing = REQUIRED - payload.keys()
    if missing:
        raise ValueError("REQUIRED_FILES_MISSING:" + ",".join(sorted(missing)))
    payload["README.md"] = (
        "# ATP Quellpaket\n\nVersion: " + commit + "\n\n"
        "Einstieg: app/docs/handbook/00-einstieg.md. Lokale Einrichtung: app/docs/handbook/03-entwicklung.md.\n"
        "Dieses Paket enthält ausgewählte portable Tests und keine Runtimezugänge, Betriebsdaten oder Live-Nachweise.\n"
        "Neue Cloudinstanz, vollständiger Restore und unabhängige Übernahme benötigen eigene Abnahme.\n"
    ).encode("utf-8")
    manifest = {
        "format": "atp-sanitized-source-1",
        "git_revision": commit,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())},
        "limits": [
            "Known secret-pattern checks are not a complete privacy review.",
            "No runtime credentials, database backup or private PDFs included.",
            "Contains selected portable tests; full release suite is maintained in the source repository.",
        ],
    }
    payload["SHA256-MANIFEST.json"] = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    with zipfile.ZipFile(output) as archive:
        for name, digest in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("PACKAGE_HASH_MISMATCH:" + name)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_package(args.source_root, args.revision, args.output)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else "PACKAGE_EXPORT_FAILED") from None
    print(
        json.dumps(
            {"revision": result["git_revision"], "files": len(result["files"]), "output": str(args.output)}
        )
    )


if __name__ == "__main__":
    main()
