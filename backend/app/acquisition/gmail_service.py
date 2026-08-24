from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

import httpx

from app.acquisition.time_scope import build_time_scope
from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.gmail")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned[:128] or "attachment"


def _decode_base64url(data: str) -> bytes:
    pad = len(data) % 4
    if pad > 0:
        data += "=" * (4 - pad)
    return base64.urlsafe_b64decode(data)


def _extract_body(payload: dict[str, Any]) -> tuple[str, str]:
    plain_text = ""
    html_text = ""

    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})

    if "data" in body and body["data"]:
        decoded = _decode_base64url(body["data"]).decode("utf-8", errors="replace")
        if "html" in mime_type:
            html_text = decoded
        else:
            plain_text = decoded

    parts = payload.get("parts", [])
    for part in parts:
        part_mime = part.get("mimeType", "")
        part_body = part.get("body", {})
        if "data" in part_body and part_body["data"]:
            decoded = _decode_base64url(part_body["data"]).decode("utf-8", errors="replace")
            if "html" in part_mime:
                html_text = decoded
            elif "plain" in part_mime:
                plain_text = decoded
        if "parts" in part:
            sub_plain, sub_html = _extract_body(part)
            if sub_plain and not plain_text:
                plain_text = sub_plain
            if sub_html and not html_text:
                html_text = sub_html

    return plain_text, html_text


class _EmailTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "template"}:
            self.suppressed += 1
        elif tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "template"} and self.suppressed > 0:
            self.suppressed -= 1
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.suppressed == 0:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _EmailTextParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, TypeError):
        return ""
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _iter_parts(payload: dict[str, Any]):
    for part in payload.get("parts", []):
        yield part
        yield from _iter_parts(part)


