"""Selective iOS backup extract for Messages (SMS+iMessage) and Contacts.

Uses pymobiledevice3 `backup2 backup --only sms --only contacts` (not full device
backup) so it stays bounded in host I/O while still supporting modern iOS.

Manifest paths are resolved by relativePath (not hardcoded file hashes) for
cross-version compatibility. Message `service` distinguishes SMS vs iMessage
inside the shared sms.db.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.agent_client import InventoryRecordV1
from app.acquisition.contracts import ProgressCallback
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.process import run_process
from app.acquisition.time_scope import build_time_scope
from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.ios_backup_comms")

APPLE_EPOCH = 978_307_200
AGENT_VERSION = "ios-backup-comms-1"
PRINTABLE_RE = re.compile(r"[^\x20-\x7E\u00A0-\uFFFF]+")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity_hash(namespace: str, identity: str) -> str:
    return _sha256_text(f"{namespace}:{identity}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _apple_ts_to_iso(raw: Any) -> str | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # iOS stores Cocoa timestamp as seconds, or nanoseconds since 2001-01-01.
    if value > 10**17:
        seconds = (value / 1e9) + APPLE_EPOCH
    elif value > 10**14:
        seconds = (value / 1e9) + APPLE_EPOCH
    elif value > 10**11:
        seconds = (value / 1e6) + APPLE_EPOCH
    else:
        seconds = float(value) + APPLE_EPOCH
    try:
        return (
            datetime.fromtimestamp(seconds, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        return None


def _text_from_attributed_body(blob: bytes | None) -> str | None:
    """Best-effort plain text from NSAttributedString blob (cross iOS versions)."""
    if not blob:
        return None
    decoded = blob.decode("utf-8", errors="ignore")
    if "NSString" in decoded:
        decoded = decoded.split("NSString", 1)[-1]
    cleaned = PRINTABLE_RE.sub(" ", decoded)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Drop common stream noise tokens
    for noise in ("NSDictionary", "NSObject", "NSValue", "NSNumber", "$class"):
        cleaned = cleaned.replace(noise, " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 2:
        return None
    return cleaned[:4096]


def _puller_python() -> Path:
    return settings.ios_media_puller_path.resolve() / ".venv" / "bin" / "python"


def _limit_for_mode(mode: AcquisitionMode, quick: int, full: int) -> int | None:
    selected = quick if mode == AcquisitionMode.QUICK else full
    return selected if selected > 0 else None


def _resolve_manifest_file(backup_root: Path, relative_like: str) -> Path | None:
    manifest = backup_root / "Manifest.db"
    if not manifest.is_file():
        return None
    con = sqlite3.connect(f"file:{manifest}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT fileID, relativePath FROM Files "
            "WHERE relativePath LIKE ? ORDER BY length(relativePath) ASC LIMIT 1",
            (relative_like,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    file_id = str(row[0])
    candidates = (
        backup_root / file_id[:2] / file_id,
        backup_root / file_id,
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _find_backup_udid_dir(work: Path, udid: str) -> Path | None:
    direct = work / udid
    if (direct / "Manifest.db").is_file():
        return direct
    for child in work.iterdir():
        if child.is_dir() and (child / "Manifest.db").is_file():
            return child
    return None


async def _run_selective_backup(udid: str, work: Path) -> Path:
    python_bin = _puller_python()
    if not python_bin.is_file():
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "ios-media-puller venv tidak siap untuk backup2.",
        )
    work.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
        "UDID": udid,
    }
    result = await run_process(
        [
            str(python_bin),
            "-m",
            "pymobiledevice3",
            "backup2",
            "backup",
            "--udid",
            udid,
            "--full",
            "--only",
            "sms",
            "--only",
            "contacts",
            str(work),
        ],
        timeout=settings.ios_backup_comms_timeout_s,
        cwd=settings.ios_media_puller_path.resolve(),
        env=env,
        check=False,
        output_limit_bytes=512 * 1024,
        operation="ios_backup_comms",
        not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
        timeout_category=ErrorCategory.ADB_TIMEOUT,
        failure_category=ErrorCategory.AGENT_UNREACHABLE,
    )
    if result.returncode != 0:
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "Selective iOS backup (sms/contacts) gagal.",
            retryable=True,
            dependency_exit_code=result.returncode,
        )
    backup_root = _find_backup_udid_dir(work, udid)
    if backup_root is None:
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "Manifest.db selective backup tidak ditemukan.",
            retryable=True,
        )
    return backup_root


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def parse_messages_db(
    db_path: Path,
    *,
    limit: int | None,
    not_before_epoch_s: float | None = None,
) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cols = _table_columns(con, "message")
        if "ROWID" not in cols:
            return []
        has_attr = "attributedBody" in cols
        has_service = "service" in cols
        has_text = "text" in cols
        select = [
            "m.ROWID AS rowid",
            "m.is_from_me AS is_from_me",
            "m.date AS date",
            "h.id AS handle_id",
        ]
        if has_text:
            select.append("m.text AS text")
        if has_attr:
            select.append("m.attributedBody AS attributed_body")
        if has_service:
            select.append("m.service AS service")
        else:
            select.append("h.service AS service")
        sql = (
            f"SELECT {', '.join(select)} FROM message m "
            "LEFT JOIN handle h ON h.ROWID = m.handle_id "
            "ORDER BY m.date DESC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = con.execute(sql).fetchall()
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        text = (row["text"] if "text" in row.keys() else None) or None
        if isinstance(text, str):
            text = text.strip() or None
        if not text and has_attr:
            blob = row["attributed_body"] if "attributed_body" in row.keys() else None
            if isinstance(blob, bytes):
                text = _text_from_attributed_body(blob)
        service_raw = str(row["service"] or "").strip()
        service = "imessage" if service_raw.lower() == "imessage" else "sms"
        if service_raw.lower() not in {"imessage", "sms"} and service_raw:
            service = "sms"
        address = str(row["handle_id"] or "").strip() or None
        is_from_me = bool(row["is_from_me"])
        sent_at = _apple_ts_to_iso(row["date"])
        if not_before_epoch_s is not None and sent_at is not None:
            try:
                sent_epoch = datetime.fromisoformat(
                    sent_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                sent_epoch = None
            if sent_epoch is not None and sent_epoch < not_before_epoch_s:
                continue
        body = text or ""
        prefix = "[iMessage]" if service == "imessage" else "[SMS]"
        normalized = f"{prefix} {address or ''} {body}".strip()[:65536]
        out.append(
            {
                "rowid": int(row["rowid"]),
                "service": service,
                "address": address,
                "is_from_me": is_from_me,
                "sent_at": sent_at,
                "text": body,
                "normalized_text": normalized,
            }
        )
    return out


def parse_contacts_db(
    db_path: Path,
    *,
    limit: int | None,
) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        person_cols = _table_columns(con, "ABPerson")
        if "ROWID" not in person_cols:
            return []
        people = con.execute(
            "SELECT ROWID, First, Last, Middle, Organization, "
            "CreationDate, ModificationDate FROM ABPerson "
            "ORDER BY ROWID DESC"
            + (f" LIMIT {int(limit)}" if limit is not None else "")
        ).fetchall()
        multi_cols = _table_columns(con, "ABMultiValue")
        multi_rows = []
        if "record_id" in multi_cols and "value" in multi_cols:
            multi_rows = con.execute(
                "SELECT record_id, property, label, value FROM ABMultiValue"
            ).fetchall()
    finally:
        con.close()

    by_person: dict[int, list[sqlite3.Row]] = {}
    for row in multi_rows:
        by_person.setdefault(int(row["record_id"]), []).append(row)

    out: list[dict[str, Any]] = []
    for person in people:
        pid = int(person["ROWID"])
        first = (person["First"] or "").strip()
        last = (person["Last"] or "").strip()
        middle = (person["Middle"] or "").strip()
        org = (person["Organization"] or "").strip()
        display = " ".join(p for p in (first, middle, last) if p).strip() or org or f"contact-{pid}"
        phones: list[dict[str, str]] = []
        emails: list[dict[str, str]] = []
        for mv in by_person.get(pid, []):
            value = str(mv["value"] or "").strip()
            if not value:
                continue
            prop = int(mv["property"] or 0)
            # 3 = phone, 4 = email (AddressBook constants)
            entry = {"value": value[:2048], "normalized_value": value[:2048], "label": None}
            if prop == 3 and len(phones) < 32:
                phones.append(entry)
            elif prop == 4 and len(emails) < 32:
                emails.append(entry)
        updated = _apple_ts_to_iso(person["ModificationDate"] or person["CreationDate"])
        lines = [display, *[p["value"] for p in phones], *[e["value"] for e in emails]]
        out.append(
            {
                "rowid": pid,
                "display_name": display[:2048],
                "phones": phones,
                "emails": emails,
                "organization": org[:2048] if org else None,
                "updated_at": updated,
                "normalized_text": "\n".join(lines)[:65536],
            }
        )
    return out


def _message_record(
    *,
    session_id: str,
    crawl_id: str,
    item: dict[str, Any],
) -> InventoryRecordV1:
    observed = item.get("sent_at") or _utc_now_iso()
    address = item.get("address")
    address_identity = (
        _identity_hash("sms_address", address) if address else None
    )
    thread_key = f"{item.get('service')}:{address or item['rowid']}"
    thread_identity = _identity_hash("sms_thread", thread_key)
    record_id = f"ios_msg_{uuid.uuid4().hex}"
    payload = {
        "schema_version": 1,
        "record_id": record_id,
        "crawl_id": crawl_id,
        "siksik_session_id": session_id,
        "source_kind": "sms",
        "source_app": "com.apple.MobileSMS",
        "source_locator": f"ios_sms:{item['service']}:{item['rowid']}",
        "observed_at": observed,
        "source_created_at": observed,
        "source_modified_at": observed,
        "normalized_text": item.get("normalized_text") or None,
        "metadata": {
            "direction": "sent" if item.get("is_from_me") else "received",
            "address": address,
            "address_identity": address_identity,
            "thread_identity": thread_identity,
            "message_type": 2 if item.get("service") == "imessage" else 1,
            "status": 0,
            "subscription_id": None,
            "is_read": None,
            "is_seen": None,
            "sent_at": observed,
            "warning_codes": (
                ["service:imessage"]
                if item.get("service") == "imessage"
                else ["service:sms"]
            ),
        },
        "attachment_ids": [],
        "content_sha256": None,
        "preprocessing": None,
        "selection": None,
        "provenance": {
            "source_adapter": "ios_backup_messages",
            "enumeration_method": "ios_mobilebackup2",
            "agent_version": AGENT_VERSION,
            "original_staged": False,
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payload["content_sha256"] = _sha256_text(raw)
    return InventoryRecordV1.model_validate(payload)


def _contact_record(
    *,
    session_id: str,
    crawl_id: str,
    item: dict[str, Any],
) -> InventoryRecordV1:
    observed = item.get("updated_at") or _utc_now_iso()
    lookup = _identity_hash("ios_contact", str(item["rowid"]))
    orgs = []
    if item.get("organization"):
        orgs.append(
            {
                "company": item["organization"],
                "title": None,
                "department": None,
            }
        )
    record_id = f"ios_ct_{uuid.uuid4().hex}"
    payload = {
        "schema_version": 1,
        "record_id": record_id,
        "crawl_id": crawl_id,
        "siksik_session_id": session_id,
        "source_kind": "contact",
        "source_app": "com.apple.MobileAddressBook",
        "source_locator": f"ios_contact:{item['rowid']}",
        "observed_at": observed,
        "source_created_at": observed,
        "source_modified_at": observed,
        "normalized_text": item.get("normalized_text") or None,
        "metadata": {
            "display_name": item.get("display_name"),
            "lookup_identity": lookup,
            "phones": item.get("phones") or [],
            "emails": item.get("emails") or [],
            "organizations": orgs,
            "updated_at": observed,
            "warning_codes": [],
        },
        "attachment_ids": [],
        "content_sha256": None,
        "preprocessing": None,
        "selection": None,
        "provenance": {
            "source_adapter": "ios_backup_contacts",
            "enumeration_method": "ios_mobilebackup2",
            "agent_version": AGENT_VERSION,
            "original_staged": False,
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payload["content_sha256"] = _sha256_text(raw)
    return InventoryRecordV1.model_validate(payload)


async def _persist(
    *,
    session_id: str,
    crawl_id: str,
    staging: Path,
    records: list[InventoryRecordV1],
) -> int:
    if not records:
        return 0
    sms_dir = staging / "sms"
    contact_dir = staging / "contacts"
    sms_dir.mkdir(parents=True, exist_ok=True)
    contact_dir.mkdir(parents=True, exist_ok=True)
    now = utcnow()
    fingerprint = _sha256_text(f"ios_backup_comms:{session_id}:{crawl_id}")
    rows: list[tuple[object, ...]] = []

    async with db.transaction(immediate=True) as conn:
        existing = await (
            await conn.execute(
                "SELECT crawl_id FROM crawl_runs WHERE session_id = ?",
                (session_id,),
            )
        ).fetchone()
        effective_crawl = str(existing["crawl_id"]) if existing else crawl_id
        if existing is None:
            await conn.execute(
                """
                INSERT INTO crawl_runs (
                    crawl_id, session_id, state, policy_version, policy_fingerprint,
                    selection_revision, selection_fingerprint, review_candidates,
                    selection_confirmed, totals_json, started_at, updated_at,
                    frozen_at, confirmed_at, failure_reason
                ) VALUES (?, ?, 'completed', 'ios_backup_comms', ?, 1, ?, 0, 1, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    effective_crawl,
                    session_id,
                    fingerprint,
                    fingerprint,
                    json.dumps({"records": len(records)}, separators=(",", ":")),
                    now,
                    now,
                    now,
                    now,
                ),
            )
        else:
            await conn.execute(
                "UPDATE crawl_runs SET updated_at = ?, totals_json = ? WHERE session_id = ?",
                (
                    now,
                    json.dumps({"records": len(records)}, separators=(",", ":")),
                    session_id,
                ),
            )

        for record in records:
            if record.crawl_id != effective_crawl:
                record = record.model_copy(update={"crawl_id": effective_crawl})
            bucket = "sms" if record.source_kind == "sms" else "contacts"
            rel = Path(bucket) / f"{record.record_id}.siksik-record.json"
            abs_path = staging / rel
            payload = record.model_dump(mode="json")
            abs_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            rows.append(
                (
                    record.record_id,
                    effective_crawl,
                    session_id,
                    record.source_kind,
                    record.source_app,
                    None,
                    record.normalized_text,
                    record.content_sha256,
                    1,
                    fingerprint,
                    json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                    str(rel),
                    now,
                )
            )
        await conn.executemany(
            """
            INSERT OR REPLACE INTO crawl_records (
                record_id, crawl_id, session_id, source_kind, source_app, social_scope,
                normalized_text, content_sha256, selection_revision, selection_fingerprint,
                canonical_json, canonical_path, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(records)


async def acquire_ios_backup_comms(
    session_id: str,
    device_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress: ProgressCallback,
) -> int:
    """Pull SMS+iMessage and contacts via selective mobilebackup2."""
    if not settings.ios_sms_contacts_enabled:
        return 0

    from app.acquisition.ios_social import validate_ios_udid

    udid = validate_ios_udid(device_id)
    await on_progress(
        SessionStatus.ACQUIRING,
        38,
        "iOS Messages/Contacts (selective backup)…",
        acquisition_method="ios_backup_comms",
    )
    work = staging / "_ios_backup_comms"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    try:
        backup_root = await _run_selective_backup(udid, work)
    except AcquisitionError:
        shutil.rmtree(work, ignore_errors=True)
        raise

    sms_path = _resolve_manifest_file(backup_root, "%Library/SMS/sms.db")
    if sms_path is None:
        sms_path = _resolve_manifest_file(backup_root, "%/SMS/sms.db")
    contacts_path = _resolve_manifest_file(
        backup_root, "%Library/AddressBook/AddressBook.sqlitedb"
    )
    if contacts_path is None:
        contacts_path = _resolve_manifest_file(
            backup_root, "%AddressBook.sqlitedb"
        )

    msg_limit = _limit_for_mode(
        mode,
        settings.ios_sms_quick_messages,
        settings.ios_sms_full_messages,
    )
    contact_limit = _limit_for_mode(
        mode,
        settings.ios_contacts_quick,
        settings.ios_contacts_full,
    )

    crawl_id = f"ios_comms_{uuid.uuid4().hex[:24]}"
    records: list[InventoryRecordV1] = []
    sms_not_before = build_time_scope(mode).not_before.timestamp()
    if sms_path is not None:
        for item in parse_messages_db(
            sms_path,
            limit=msg_limit,
            not_before_epoch_s=sms_not_before,
        ):
            records.append(
                _message_record(session_id=session_id, crawl_id=crawl_id, item=item)
            )
    if contacts_path is not None:
        for item in parse_contacts_db(contacts_path, limit=contact_limit):
            records.append(
                _contact_record(session_id=session_id, crawl_id=crawl_id, item=item)
            )

    count = await _persist(
        session_id=session_id,
        crawl_id=crawl_id,
        staging=staging,
        records=records,
    )
    shutil.rmtree(work, ignore_errors=True)
    await on_progress(
        SessionStatus.ACQUIRING,
        42,
        f"iOS Messages/Contacts selesai ({count} record)",
        acquisition_method="ios_backup_comms",
        files_pulled=count,
    )
    logger.info(
        "ios_backup_comms_completed",
        extra={
            "session_id": session_id,
            "crawl_id": crawl_id,
            "item_count": count,
            "source_adapter": "ios_backup_messages",
        },
    )
    return count
