"""Refresh mapping on an existing completed session (labels, findings, report)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.acquisition.android_recovery.paths import recovery_file_source
from app.acquisition.contact_identity import (
    annotate_contact_file_rows,
    contact_cluster_keys,
    contact_emails,
    contact_phones,
)
from app.acquisition.media_types import is_agent_self_capture
from app.acquisition.source_app_hints import infer_source_app
from app.core.db import db
from app.services.analysis import finding_attachment_row, media_siblings_by_record
from app.services.reports import save_session_report

logger = logging.getLogger("siksik.session_mapping")


def _meta(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def refresh_session_mapping(session_id: str) -> dict[str, int]:
    """Apply current mapping rules to files/findings already on disk."""
    file_rows = await db.fetchall(
        "SELECT id, source, path, mime, meta_json, analyzed FROM files WHERE session_id = ?",
        (session_id,),
    )
    crawl_rows = await db.fetchall(
        "SELECT record_id, canonical_json, source_kind FROM crawl_records WHERE session_id = ?",
        (session_id,),
    )
    canonical_by_id: dict[str, dict] = {}
    for row in crawl_rows:
        if not row["canonical_json"]:
            continue
        try:
            payload = json.loads(row["canonical_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            canonical_by_id[str(row["record_id"])] = payload

    tuples: list[tuple] = []
    recovered_cache = 0
    self_captures = 0
    inferred_apps = 0
    for row in file_rows:
        meta = _meta(row["meta_json"])
        source = str(row["source"] or "")
        path = str(row["path"] or "")
        display = meta.get("display_name") if isinstance(meta.get("display_name"), str) else None
        recovery_source = meta.get("recovery_source")
        if recovery_source:
            mapped = recovery_file_source(str(recovery_source))
            if mapped != source:
                source = mapped
                if mapped == "recovered_cache":
                    recovered_cache += 1
        if not meta.get("source_app"):
            inferred = infer_source_app(
                directory_hint=meta.get("directory_hint")
                if isinstance(meta.get("directory_hint"), str)
                else None,
                display_name=display,
                path=path,
            )
            if inferred:
                meta["source_app"] = inferred
                meta["source_app_inferred"] = True
                inferred_apps += 1
        if is_agent_self_capture(path, display):
            meta["acquisition_self_capture"] = True
            self_captures += 1
        record_id = str(meta.get("crawl_record_id") or meta.get("record_id") or "")
        payload = canonical_by_id.get(record_id) if record_id else None
        if source == "contact" and payload:
            metadata = payload.get("metadata") if isinstance(payload, dict) else None
            meta["contact_phones"] = contact_phones(metadata)
            meta["contact_emails"] = contact_emails(metadata)
            meta["contact_cluster_keys"] = contact_cluster_keys(metadata)
        analyzed = 1 if meta.get("acquisition_self_capture") else int(row["analyzed"] or 0)
        tuples.append(
            (
                row["id"],
                session_id,
                source,
                path,
                row["mime"],
                0,
                "",
                "pulled",
                analyzed,
                json.dumps(meta, ensure_ascii=False),
            )
        )

    tuples = annotate_contact_file_rows(tuples)
    contact_dups = 0
    for item in tuples:
        try:
            meta = json.loads(item[9])
        except (TypeError, json.JSONDecodeError):
            continue
        if meta.get("contact_duplicate"):
            contact_dups += 1

    await db.executemany(
        """
        UPDATE files
        SET source = ?, meta_json = ?, analyzed = CASE WHEN ? = 1 THEN 1 ELSE analyzed END
        WHERE id = ?
        """,
        [(item[2], item[9], item[8], item[0]) for item in tuples],
    )

    sibling_rows = await db.fetchall(
        "SELECT id, source, path, mime, meta_json FROM files WHERE session_id = ?",
        (session_id,),
    )
    siblings = media_siblings_by_record(sibling_rows)
    files_by_id = {str(row["id"]): row for row in sibling_rows}
    findings = await db.fetchall(
        "SELECT id, file_id, path FROM findings WHERE session_id = ?",
        (session_id,),
    )
    retargeted = 0
    for finding in findings:
        current = files_by_id.get(str(finding["file_id"]))
        if current is None:
            continue
        target = finding_attachment_row(current, siblings)
        if str(target["id"]) == str(finding["file_id"]):
            continue
        await db.execute(
            """
            UPDATE findings
            SET file_id = ?, source = ?, path = ?
            WHERE id = ?
            """,
            (target["id"], target["source"], target["path"], finding["id"]),
        )
        retargeted += 1

    await db.execute(
        """
        UPDATE findings
        SET source = (
            SELECT fi.source FROM files fi WHERE fi.id = findings.file_id
        )
        WHERE session_id = ?
          AND file_id IN (SELECT id FROM files WHERE session_id = ?)
        """,
        (session_id, session_id),
    )

    try:
        await save_session_report(session_id)
        report_saved = 1
    except Exception:
        logger.exception("save_session_report_failed session_id=%s", session_id)
        report_saved = 0

    return {
        "files": len(tuples),
        "recovered_cache": recovered_cache,
        "self_captures": self_captures,
        "inferred_apps": inferred_apps,
        "contact_duplicates": contact_dups,
        "findings_retargeted": retargeted,
        "report_saved": report_saved,
    }
