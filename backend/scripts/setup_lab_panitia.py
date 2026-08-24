#!/usr/bin/env python3
"""Setup sekali jalan: env lab panitia + password acak + rotate DB.

Menulis:
  backend/.env
  backend/data/lab-panitia-credentials.txt  (gitignored)
  deploy/env/docker-panitia.generated.env   (gitignored)

Usage:
  cd backend && python scripts/setup_lab_panitia.py
  cd backend && python scripts/setup_lab_panitia.py --cors-extra https://10.0.0.5:8443
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
PRESET = ROOT / "env" / "lab.panitia.env"
TARGET_ENV = ROOT / ".env"
CREDENTIALS = ROOT / "data" / "lab-panitia-credentials.txt"
DOCKER_ENV = REPO / "deploy" / "env" / "docker-panitia.generated.env"

ROLES = ("operator", "analis", "pimpinan", "admin")
ENV_KEYS = {
    "operator": "SADT_SEED_OPERATOR_PASSWORD",
    "analis": "SADT_SEED_ANALIS_PASSWORD",
    "pimpinan": "SADT_SEED_PIMPINAN_PASSWORD",
    "admin": "SADT_SEED_ADMIN_PASSWORD",
}


def generate_password() -> str:
    # URL-safe, ~128-bit; memenuhi rotate script min 8 char
    return secrets.token_urlsafe(18)


def merge_cors(base_text: str, extra_cors: list[str]) -> str:
    if not extra_cors:
        return base_text
    lines: list[str] = []
    for line in base_text.splitlines():
        if line.startswith("SADT_CORS_ORIGINS="):
            origins = [part.strip() for part in line.split("=", 1)[1].split(",") if part.strip()]
            for origin in extra_cors:
                if origin and origin not in origins:
                    origins.append(origin)
            lines.append("SADT_CORS_ORIGINS=" + ",".join(origins))
        else:
            lines.append(line)
    return "\n".join(lines)


def build_env_text(extra_cors: list[str]) -> tuple[str, dict[str, str]]:
    if not PRESET.is_file():
        raise FileNotFoundError(f"preset missing: {PRESET}")
    base = merge_cors(PRESET.read_text(encoding="utf-8").rstrip(), extra_cors)
    passwords = {role: generate_password() for role in ROLES}
    secret_lines = ["", "# --- Seed passwords (generated) ---"]
    for role in ROLES:
        secret_lines.append(f"{ENV_KEYS[role]}={passwords[role]}")
    secret_lines.append("")
    secret_lines.append(f"# Generated {datetime.now(timezone.utc).isoformat()}")
    return base + "\n".join(secret_lines) + "\n", passwords


def write_credentials(passwords: dict[str, str]) -> None:
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# SATRIA lab panitia — simpan aman, jangan commit",
        f"# Generated {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for role in ROLES:
        lines.append(f"{role}: {passwords[role]}")
    CREDENTIALS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(CREDENTIALS, 0o600)


def write_docker_env(env_text: str) -> None:
    DOCKER_ENV.parent.mkdir(parents=True, exist_ok=True)
    # Subset untuk container API (tanpa path host-only)
    keep_prefixes = (
        "SADT_LAB_DEMO_MODE",
        "SADT_E2E_SIMULATION",
        "SADT_RUNTIME_ENV",
        "SADT_CORS_ORIGINS",
        "SADT_ZIP_",
        "SADT_SEED_",
        "SADT_OCR_",
        "SADT_WORKER_",
    )
    lines = [
        "# Auto-generated — jangan edit manual",
        "SADT_RUNTIME_ENV=docker",
        "SADT_API_HOST=0.0.0.0",
        "",
    ]
    for raw in env_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        if key == "SADT_RUNTIME_ENV":
            continue
        if any(key.startswith(p) for p in keep_prefixes):
            lines.append(line)
    DOCKER_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(DOCKER_ENV, 0o600)


async def rotate_passwords() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for role, key in ENV_KEYS.items():
        val = os.environ.get(key)
        if val:
            os.environ[key] = val
    from app.core.db import db
    from app.services.auth import ensure_auth_schema, hash_password

    await db.connect()
    await ensure_auth_schema()
    try:
        for role in ROLES:
            password = os.environ[ENV_KEYS[role]]
            pw_hash, salt = hash_password(password)
            row = await db.fetchone("SELECT id FROM users WHERE username = ?", (role,))
            if row:
                await db.execute(
                    "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                    (pw_hash, salt, row["id"]),
                )
            else:
                import uuid
                from app.services.auth import Role

                role_enum = Role(role)
                await db.execute(
                    """
                    INSERT INTO users (id, username, password_hash, salt, role, display_name, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))
                    """,
                    (str(uuid.uuid4()), role, pw_hash, salt, role_enum.value, role.title()),
                )
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup lab panitia env + passwords")
    parser.add_argument(
        "--cors-extra",
        action="append",
        default=[],
        help="Tambahan origin CORS (mis. https://10.0.0.5:8443)",
    )
    parser.add_argument("--skip-rotate", action="store_true", help="Hanya tulis .env, tanpa update DB")
    args = parser.parse_args()

    env_text, passwords = build_env_text(args.cors_extra)

    if TARGET_ENV.exists():
        bak = TARGET_ENV.with_suffix(".env.bak")
        shutil.copy2(TARGET_ENV, bak)
        print(f"Backup: {bak}")

    TARGET_ENV.write_text(env_text, encoding="utf-8")
    os.chmod(TARGET_ENV, 0o600)
    print(f"OK → {TARGET_ENV.relative_to(ROOT)}")

    write_credentials(passwords)
    print(f"OK → {CREDENTIALS.relative_to(ROOT)} (chmod 600)")

    write_docker_env(env_text)
    print(f"OK → {DOCKER_ENV.relative_to(REPO)}")

    if not args.skip_rotate:
        for role in ROLES:
            os.environ[ENV_KEYS[role]] = passwords[role]
        asyncio.run(rotate_passwords())
        print("OK → password di DB di-rotate")

    print("\nAkun lab panitia (simpan file credentials):")
    for role in ROLES:
        print(f"  {role:10} / {passwords[role]}")
    print("\nRestart API. Docker: docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
