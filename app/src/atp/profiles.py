"""Immutable file-backed ATP reference profiles; never consult the web at runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROFILE_VERSION = "ATP-2026-10-15.2"
PROFILE_DIR = Path(__file__).with_name("profiles")


def load_profile(version: str | None = None) -> dict:
    version = version or PROFILE_VERSION
    if not version or any(
        c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-_" for c in version
    ):
        raise ValueError("Ungültige ATP-Profilversion.")
    path = PROFILE_DIR / f"{version}.json"
    if not path.is_file():
        raise ValueError(f"ATP-Profilversion {version} ist nicht eingerichtet.")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("profile_id") != version or not profile.get("services") or not profile.get("icp"):
        raise ValueError("Das ATP-Profil ist unvollständig oder falsch versioniert.")
    return profile


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(
        json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
