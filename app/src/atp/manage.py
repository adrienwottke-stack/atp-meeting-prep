"""Explicit, authenticated administration; never inferred from organisation membership."""

import argparse
import asyncio
import json
from getpass import getpass
from pathlib import Path
from uuid import UUID

from psycopg.types.json import Jsonb

from .auth import Auth
from .config import Settings
from .profiles import load_profile, profile_hash
from .repository import Repository


def active_profile(repo, actor):
    with repo._connection(actor) as db:
        row = db.execute(
            "select content from atp.profile_versions where org_id=%s and active", (actor.org_id,)
        ).fetchone()
        if not row:
            raise ValueError("PROFILE_NOT_ACTIVE")
        return row["content"]


async def execute(args):
    settings = Settings()
    repo = Repository(settings)
    auth = Auth(settings, repo)
    password = getpass("Passwort: ")
    session = await auth.login(args.email, password)
    user = await auth.authenticate(session["access_token"])
    if user.role != "admin":
        raise SystemExit("Dieser Befehl benötigt eine serverseitig bestätigte Administratorrolle.")
    if args.command == "profile-activate":
        profile = load_profile(args.version)
        digest = profile_hash(profile)
        with repo._connection(worker=True) as db:
            # Recheck the role immediately before privileged writes.
            role = db.execute(
                "select role from atp.memberships where user_id=%s and org_id=%s and active for share",
                (user.user_id, user.org_id),
            ).fetchone()
            if not role or role["role"] != "admin":
                raise ValueError("FORBIDDEN")
            db.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", ("profile:" + user.org_id,))
            existing = db.execute(
                "select content_hash from atp.profile_versions where org_id=%s and version=%s",
                (user.org_id, args.version),
            ).fetchone()
            if existing and existing["content_hash"] != digest:
                raise SystemExit(
                    "Eine bestehende Version darf nicht verändert werden. Neue profile_id vergeben."
                )
            db.execute(
                "update atp.profile_versions set active=false where org_id=%s and active", (user.org_id,)
            )
            db.execute(
                "insert into atp.profile_versions(org_id,version,content_hash,content,active,activated_by) "
                "values(%s,%s,%s,%s,true,%s) on conflict(org_id,version) do update set active=true,activated_by=excluded.activated_by",
                (user.org_id, args.version, digest, Jsonb(profile), user.user_id),
            )
            db.execute(
                "insert into atp.profile_activations(org_id,version,content_hash,activated_by,reason) values(%s,%s,%s,%s,%s)",
                (user.org_id, args.version, digest, user.user_id, args.reason),
            )
            # Activation history is append-only in a local operational record, without credentials.
        output = Path(settings.output_dir) / "profile-aktivierungen.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        from .models import utcnow

        with output.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "version": args.version,
                        "hash": digest,
                        "org_id": user.org_id,
                        "activated_by": user.user_id,
                        "activated_at": utcnow().isoformat(),
                        "reason": args.reason,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print(f"Profil {args.version} bewusst aktiviert. Vorhandene Läufe behalten ihren Snapshot.")
    elif args.command in ("share", "unshare"):
        UUID(args.meeting_id)
        UUID(args.user_id)
        with repo._connection(user) as db:
            meeting = db.execute(
                "select owner_id from atp.meetings where id=%s", (args.meeting_id,)
            ).fetchone()
            if not meeting or str(meeting["owner_id"]) != user.user_id:
                raise SystemExit("Nur eigene Meetings können mit diesem Befehl freigegeben werden.")
            if args.command == "unshare":
                db.execute(
                    "delete from atp.shares where meeting_id=%s and user_id=%s",
                    (args.meeting_id, args.user_id),
                )
                print("Meetingfreigabe widerrufen.")
                return
            with repo._connection(worker=True) as privileged:
                target = privileged.execute(
                    "select user_id from atp.memberships where org_id=%s and user_id=%s and active",
                    (user.org_id, args.user_id),
                ).fetchone()
            if not target:
                raise SystemExit("Der Empfänger ist kein aktives Mitglied dieser Organisation.")
            db.execute(
                "insert into atp.shares(meeting_id,user_id,permission,granted_by) values(%s,%s,%s,%s) "
                "on conflict(meeting_id,user_id) do update set permission=excluded.permission,granted_by=excluded.granted_by",
                (args.meeting_id, args.user_id, args.permission, user.user_id),
            )
        print("Ausdrückliche Meetingfreigabe gespeichert.")


def main():
    parser = argparse.ArgumentParser(description="ATP rollenbeschränkte Verwaltung")
    sub = parser.add_subparsers(dest="command", required=True)
    profile = sub.add_parser("profile-activate")
    profile.add_argument("--email", required=True)
    profile.add_argument("--version", required=True)
    profile.add_argument("--reason", required=True)
    share = sub.add_parser("share")
    share.add_argument("--email", required=True)
    share.add_argument("--meeting-id", required=True)
    share.add_argument("--user-id", required=True)
    share.add_argument("--permission", choices=["read", "edit"], default="read")
    unshare = sub.add_parser("unshare")
    unshare.add_argument("--email", required=True)
    unshare.add_argument("--meeting-id", required=True)
    unshare.add_argument("--user-id", required=True)
    try:
        asyncio.run(execute(parser.parse_args()))
    except (ValueError, OSError):
        raise SystemExit(
            "Verwaltungsvorgang nicht möglich. Rollen, Objektzuordnung und Konfiguration prüfen."
        ) from None


if __name__ == "__main__":
    main()
