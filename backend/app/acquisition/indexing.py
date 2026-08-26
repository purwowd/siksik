from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.file_identity import stable_file_id
from app.acquisition.media_types import (
    _is_junk_media_path,
    guess_mime,
    is_agent_self_capture,
)
from app.core.branding import is_crawl_record_mime
from app.core.config import settings
from app.core.db import db
from app.models.schemas import SessionStatus

logger = logging.getLogger("siksik.acquisition.indexing")

async def hash_file(path: Path) -> str:
    def _hash() -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(settings.hash_chunk_bytes)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    return await asyncio.to_thread(_hash)


async def index_staging(session_id: str, staging: Path, on_progress) -> tuple[int, float]:
    t0 = time.perf_counter()
    files: list[tuple] = []
    from app.acquisition.android_recovery.paths import is_recovery_namespace_path
    from app.acquisition.android_recovery.service import recovery_metadata
    from app.acquisition.ios_afc import is_ios_library_path, ios_library_metadata

    recovered_artifacts = await asyncio.to_thread(recovery_metadata, staging)
    ios_library_artifacts = await asyncio.to_thread(ios_library_metadata, staging)
    paths = [
        p
        for p in staging.rglob("*")
        if p.is_file()
        and not p.name.endswith(".risk")
        and "_backup" not in p.parts
        and not any(part.startswith("_") for part in p.parts)
        and not _is_junk_media_path(str(p))
        and (
            not is_recovery_namespace_path(p.relative_to(staging).as_posix())
            or p.relative_to(staging).as_posix() in recovered_artifacts
        )
        and (
            not is_ios_library_path(p.relative_to(staging).as_posix())
            or p.relative_to(staging).as_posix() in ios_library_artifacts
        )
    ]
    total = len(paths)
    sem = asyncio.Semaphore(settings.worker_concurrency)
    crawl_artifact_rows = await db.fetchall(
        "SELECT a.record_id, a.source_kind, a.role, a.mime_type, a.relative_path, "
        "a.sha256, "
        "r.social_scope, r.source_app, r.canonical_json FROM crawl_artifacts a "
        "JOIN crawl_records r ON r.crawl_id = a.crawl_id AND r.record_id = a.record_id "
        "WHERE a.session_id = ? AND a.verified = 1",
        (session_id,),
    )
    crawl_artifacts = {row["relative_path"]: row for row in crawl_artifact_rows}
    existing_file_rows = await db.fetchall(
        "SELECT id, path FROM files WHERE session_id = ?",
        (session_id,),
    )
    existing_file_ids = {str(row["path"]): str(row["id"]) for row in existing_file_rows}
    crawl_capture_meta: dict[str, dict[str, object]] = {}
    for row in crawl_artifact_rows:
        record_id = str(row["record_id"])
        if record_id in crawl_capture_meta:
            continue
        try:
            payload = json.loads(row["canonical_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("canonical crawl metadata is invalid") from exc
        captured_at = (
            payload.get("source_created_at")
            or payload.get("source_modified_at")
            or payload.get("observed_at")
        )
        captured_year = None
        if isinstance(captured_at, str) and len(captured_at) >= 4:
            try:
                year = int(captured_at[:4])
                captured_year = year if 1970 <= year <= 9999 else None
            except ValueError:
                captured_year = None
        from app.services.gallery import gallery_meta_from_canonical

        gallery_meta = gallery_meta_from_canonical(payload)
        crawl_capture_meta[record_id] = {
            "captured_at": captured_at if isinstance(captured_at, str) else None,
            "captured_year": captured_year,
            "date_source": "android_agent_canonical",
            **gallery_meta,
        }

    async def one(p: Path) -> tuple:
        async with sem:
            rel = str(p.relative_to(staging))
            artifact = crawl_artifacts.get(rel)
            recovered = recovered_artifacts.get(rel)
            ios_artifact = ios_library_artifacts.get(rel)
            if recovered is not None:
                from app.acquisition.android_recovery.paths import recovery_file_source

                source = recovery_file_source(recovered.source)
            elif artifact is not None:
                source = artifact["source_kind"]
            elif ios_artifact is not None:
                source = ios_artifact.source
            else:
                source = Path(rel).parts[0] if Path(rel).parts else "other"
            digest = (
                artifact["sha256"]
                if artifact is not None
                else ios_artifact.sha256
                if ios_artifact is not None
                else recovered.sha256 if recovered is not None else await hash_file(p)
            )
            mime = (
                artifact["mime_type"]
                if artifact is not None
                else ios_artifact.mime_type
                if ios_artifact is not None
                else recovered.mime_type if recovered is not None else guess_mime(p)
            )
            if recovered is not None and p.stat().st_size != recovered.size_bytes:
                raise RuntimeError("artifact recovery Android gagal verifikasi")
            if artifact is not None:
                capture = crawl_capture_meta[str(artifact["record_id"])]
            elif ios_artifact is not None and ios_artifact.captured_epoch_s is not None:
                captured = datetime.fromtimestamp(
                    ios_artifact.captured_epoch_s,
                    tz=timezone.utc,
                ).isoformat()
                capture = {
                    "captured_at": captured,
                    "captured_year": int(captured[:4]),
                    "date_source": "ios_photos_database",
                }
            else:
                from app.services.media_dates import capture_meta

                capture = capture_meta(p)
            meta = {"ext": p.suffix.lower(), **capture}
            if artifact is not None:
                capture_extra = crawl_capture_meta.get(str(artifact["record_id"]), {})
                meta.update(
                    {
                        "acquisition_method": "android_agent_direct_manifest",
                        "crawl_record_id": artifact["record_id"],
                        "crawl_artifact_role": artifact["role"],
                        "social_scope": artifact["social_scope"],
                        "source_app": artifact["source_app"],
                        "directory_hint": capture_extra.get("directory_hint"),
                        "display_name": capture_extra.get("display_name"),
                        "is_favorite": bool(capture_extra.get("is_favorite")),
                        "date_added": capture_extra.get("date_added"),
                        "date_modified": capture_extra.get("date_modified"),
                        "date_taken": capture_extra.get("date_taken"),
                        "album": capture_extra.get("album"),
                    }
                )
            if recovered is not None:
                meta.update(
                    {
                        "acquisition_method": "android_recovery_v1",
                        "recovery_candidate_id": recovered.candidate_id,
                        "recovery_source": recovered.source,
                        "recovery_classification": recovered.classification,
                        "recovery_confidence": recovered.confidence,
                        "recovery_expires_epoch_s": recovered.expires_epoch_s,
                    }
                )
            if ios_artifact is not None:
                meta.update(
                    {
                        "acquisition_method": "ios_photo_library_recovery_v1",
                        "ios_library_classification": ios_artifact.classification,
                        "ios_library_capture_method": ios_artifact.capture_method,
                        "ios_source_uuid": ios_artifact.source_uuid,
                        "ios_original_filename": ios_artifact.original_filename,
                    }
                )
            from app.services.gallery import album_leaf, looks_favorite
            from app.acquisition.source_app_hints import infer_source_app, inferred_album_label

            hint = meta.get("directory_hint") if isinstance(meta.get("directory_hint"), str) else None
            display = meta.get("display_name") if isinstance(meta.get("display_name"), str) else None
            inferred_album = inferred_album_label(
                directory_hint=hint,
                display_name=display,
                path=rel,
            )
            if inferred_album:
                meta["album"] = inferred_album
            elif not meta.get("album"):
                meta["album"] = album_leaf(
                    hint,
                    rel,
                    str(source),
                )
            meta["is_favorite"] = bool(meta.get("is_favorite")) or looks_favorite(
                rel,
                str(meta.get("album") or ""),
                str(meta.get("display_name") or ""),
                str(meta.get("directory_hint") or ""),
            )
            if is_crawl_record_mime(mime):
                from app.acquisition.agent_client import InventoryRecordV1

                try:
                    record = InventoryRecordV1.model_validate_json(p.read_bytes())
                except (OSError, ValueError) as exc:
                    raise RuntimeError("canonical crawl record is invalid") from exc
                meta.update(
                    {
                        "crawl_id": record.crawl_id,
                        "record_id": record.record_id,
                        "source_kind": record.source_kind,
                        "source_app": record.source_app,
                        "observed_at": record.observed_at,
                        "source_created_at": record.source_created_at,
                        "source_modified_at": record.source_modified_at,
                        "captured_at": record.source_created_at or record.observed_at,
                        "captured_year": int(
                            (record.source_created_at or record.observed_at)[:4]
                        ),
                        "provenance": record.provenance.model_dump(mode="json"),
                        "social_scope": (
                            record.metadata.social_scope
                            if record.source_kind == "visible_ui"
                            else None
                        ),
                    }
                )
                if record.source_kind == "contact":
                    from app.acquisition.contact_identity import (
                        contact_cluster_keys,
                        contact_emails,
                        contact_phones,
                    )

                    meta["contact_phones"] = contact_phones(record.metadata)
                    meta["contact_emails"] = contact_emails(record.metadata)
                    meta["contact_cluster_keys"] = contact_cluster_keys(record.metadata)
            if not meta.get("source_app"):
                inferred_app = infer_source_app(
                    directory_hint=meta.get("directory_hint")
                    if isinstance(meta.get("directory_hint"), str)
                    else None,
                    display_name=meta.get("display_name")
                    if isinstance(meta.get("display_name"), str)
                    else None,
                    path=rel,
                )
                if inferred_app:
                    meta["source_app"] = inferred_app
                    meta["source_app_inferred"] = True
            display_for_capture = (
                meta.get("display_name") if isinstance(meta.get("display_name"), str) else None
            )
            if is_agent_self_capture(rel, display_for_capture):
                meta["acquisition_self_capture"] = True
            analyzed = 1 if meta.get("acquisition_self_capture") else 0
            file_id = (
                existing_file_ids.get(rel)
                or (
                    stable_file_id(session_id, rel)
                    if artifact is not None or recovered is not None or ios_artifact is not None
                    else str(uuid.uuid4())
                )
            )
            return (
                file_id,
                session_id,
                source,
                rel,
                mime,
                p.stat().st_size,
                digest,
                "pulled",
                analyzed,
                json.dumps(meta),
            )

    wave = 64
    indexed = 0
    for start in range(0, total, wave):
        batch = paths[start : start + wave]
        rows = await asyncio.gather(*(one(p) for p in batch))
        files.extend(rows)
        indexed += len(rows)
        pct = 45 + (indexed / max(total, 1)) * 15
        await on_progress(
            SessionStatus.INDEXING,
            pct,
            f"Indexing & hashing ({indexed}/{total})",
            files_listed=total,
            files_pulled=total,
            files_indexed=indexed,
        )

    if files:
        from app.acquisition.contact_identity import annotate_contact_file_rows

        files = annotate_contact_file_rows(files)
        await db.executemany(
            """
            INSERT INTO files (id, session_id, source, path, mime, size_bytes, sha256, pull_status, analyzed, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source,
                path = excluded.path,
                mime = excluded.mime,
                size_bytes = excluded.size_bytes,
                sha256 = excluded.sha256,
                pull_status = excluded.pull_status,
                meta_json = excluded.meta_json
            """,
            files,
        )

    return indexed, (time.perf_counter() - t0) * 1000
