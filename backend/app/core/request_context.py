from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_request_id: ContextVar[str | None] = ContextVar("siksik_request_id", default=None)


def normalize_request_id(value: str | None) -> str:
    if value and SAFE_REQUEST_ID.fullmatch(value):
        return value
    return str(uuid.uuid4())


def bind_request_id(value: str) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()

