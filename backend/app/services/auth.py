"""RBAC PoC — roles sesuai panitia: operator, analis, pimpinan, admin."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated

import bcrypt
from fastapi import Depends, Header, HTTPException, Request

from app.core.db import db, utcnow

BCRYPT_MARKER = "bcrypt"
_LOGIN_WINDOW_S = 60.0
_LOGIN_MAX_ATTEMPTS = 8
_login_hits: dict[str, list[float]] = defaultdict(list)


class Role(str, Enum):
    OPERATOR = "operator"
    ANALIS = "analis"
    PIMPINAN = "pimpinan"
    ADMIN = "admin"


# Matriks izin PoC
PERMISSIONS: dict[Role, set[str]] = {
    Role.OPERATOR: {
        "health",
        "devices",
        "sessions:start",
        "sessions:read",
        "sessions:cancel",
        "findings:read",
        "report:read",
    },
    Role.ANALIS: {
        "health",
        "devices",
        "sessions:read",
        "findings:read",
        "findings:review",
        "dashboard",
        "report:read",
    },
    Role.PIMPINAN: {
        "health",
        "sessions:read",
        "findings:read",
        "dashboard",
        "report:read",
        "report:authorize",
    },
    Role.ADMIN: {
        "health",
        "devices",
        "sessions:start",
        "sessions:read",
        "sessions:cancel",
        "findings:read",
        "findings:review",
        "dashboard",
        "report:read",
        "report:authorize",
        "users:manage",
    },
}


def _seed_password(env_key: str, default: str) -> str:
    return os.environ.get(env_key, default)


# Seed akun lab — override via SADT_SEED_*_PASSWORD (ganti di produksi)
SEED_USERS = [
    (
        "operator",
        lambda: _seed_password("SADT_SEED_OPERATOR_PASSWORD", "Ops@2026"),
        Role.OPERATOR,
        "Operator Akuisisi",
    ),
    (
        "analis",
        lambda: _seed_password("SADT_SEED_ANALIS_PASSWORD", "Analis@2026"),
        Role.ANALIS,
        "Analis Forensik",
    ),
    (
        "pimpinan",
        lambda: _seed_password("SADT_SEED_PIMPINAN_PASSWORD", "Pimpinan@2026"),
        Role.PIMPINAN,
        "Pimpinan Panitia",
    ),
    (
        "admin",
        lambda: _seed_password("SADT_SEED_ADMIN_PASSWORD", "Admin@2026"),
        Role.ADMIN,
        "Administrator Sistem",
    ),
]


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash dengan bcrypt. Argumen `salt` diabaikan (kompatibilitas signature lama)."""
    del salt
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")
    return digest, BCRYPT_MARKER


def _legacy_sha256(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    if salt == BCRYPT_MARKER or password_hash.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
        except (ValueError, TypeError):
            return False
    return secrets.compare_digest(_legacy_sha256(password, salt), password_hash)


def _check_login_rate(request: Request | None, username: str) -> None:
    """Catat percobaan gagal; raise 429 jika melebihi ambang."""
    if request is None:
        return
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{username.strip().lower()}"
    now = time.monotonic()
    window = [t for t in _login_hits[key] if now - t < _LOGIN_WINDOW_S]
    window.append(now)
    _login_hits[key] = window
    if len(window) > _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Terlalu banyak percobaan login. Coba lagi sebentar.",
        )


def reset_login_rate_limits() -> None:
    """Test helper."""
    _login_hits.clear()


@dataclass
class AuthUser:
    id: str
    username: str
    role: Role
    display_name: str
    token: str | None = None

    def can(self, permission: str) -> bool:
        perms = PERMISSIONS.get(self.role, set())
        return permission in perms or self.role == Role.ADMIN

    def require(self, permission: str) -> None:
        if not self.can(permission):
            raise HTTPException(
                status_code=403,
                detail=f"Akses ditolak: peran '{self.role.value}' tidak punya izin '{permission}'",
            )


AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_tokens (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id);
"""


async def ensure_auth_schema() -> None:
    await db.conn.executescript(AUTH_SCHEMA)
    await db.conn.commit()
    row = await db.fetchone("SELECT COUNT(*) AS c FROM users")
    if row and row["c"] > 0:
        return
    now = utcnow()
    for username, password_fn, role, display in SEED_USERS:
        pw_hash, salt = hash_password(password_fn())
        await db.execute(
            """
            INSERT INTO users (id, username, password_hash, salt, role, display_name, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (str(uuid.uuid4()), username, pw_hash, salt, role.value, display, now),
        )


async def _upgrade_hash_if_legacy(user_id: str, password: str, salt: str) -> None:
    if salt == BCRYPT_MARKER:
        return
    pw_hash, new_salt = hash_password(password)
    await db.execute(
        "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
        (pw_hash, new_salt, user_id),
    )


async def login(
    username: str,
    password: str,
    request: Request | None = None,
) -> AuthUser:
    row = await db.fetchone(
        "SELECT * FROM users WHERE username = ? AND active = 1",
        (username.strip().lower(),),
    )
    if not row or not verify_password(password, row["password_hash"], row["salt"]):
        _check_login_rate(request, username)
        raise HTTPException(status_code=401, detail="Username atau password salah")

    await _upgrade_hash_if_legacy(row["id"], password, row["salt"])

    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    await db.execute(
        "INSERT INTO auth_tokens (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, row["id"], expires, utcnow()),
    )
    return AuthUser(
        id=row["id"],
        username=row["username"],
        role=Role(row["role"]),
        display_name=row["display_name"],
        token=token,
    )


async def logout(token: str) -> None:
    await db.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))


async def user_from_token(token: str | None) -> AuthUser | None:
    if not token:
        return None
    raw = token.removeprefix("Bearer ").strip()
    if not raw:
        return None
    row = await db.fetchone(
        """
        SELECT u.*, t.expires_at, t.token
        FROM auth_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token = ? AND u.active = 1
        """,
        (raw,),
    )
    if not row:
        return None
    try:
        exp = datetime.fromisoformat(row["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            await db.execute("DELETE FROM auth_tokens WHERE token = ?", (raw,))
            return None
    except ValueError:
        return None
    return AuthUser(
        id=row["id"],
        username=row["username"],
        role=Role(row["role"]),
        display_name=row["display_name"],
        token=raw,
    )


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    user = await user_from_token(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Autentikasi diperlukan")
    return user


def require_perm(permission: str):
    async def _dep(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        user.require(permission)
        return user

    return _dep


async def list_users_safe() -> list[dict]:
    rows = await db.fetchall(
        "SELECT id, username, role, display_name, active, created_at FROM users ORDER BY role, username"
    )
    return [dict(r) for r in rows]
