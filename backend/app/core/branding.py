"""SATRIA product branding helpers — dual-compat with legacy SADT/SIKSIK wire formats."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field

CANONICAL_CRAWL_RECORD_MIME = "application/vnd.satria.crawl-record+json"
LEGACY_CRAWL_RECORD_MIME = "application/vnd.siksik.crawl-record+json"
# Agent on main still emits application/vnd.siksik.crawl-record+json
MAIN_AGENT_CRAWL_RECORD_MIME = "application/vnd.siksik.crawl-record+json"
CRAWL_RECORD_MIMES = frozenset(
    {
        CANONICAL_CRAWL_RECORD_MIME,
        LEGACY_CRAWL_RECORD_MIME,
        MAIN_AGENT_CRAWL_RECORD_MIME,
    }
)

PRODUCT_NAME = "SATRIA"
PRODUCT_FULL_NAME = "Sistem Analisis Terpadu Resiko & Integritas Aparatur"
PRODUCT_TAGLINE = "Deteksi Dini — Analisis Mendalam — Keputusan Akurat"

SESSION_ID_ALIASES = AliasChoices("satria_session_id", "siksik_session_id")


def session_id_field(**kwargs: Any) -> Any:
    """Accept satria_session_id or siksik_session_id; serialize as siksik_session_id."""
    return Field(
        validation_alias=SESSION_ID_ALIASES,
        serialization_alias="siksik_session_id",
        **kwargs,
    )


def is_crawl_record_mime(mime: str | None) -> bool:
    return (mime or "").strip().lower() in CRAWL_RECORD_MIMES


def crawl_record_filename_mime(name: str) -> str | None:
    lower = name.lower()
    if lower.endswith(".satria-record.json"):
        return CANONICAL_CRAWL_RECORD_MIME
    if lower.endswith(".siksik-record.json"):
        return LEGACY_CRAWL_RECORD_MIME
    return None


def _parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def promote_satria_env(*, root: Path | None = None) -> None:
    """Load SATRIA_* then copy onto SADT_* (SATRIA wins). SADT_*-only labs still work."""
    candidates: list[Path] = []
    if root is not None:
        candidates.append(root / ".env")
    candidates.append(Path.cwd() / ".env")
    backend_root = Path(__file__).resolve().parents[2]
    candidates.append(backend_root / ".env")
    candidates.append(backend_root.parent / ".env")

    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        for key, value in _parse_dotenv(path).items():
            if key.startswith("SATRIA_") and key not in os.environ:
                os.environ[key] = value

    for key, value in list(os.environ.items()):
        if not key.startswith("SATRIA_"):
            continue
        os.environ["SADT_" + key.removeprefix("SATRIA_")] = value