def _render_email_html(
    *,
    subject: str,
    sender: str,
    to: str,
    date_str: str,
    labels: list[str],
    body_text: str,
    body_html: str,
    attachments: list[str],
) -> str:
    label_chips = "".join(
        f'<span style="display:inline-block;background:#e2e8f0;color:#334155;padding:2px 8px;border-radius:12px;font-size:11px;margin-right:4px;">{html.escape(lbl)}</span>'
        for lbl in labels
    )
    attachment_links = "".join(
        f'<li style="margin:2px 0;">{html.escape(att)}</li>' for att in attachments
    )
    attachment_section = (
        f'<div style="margin-top:12px;padding-top:8px;border-top:1px dashed #cbd5e1;font-size:12px;color:#64748b;"><strong>Lampiran:</strong><ul style="margin:4px 0 0 16px;padding:0;">{attachment_links}</ul></div>'
        if attachments
        else ""
    )

    readable_body = body_text.strip() or _html_to_text(body_html)
    main_content = (
        "<div style='white-space:pre-wrap;font-family:inherit;'>"
        f"{html.escape(readable_body or '(Isi email kosong)')}</div>"
    )

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(subject or "Email")}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; color: #1e293b; margin: 0; padding: 16px; }}
.card {{ background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; max-width: 800px; margin: 0 auto; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
.header {{ border-bottom: 1px solid #e2e8f0; padding-bottom: 12px; margin-bottom: 12px; }}
.subject {{ font-size: 16px; font-weight: 600; color: #0f172a; margin: 0 0 8px 0; }}
.meta-row {{ font-size: 12px; color: #475569; margin: 2px 0; }}
.body {{ font-size: 14px; line-height: 1.5; color: #334155; word-break: break-word; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <h2 class="subject">{html.escape(subject or "(Tanpa Subjek)")}</h2>
    <div class="meta-row"><strong>Dari:</strong> {html.escape(sender or "—")}</div>
    <div class="meta-row"><strong>Kepada:</strong> {html.escape(to or "—")}</div>
    <div class="meta-row"><strong>Waktu:</strong> {html.escape(date_str or "—")}</div>
    <div style="margin-top:6px;">{label_chips}</div>
  </div>
  <div class="body">
    {main_content}
  </div>
  {attachment_section}
</div>
</body>
</html>
"""


class GmailAcquisitionService:
    def __init__(self, timeout_s: float | None = None) -> None:
        self._timeout_s = timeout_s or settings.gmail_request_timeout_s

    async def acquire(
        self,
        session_id: str,
        staging: Path,
        mode: AcquisitionMode,
        *,
        token: str | None = None,
        account_name: str | None = None,
        simulated: bool = False,
        on_progress=None,
        request_id: str | None = None,
        reference: datetime | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        t0 = time.perf_counter()
        email_dir = staging / "email"
        email_dir.mkdir(parents=True, exist_ok=True)

        if simulated:
            return await self._acquire_simulated(
                session_id=session_id,
                email_dir=email_dir,
                mode=mode,
                account_name=account_name or "user@gmail.com",
                on_progress=on_progress,
            )

        if not token:
            logger.error(
                "gmail_live_token_not_available",
                extra={
                    "session_id": session_id,
                    "account_name": account_name,
                    "error": "Otorisasi Google belum aktif di perangkat",
                },
            )
            if on_progress:
                await on_progress(
                    SessionStatus.ACQUIRING,
                    50.0,
                    "Gmail tidak diambil: otorisasi Google belum tersedia pada perangkat",
                    acquisition_method="gmail_api",
                )
            return 0, []

        max_messages = (
            settings.gmail_quick_max_messages
            if mode == AcquisitionMode.QUICK
            else settings.gmail_full_max_messages
        )

        time_scope = build_time_scope(mode, reference=reference)
        after_date = time_scope.not_before.strftime("%Y/%m/%d")
        query = f"after:{after_date}"
        logger.info(
            "gmail_time_scope",
            extra={
                "session_id": session_id,
                "mode": mode.value,
                "not_before": time_scope.not_before.isoformat(),
                "reference": (reference or datetime.now(timezone.utc)).isoformat(),
            },
        )

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        if on_progress:
            await on_progress(
                SessionStatus.ACQUIRING,
                50.0,
                f"Mengunduh email Gmail ({account_name or 'akun aktif'})…",
                acquisition_method="gmail_api",
            )

        items_saved = 0
        records: list[dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, headers=headers) as client:
                page_token: str | None = None
                fetched = 0
                while True:
                    remaining = max_messages - fetched if max_messages > 0 else None
                    if remaining is not None and remaining <= 0:
                        break
                    params: dict[str, Any] = {
                        "q": query,
                        "maxResults": min(remaining, 100) if remaining is not None else 100,
                    }
                    if page_token:
                        params["pageToken"] = page_token
                    list_res = await client.get(f"{GMAIL_API_BASE}/messages", params=params)
                    if list_res.status_code != 200:
                        logger.warning(
                            "gmail_api_list_failed",
                            extra={
                                "status_code": list_res.status_code,
                                "response": list_res.text[:200],
                            },
                        )
                        break
                    page = list_res.json()
                    messages_summary = page.get("messages", [])
                    if remaining is not None:
                        messages_summary = messages_summary[:remaining]
                    for msg_meta in messages_summary:
                        msg_id = msg_meta.get("id")
                        if not msg_id:
                            continue
                        msg_res = await client.get(
                            f"{GMAIL_API_BASE}/messages/{msg_id}",
                            params={"format": "full"},
                        )
                        fetched += 1
                        if msg_res.status_code != 200:
                            continue
                        msg_data = msg_res.json()
                        epoch_ms = int(msg_data.get("internalDate", 0) or 0)
                        if epoch_ms > 0 and epoch_ms < time_scope.not_before_epoch_ms:
                            continue
                        saved = await self._process_message(
                            client=client,
                            session_id=session_id,
                            email_dir=email_dir,
                            msg_data=msg_data,
                            account_name=account_name or "me",
                        )
                        if saved:
                            items_saved += len(saved)
                            records.extend(saved)
                        if on_progress and fetched % 20 == 0:
                            await on_progress(
                                SessionStatus.ACQUIRING,
                                58.0,
                                f"Gmail fetch {fetched} email",
                                files_pulled=items_saved,
                                acquisition_method="gmail_api",
                            )
                    page_token = page.get("nextPageToken")
                    if not page_token or not messages_summary:
                        break

        except Exception as exc:
            logger.warning(
                "gmail_acquisition_error",
                extra={"session_id": session_id, "error": str(exc)},
            )

        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "gmail_acquisition_complete",
            extra={
                "session_id": session_id,
                "items_saved": items_saved,
                "duration_ms": round(duration_ms, 1),
            },
        )
        return items_saved, records

    async def _process_message(
        self,
        *,
        client: httpx.AsyncClient | None,
        session_id: str,
        email_dir: Path,
        msg_data: dict[str, Any],
        account_name: str,
    ) -> list[dict[str, Any]]:
        email_dir.mkdir(parents=True, exist_ok=True)
        msg_id = msg_data.get("id", str(uuid.uuid4()))
        payload = msg_data.get("payload", {})
        headers_list = payload.get("headers", [])
        headers_map = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}

        subject = headers_map.get("subject", "(Tanpa Subjek)")
        sender = headers_map.get("from", "")
        to = headers_map.get("to", "")
        date_str = headers_map.get("date", "")
        labels = msg_data.get("labelIds", [])

        plain_text, html_text = _extract_body(payload)
        snippet = msg_data.get("snippet", "")
        combined_text = plain_text or _html_to_text(html_text) or snippet or subject

        epoch_ms = int(msg_data.get("internalDate", 0))
        iso_timestamp = (
            datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).isoformat()
            if epoch_ms > 0
            else utcnow()
        )

        attachments: list[str] = []
        attachment_files: list[tuple[Path, str, str]] = []

        if client is not None:
            for part in _iter_parts(payload):
                filename = part.get("filename")
                part_body = part.get("body", {})
                attach_id = part_body.get("attachmentId")
                if filename and attach_id:
                    safe_fn = _safe_name(filename)
                    att_path = email_dir / f"{msg_id}_{safe_fn}"
                    try:
                        att_res = await client.get(
                            f"{GMAIL_API_BASE}/messages/{msg_id}/attachments/{attach_id}"
                        )
                        if att_res.status_code == 200:
                            raw_data = att_res.json().get("data", "")
                            if raw_data:
                                att_bytes = _decode_base64url(raw_data)
                                att_path.write_bytes(att_bytes)
                                attachments.append(filename)
                                attachment_files.append(
                                    (att_path, part.get("mimeType", "application/octet-stream"), filename)
                                )
                    except Exception as e:
                        logger.warning("attachment_download_error", extra={"error": str(e)})

        rendered_html = _render_email_html(
            subject=subject,
            sender=sender,
            to=to,
            date_str=date_str or iso_timestamp,
            labels=labels,
            body_text=plain_text or snippet,
            body_html=html_text,
            attachments=attachments,
        )
        html_file = email_dir / f"email_{msg_id}.html"
        html_file.write_text(rendered_html, encoding="utf-8")

        meta_payload = {
            "id": msg_id,
            "account": account_name,
            "subject": subject,
            "from": sender,
            "to": to,
            "date": date_str,
            "timestamp": iso_timestamp,
            "labels": labels,
            "snippet": snippet,
            "plain_text": plain_text,
            "attachments": attachments,
        }
        json_file = email_dir / f"email_{msg_id}.json"
        json_file.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

        crawl_id = f"crawl_gmail_{session_id[:8]}"
        record_id = f"gmail_{msg_id}"
        canonical_rel = f"email/email_{msg_id}.html"
        html_sha256 = hashlib.sha256(rendered_html.encode("utf-8")).hexdigest()

        now_str = utcnow()
        await db.execute(
            """
            INSERT OR REPLACE INTO crawl_records (
                record_id, crawl_id, session_id, source_kind, source_app,
                social_scope, normalized_text, content_sha256, selection_revision,
                selection_fingerprint, canonical_json, canonical_path, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                crawl_id,
                session_id,
                "email",
                "com.google.android.gm",
                "gmail_messages",
                f"{subject}\n{sender}\n{to}\n{combined_text}",
                html_sha256,
                1,
                "gmail_api",
                json.dumps(
                    {
                        "source_kind": "email",
                        "source_created_at": iso_timestamp,
                        "source_modified_at": iso_timestamp,
                        "observed_at": iso_timestamp,
                        "metadata": {
                            "display_name": f"[{subject[:40]}] {sender}",
                            "album": "Email",
                            "directory_hint": "Email",
                            "date_header": date_str,
                            "capture_time": iso_timestamp,
                            "is_favorite": "STARRED" in labels or "IMPORTANT" in labels,
                        },
                    }
                ),
                canonical_rel,
                now_str,
            ),
        )

        await db.execute(
            """
            INSERT OR REPLACE INTO crawl_artifacts (
                artifact_id, crawl_id, session_id, record_id, source_kind,
                role, mime_type, relative_path, size_bytes, sha256, verified, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"art_email_{msg_id}",
                crawl_id,
                session_id,
                record_id,
                "email",
                "email_body",
                "text/html",
                canonical_rel,
                len(rendered_html.encode("utf-8")),
                html_sha256,
                1,
                now_str,
            ),
        )

        for att_path, mime_type, orig_fn in attachment_files:
            att_rel = f"email/{att_path.name}"
            att_bytes = att_path.read_bytes()
            att_sha = hashlib.sha256(att_bytes).hexdigest()
            await db.execute(
                """
                INSERT OR REPLACE INTO crawl_artifacts (
                    artifact_id, crawl_id, session_id, record_id, source_kind,
                    role, mime_type, relative_path, size_bytes, sha256, verified, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"art_att_{msg_id}_{_safe_name(orig_fn)}",
                    crawl_id,
                    session_id,
                    record_id,
                    "email",
                    "email_attachment",
                    mime_type,
                    att_rel,
                    len(att_bytes),
                    att_sha,
                    1,
                    now_str,
                ),
            )

        return [{"record_id": record_id, "subject": subject, "path": canonical_rel}]

    async def _acquire_simulated(
        self,
        *,
        session_id: str,
        email_dir: Path,
        mode: AcquisitionMode,
        account_name: str,
        on_progress=None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Generate simulated sample emails for lab demo / offline test sessions."""
        if on_progress:
            await on_progress(
                SessionStatus.ACQUIRING,
                55.0,
                "Membuat sampel email Gmail (mode simulasi)…",
                acquisition_method="simulated_gmail",
            )

        sample_emails = [
            {
                "id": "sim_msg_001",
                "internalDate": str(int(time.time() * 1000) - 86400000),
                "labelIds": ["INBOX", "IMPORTANT"],
                "snippet": "Laporan mingguan koordinasi tim dan jadwal evaluasi proyek.",
                "payload": {
                    "mimeType": "text/html",
                    "headers": [
                        {"name": "Subject", "value": "Laporan Koordinasi Mingguan Proyek"},
                        {"name": "From", "value": "koordinator@instansi.go.id"},
                        {"name": "To", "value": account_name},
                        {"name": "Date", "value": "Wed, 19 Aug 2026 10:30:00 +0700"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"<p>Yth. Rekan Tim,</p><p>Terlampir ringkasan laporan koordinasi mingguan dan matriks capaian kerja tim. Silakan ditinjau sebelum rapat evaluasi.</p><p>Salam hormat,<br>Koordinator</p>"
                        ).decode()
                    },
                },
            },
            {
                "id": "sim_msg_002",
                "internalDate": str(int(time.time() * 1000) - 172800000),
                "labelIds": ["INBOX"],
                "snippet": "Informasi keamanan akun dan verifikasi masuk perangkat baru.",
                "payload": {
                    "mimeType": "text/html",
                    "headers": [
                        {"name": "Subject", "value": "Peringatan Keamanan Google: Perangkat Baru Terdeteksi"},
                        {"name": "From", "value": "no-reply@accounts.google.com"},
                        {"name": "To", "value": account_name},
                        {"name": "Date", "value": "Tue, 18 Aug 2026 14:15:00 +0700"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"<p>Akun Google Anda baru saja digunakan untuk login pada perangkat baru.</p><p>Jika ini adalah Anda, tidak ada tindakan lebih lanjut yang diperlukan.</p>"
                        ).decode()
                    },
                },
            },
            {
                "id": "sim_msg_003",
                "internalDate": str(int(time.time() * 1000) - 259200000),
                "labelIds": ["INBOX", "STARRED"],
                "snippet": "Konfirmasi pendaftaran tiket seminar teknologi digital dan keamanan siber.",
                "payload": {
                    "mimeType": "text/html",
                    "headers": [
                        {"name": "Subject", "value": "Konfirmasi Registrasi Seminar Cyber Security 2026"},
                        {"name": "From", "value": "event@cybersecurity.id"},
                        {"name": "To", "value": account_name},
                        {"name": "Date", "value": "Mon, 17 Aug 2026 09:00:00 +0700"},
                    ],
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            b"<p>Terima kasih telah mendaftar pada Seminar Nasional Keamanan Siber 2026.</p><p>E-ticket Anda telah aktif dan dapat ditunjukkan saat registrasi ulang.</p>"
                        ).decode()
                    },
                },
            },
        ]

        # Also create a small sample attachment image for sim_msg_001
        sample_img = email_dir / "sim_msg_001_lampiran_diagram.png"
        # 1x1 transparent PNG
        sample_img.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        all_records = []
        for msg in sample_emails:
            records = await self._process_message(
                client=None,
                session_id=session_id,
                email_dir=email_dir,
                msg_data=msg,
                account_name=account_name,
            )
            all_records.extend(records)

        return len(all_records), all_records
