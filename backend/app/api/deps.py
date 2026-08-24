"""Shared API dependencies and query helpers."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from app.core.branding import CRAWL_RECORD_MIMES
from app.core.config import settings
from app.core.db import db
from app.models.schemas import AcquisitionMode, FindingOut, PaginatedFindings
from app.services.auth import PERMISSIONS, AuthUser

MAX_FINDING_PREVIEW_CHARS = 320

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".heif",
    ".mp4", ".mov", ".webm", ".mkv", ".3gp", ".avi", ".m4v",
    ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".amr",
    ".html", ".htm", ".json", ".eml", ".msg", ".txt", ".csv", ".xml", ".log",
    ".vcf", ".vcard", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odt", ".rtf", ".pages", ".numbers", ".key",
}

MEDIA_APPLICATION_MIMES = {
    "application/json", "application/pdf", "application/rtf", "application/msword",
    "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.apple.pages", "application/vnd.apple.numbers",
    "application/vnd.apple.keynote",
    *sorted(CRAWL_RECORD_MIMES),
}

FINDING_DEDUP_PREDICATE = """
AND f.id IN (
  SELECT ranked.id FROM (
    SELECT
      f2.id AS id,
      ROW_NUMBER() OVER (
        PARTITION BY COALESCE(NULLIF(fi2.sha256, ''), f2.file_id), f2.label
        ORDER BY f2.confidence DESC, f2.created_at ASC, f2.id ASC
      ) AS rn
    FROM findings f2
    LEFT JOIN files fi2 ON fi2.id = f2.file_id
    WHERE f2.session_id = f.session_id
  ) ranked
  WHERE ranked.rn = 1
)
"""


def pages(total: int, page_size: int) -> int:
    if total <= 0:
        return 1
    return max(1, (total + page_size - 1) // page_size)


def clamp_page(page: int, pages_total: int) -> int:
    return min(max(1, page), pages_total)


def gpu_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def counts(rows: list, key: str) -> list:
    from app.models.schemas import NamedCount

    bucket: dict[str, int] = {}
    for r in rows:
        name = r[key] if isinstance(r, dict) else r[key]
        bucket[name] = bucket.get(name, 0) + 1
    return [NamedCount(name=k, count=v) for k, v in sorted(bucket.items(), key=lambda x: -x[1])]


def perms(user: AuthUser) -> list[str]:
    return sorted(PERMISSIONS.get(user.role, set()))


async def paginate_findings(
    *,
    where_sql: str,
    params: tuple,
    order_sql: str,
    page: int,
    page_size: int,
) -> PaginatedFindings:
    total_row = await db.fetchone(
        f"SELECT COUNT(*) AS c FROM findings f {where_sql} {FINDING_DEDUP_PREDICATE}",
        params,
    )
    total = int(total_row["c"]) if total_row else 0
    pages_total = pages(total, page_size)
    page = clamp_page(page, pages_total)
    offset = (page - 1) * page_size
    rows = await db.fetchall(
        f"""
        SELECT
            f.*,
            CASE
                WHEN fi.mime LIKE 'image/%' OR fi.mime LIKE 'video/%' THEN f.path
                ELSE (
                    SELECT ca.relative_path
                    FROM crawl_artifacts ca
                    WHERE ca.session_id = f.session_id
                      AND ca.record_id = CASE
                          WHEN json_valid(fi.meta_json)
                          THEN json_extract(fi.meta_json, '$.crawl_record_id')
                          ELSE NULL
                      END
                      AND ca.verified = 1
                      AND ca.role IN ('source_binary', 'screenshot')
                      AND (ca.mime_type LIKE 'image/%' OR ca.mime_type LIKE 'video/%')
                    ORDER BY CASE ca.role WHEN 'source_binary' THEN 0 ELSE 1 END,
                             ca.relative_path
                    LIMIT 1
                )
            END AS resolved_preview_path,
            (
                SELECT cr.normalized_text
                FROM crawl_records cr
                WHERE cr.session_id = f.session_id
                  AND cr.record_id = CASE
                      WHEN json_valid(fi.meta_json)
                      THEN json_extract(fi.meta_json, '$.crawl_record_id')
                      ELSE NULL
                  END
                LIMIT 1
            ) AS normalized_preview_text
        FROM findings f
        LEFT JOIN files fi ON fi.id = f.file_id
        {where_sql} {FINDING_DEDUP_PREDICATE} {order_sql} LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    )
    items: list[FindingOut] = []
    for row in rows:
        payload = dict(row)
        preview_path = payload.pop("resolved_preview_path", None)
        normalized_text = payload.pop("normalized_preview_text", None)
        preview_source = normalized_text or payload.get("evidence") or ""
        preview_text = " ".join(str(preview_source).replace("\x00", " ").split())[
            :MAX_FINDING_PREVIEW_CHARS
        ]
        payload["preview_path"] = preview_path
        payload["preview_text"] = preview_text or None
        items.append(FindingOut.model_validate(payload))
    return PaginatedFindings(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        pages=pages_total,
    )


async def session_mode(session_id: str) -> AcquisitionMode:
    row = await db.fetchone("SELECT mode FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return AcquisitionMode(str(row["mode"]))


async def resolve_session_media(session_id: str, path: str) -> tuple[str, Path, str | None]:
    row = await db.fetchone("SELECT id FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    rel = path.replace("\\", "/").lstrip("/")
    if ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="Invalid path")
    staging = (settings.staging_dir / session_id).resolve()
    target = (staging / rel).resolve()
    try:
        target.relative_to(staging)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path di luar staging") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    file_row = await db.fetchone(
        "SELECT mime FROM files WHERE session_id = ? AND path = ? AND pull_status = 'pulled' LIMIT 1",
        (session_id, rel),
    )
    indexed_mime = str(file_row["mime"] or "").casefold() if file_row else ""
    mime_allowed = indexed_mime.startswith(("image/", "video/", "audio/", "text/")) or (
        indexed_mime in MEDIA_APPLICATION_MIMES
    )
    if target.suffix.lower() not in MEDIA_EXTENSIONS and not mime_allowed:
        raise HTTPException(status_code=415, detail="Tipe media tidak didukung preview")
    return rel, target, indexed_mime or None
