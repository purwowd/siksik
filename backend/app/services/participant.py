"""Validasi & helper identitas peserta seleksi."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.db import db
from app.models.session import ParticipantInput

_NIK_RE = re.compile(r"^\d{16}$")


def normalize_registration_no(value: str) -> str:
    return value.strip().upper()


def participant_dict(participant: ParticipantInput) -> dict[str, Any]:
    nik = (participant.nik or "").strip() or None
    if nik and not _NIK_RE.match(nik):
        raise ValueError("NIK harus 16 digit angka")
    return {
        "full_name": participant.full_name.strip(),
        "registration_no": normalize_registration_no(participant.registration_no),
        "nik": nik,
        "organization": (participant.organization or "").strip() or None,
    }


def participant_display_label(participant: ParticipantInput | dict[str, Any]) -> str:
    if isinstance(participant, ParticipantInput):
        name = participant.full_name.strip()
        reg = normalize_registration_no(participant.registration_no)
    else:
        name = str(participant.get("full_name") or "").strip()
        reg = normalize_registration_no(str(participant.get("registration_no") or ""))
    if name and reg:
        return f"{name} · {reg}"
    return name or reg


def session_focus_label(label: str, participant_json: str | None) -> str:
    """Label fokus kasus: identitas peserta jika ada, else label sesi."""
    try:
        data = json.loads(participant_json or "{}")
    except (TypeError, json.JSONDecodeError):
        data = {}
    if isinstance(data, dict):
        name = str(data.get("full_name") or "").strip()
        reg = normalize_registration_no(str(data.get("registration_no") or ""))
        if name and reg:
            return f"{name} · {reg}"
        if name:
            return name
        if reg:
            return reg
    return (label or "").strip() or "—"


async def find_registration_conflict(
    registration_no: str,
    *,
    exclude_session_id: str | None = None,
) -> dict[str, Any] | None:
    """Cari sesi aktif/completed hari ini (UTC) dengan no. peserta sama.

    Sesi failed/cancelled tidak memblokir retry calon yang sama.
    """
    reg = normalize_registration_no(registration_no)
    rows = await db.fetchall(
        """
        SELECT id, label, participant_json, created_at, status
        FROM sessions
        WHERE status NOT IN ('cancelled', 'failed')
          AND date(substr(created_at, 1, 10)) = date('now')
        """
    )
    for row in rows:
        if exclude_session_id and row["id"] == exclude_session_id:
            continue
        try:
            data = json.loads(row["participant_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        existing = normalize_registration_no(str(data.get("registration_no") or ""))
        if existing and existing == reg:
            return {
                "id": row["id"],
                "label": row["label"],
                "status": row["status"],
            }
    return None
