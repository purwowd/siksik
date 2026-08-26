from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import re
from pathlib import PurePosixPath
from typing import Sequence

from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error

STORAGE_UUID = re.compile(r"^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$")
SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,12}$")
RECOVERY_ROOT = "recovered_trash"
RECOVERY_BUCKETS = frozenset({"trash", "previews"})
CACHE_RECOVERY_SOURCES = frozenset({"gallery_cache", "classic_thumbnail", "thumbdata"})
RECOVERY_CACHE_SOURCE = "recovered_cache"
RECOVERY_TRASH_SOURCE = "recovered_trash"


def canonical_shared_path(value: str) -> str:
    for prefix in ("/storage/emulated/0", "/storage/self/primary"):
        if value == prefix:
            return "/sdcard"
        if value.startswith(f"{prefix}/"):
            return f"/sdcard/{value[len(prefix) + 1:]}"
    return value


def device_shared_path(value: str) -> str:
    if value == "/sdcard":
        return "/storage/emulated/0"
    if value.startswith("/sdcard/"):
        return f"/storage/emulated/0/{value[len('/sdcard/'):]}"
    return value


def validate_shared_path(value: str, roots: Sequence[str]) -> str:
    if not value.startswith("/") or "\x00" in value or len(value) > 4096:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Path recovery Android tidak valid.")
    normalized = posixpath.normpath(canonical_shared_path(value))
    if any(
        normalized == posixpath.normpath(root)
        or normalized.startswith(f"{posixpath.normpath(root)}/")
        for root in roots
    ):
        return normalized
    raise acquisition_error(
        ErrorCategory.VALIDATION_ERROR,
        "Path recovery berada di luar shared storage.",
    )


def recovery_file_source(recovery_source: str | None) -> str:
    value = getattr(recovery_source, "value", recovery_source) or ""
    if value in CACHE_RECOVERY_SOURCES:
        return RECOVERY_CACHE_SOURCE
    return RECOVERY_TRASH_SOURCE


def stable_candidate_id(source: str, identity: str) -> str:
    return hashlib.sha256(f"siksik-recovery:{source}:{identity}".encode("utf-8", "replace")).hexdigest()[:32]


def safe_extension(display_name: str, mime_type: str | None) -> str:
    suffix = PurePosixPath(display_name).suffix.lower()
    if SAFE_EXTENSION.fullmatch(suffix):
        return suffix
    guessed = mimetypes.guess_extension(mime_type or "") or ".bin"
    return guessed if SAFE_EXTENSION.fullmatch(guessed) else ".bin"


def normalized_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Path manifest recovery tidak valid.",
        )
    return path.as_posix()


def recovery_relative_path(value: str) -> str:
    normalized = normalized_relative_path(value)
    parts = PurePosixPath(normalized).parts
    if len(parts) != 3 or parts[0] != RECOVERY_ROOT or parts[1] not in RECOVERY_BUCKETS:
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "Path artifact recovery tidak berada di direktori milik recovery.",
        )
    return normalized


def is_recovery_owned_path(value: str) -> bool:
    try:
        recovery_relative_path(value)
    except AcquisitionError:
        return False
    return True


def is_recovery_namespace_path(value: str) -> bool:
    try:
        normalized = normalized_relative_path(value)
    except AcquisitionError:
        return False
    parts = PurePosixPath(normalized).parts
    return len(parts) >= 2 and parts[0] == RECOVERY_ROOT and parts[1] in RECOVERY_BUCKETS
