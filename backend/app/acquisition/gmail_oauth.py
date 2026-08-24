from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Protocol

from app.acquisition.errors import ErrorCategory, acquisition_error
from app.core.config import settings
from app.core.db import db
from app.models.schemas import SessionStatus

logger = logging.getLogger("siksik.acquisition.gmail_oauth")

GOOGLE_ACCOUNT_DUMPSYS_PATTERN = re.compile(
    r"Account \{name=([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}), type=com\.google\}"
)


class GoogleAccountClient(Protocol):
    async def list_google_accounts(
        self,
        session_id: str,
        *,
        request_id: str | None = None,
    ) -> list[Any]: ...

    async def get_google_auth_token(
        self,
        session_id: str,
        account_name: str,
        *,
        scope: str | None = None,
        request_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str | None: ...


async def session_acquisition_reference(session_id: str) -> datetime | None:
    row = await db.fetchone("SELECT created_at FROM sessions WHERE id = ?", (session_id,))
    if row is None:
        return None
    value = row["created_at"]
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def resolve_google_account_name(
    client: GoogleAccountClient,
    session_id: str,
    *,
    serial: str | None = None,
    adb: Any | None = None,
    request_id: str | None = None,
) -> str | None:
    accounts = await client.list_google_accounts(session_id, request_id=request_id)
    if accounts:
        return accounts[0].name

    if serial and adb is not None:
        dumpsys_res = await adb.run(
            serial,
            ["shell", "dumpsys", "account"],
            operation="dumpsys_account_probe",
            timeout=5.0,
            check=False,
        )
        if dumpsys_res.returncode == 0:
            match = GOOGLE_ACCOUNT_DUMPSYS_PATTERN.search(dumpsys_res.stdout)
            if match:
                return match.group(1)
    return None


async def peek_gmail_oauth_token(
    client: GoogleAccountClient,
    session_id: str,
    account_name: str,
    *,
    request_id: str | None = None,
) -> str | None:
    """Return a cached Gmail token without opening the consent UI."""
    try:
        token = await client.get_google_auth_token(
            session_id,
            account_name,
            scope=settings.resolved_gmail_scope,
            request_id=request_id,
            timeout_seconds=min(settings.android_agent_request_timeout_s, 20.0),
        )
        if token:
            return token
        if settings.resolved_gmail_scope != settings.gmail_scope:
            return await client.get_google_auth_token(
                session_id,
                account_name,
                scope=settings.gmail_scope,
                request_id=request_id,
                timeout_seconds=min(settings.android_agent_request_timeout_s, 20.0),
            )
    except Exception as exc:
        logger.debug("gmail_oauth_peek_failed", extra={"error": str(exc)})
    return None


async def fetch_gmail_oauth_token(
    client: GoogleAccountClient,
    session_id: str,
    account_name: str,
    *,
    request_id: str | None = None,
    on_progress: Any | None = None,
    attempts: int | None = None,
) -> str | None:
    total_attempts = attempts or settings.gmail_oauth_attempts
    oauth_timeout = settings.gmail_oauth_request_timeout_s
    for attempt in range(1, max(total_attempts, 1) + 1):
        if on_progress is not None:
            message = (
                f"Menunggu otorisasi Gmail di perangkat ({account_name}) "
                f"— percobaan {attempt}/{total_attempts}. "
                "Selesaikan dialog Google di layar HP."
            )
            await on_progress(
                SessionStatus.AWAITING_ACCESS,
                52.0 + min(attempt - 1, 4),
                message,
                acquisition_method="gmail_api",
                gmail_account=account_name,
            )
        token = await client.get_google_auth_token(
            session_id,
            account_name,
            scope=settings.resolved_gmail_scope,
            request_id=request_id,
            timeout_seconds=oauth_timeout,
        )
        if token:
            return token
        if settings.resolved_gmail_scope != settings.gmail_scope:
            token = await client.get_google_auth_token(
                session_id,
                account_name,
                scope=settings.gmail_scope,
                request_id=request_id,
                timeout_seconds=oauth_timeout,
            )
            if token:
                return token
        if attempt < total_attempts:
            await asyncio.sleep(3.0)

    logger.error(
        "gmail_oauth_token_unavailable",
        extra={
            "session_id": session_id,
            "account_name": account_name,
            "attempts": total_attempts,
        },
    )
    return None


async def ensure_gmail_oauth(
    *,
    client: GoogleAccountClient,
    session_id: str,
    serial: str | None,
    adb: Any | None,
    on_progress: Any,
    request_id: str | None = None,
    existing_account: str | None = None,
    existing_token: str | None = None,
) -> tuple[str, str]:
    if not settings.gmail_client_id.strip():
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Gmail OAuth belum dikonfigurasi (SADT_GMAIL_CLIENT_ID).",
        )

    if existing_token and existing_account:
        return existing_account, existing_token

    account_name = existing_account or await resolve_google_account_name(
        client,
        session_id,
        serial=serial,
        adb=adb,
        request_id=request_id,
    )
    if not account_name:
        raise acquisition_error(
            ErrorCategory.AGENT_UNAVAILABLE,
            "Tidak ada akun Google di perangkat untuk akuisisi Gmail.",
        )

    cached = await peek_gmail_oauth_token(
        client,
        session_id,
        account_name,
        request_id=request_id,
    )
    if cached:
        await on_progress(
            SessionStatus.ACQUIRING,
            54.0,
            f"Gmail siap ({account_name}) — mengunduh email…",
            acquisition_method="gmail_api",
            gmail_account=account_name,
        )
        return account_name, cached

    token = await fetch_gmail_oauth_token(
        client,
        session_id,
        account_name,
        request_id=request_id,
        on_progress=on_progress,
    )
    if not token:
        raise acquisition_error(
            ErrorCategory.AWAITING_USER,
            (
                f"Otorisasi Gmail belum selesai untuk {account_name}. "
                "Selesaikan dialog Google di layar perangkat, lalu mulai sesi baru."
            ),
            retryable=True,
        )

    await on_progress(
        SessionStatus.ACQUIRING,
        54.0,
        f"Gmail terotorisasi ({account_name}) — mengunduh email…",
        acquisition_method="gmail_api",
        gmail_account=account_name,
    )
    return account_name, token
