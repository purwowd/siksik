#!/usr/bin/env python3
"""Rotate SATRIA lab user passwords (interactive or from env).

Reads SATRIA_SEED_* / SADT_SEED_* for each role when --from-env is set.
Otherwise prompts securely per user.

Usage:
  cd backend && python scripts/rotate_lab_passwords.py --from-env
  cd backend && python scripts/rotate_lab_passwords.py --user admin
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import db  # noqa: E402
from app.services.auth import Role, ensure_auth_schema, hash_password  # noqa: E402

ENV_KEYS: dict[str, tuple[Role, str]] = {
    "operator": (Role.OPERATOR, "SATRIA_SEED_OPERATOR_PASSWORD"),
    "analis": (Role.ANALIS, "SATRIA_SEED_ANALIS_PASSWORD"),
    "pimpinan": (Role.PIMPINAN, "SATRIA_SEED_PIMPINAN_PASSWORD"),
    "admin": (Role.ADMIN, "SATRIA_SEED_ADMIN_PASSWORD"),
}

LEGACY_PREFIX = "SADT_SEED_"


def resolve_env_password(key: str) -> str | None:
    satria = os.environ.get(key)
    if satria:
        return satria
    legacy = os.environ.get(key.replace("SATRIA_", LEGACY_PREFIX))
    return legacy


async def rotate_user(username: str, password: str) -> None:
    pw_hash, salt = hash_password(password)
    row = await db.fetchone("SELECT id FROM users WHERE username = ?", (username,))
    if row:
        await db.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (pw_hash, salt, row["id"]),
        )
        print(f"updated {username}")
        return
    role, _ = ENV_KEYS[username]
    await db.execute(
        """
        INSERT INTO users (id, username, password_hash, salt, role, display_name, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))
        """,
        (str(uuid.uuid4()), username, pw_hash, salt, role.value, username.title(),),
    )
    print(f"created {username}")


async def main_async(args: argparse.Namespace) -> int:
    await db.connect()
    await ensure_auth_schema()
    try:
        targets = [args.user] if args.user else list(ENV_KEYS)
        for username in targets:
            if username not in ENV_KEYS:
                print(f"unknown user: {username}", file=sys.stderr)
                return 1
            if args.from_env:
                _, env_key = ENV_KEYS[username]
                password = resolve_env_password(env_key)
                if not password:
                    print(f"missing env {env_key} for {username}", file=sys.stderr)
                    return 1
            else:
                password = getpass.getpass(f"New password for {username}: ")
                confirm = getpass.getpass("Confirm: ")
                if password != confirm or len(password) < 8:
                    print("password mismatch or too short (min 8)", file=sys.stderr)
                    return 1
            await rotate_user(username, password)
    finally:
        await db.close()
    print("rotate_ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate SATRIA lab passwords")
    parser.add_argument("--from-env", action="store_true", help="Use SATRIA_SEED_* env vars")
    parser.add_argument("--user", choices=sorted(ENV_KEYS), help="Single user only")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
