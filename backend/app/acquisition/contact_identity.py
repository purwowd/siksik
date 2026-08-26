"""Normalize and cluster Android/iOS contact records by phone (and email)."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

_NON_PHONE = re.compile(r"[^\d+]")
_DIGITS = re.compile(r"\D")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def canonical_phone(value: str | None) -> str | None:
    """Collapse local/ID formats so 0812…, 62812…, and +62 812… are one key."""
    raw = (value or "").strip()
    if not raw:
        return None
    compact = _NON_PHONE.sub("", raw)
    if not compact:
        return None
    if compact.startswith("+"):
        rest = _DIGITS.sub("", compact[1:]).lstrip("0")
        if not rest:
            return None
        if rest.startswith("62"):
            return f"+{rest}"
        if rest.startswith("8") and len(rest) >= 8:
            return f"+62{rest}"
        return f"+{rest}"
    nums = _DIGITS.sub("", compact)
    if not nums:
        return None
    if nums.startswith("0") and len(nums) >= 9:
        return f"+62{nums.lstrip('0')}"
    if nums.startswith("62") and len(nums) >= 10:
        return f"+{nums}"
    if nums.startswith("8") and len(nums) >= 8:
        return f"+62{nums}"
    return f"+{nums}"


def canonical_email(value: str | None) -> str | None:
    text = (value or "").strip().casefold()
    if not text or not _EMAIL.match(text):
        return None
    return text


def _identity_values(items: Any, *, field: str) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, Mapping):
            raw = item.get("normalized_value") or item.get("value")
        else:
            raw = getattr(item, "normalized_value", None) or getattr(item, "value", None)
        if not isinstance(raw, str) or not raw.strip():
            continue
        if field == "phone":
            key = canonical_phone(raw)
        else:
            key = canonical_email(raw)
        if key and key not in out:
            out.append(key)
    return out


def contact_phones(metadata: Any) -> list[str]:
    if metadata is None:
        return []
    if isinstance(metadata, Mapping):
        return _identity_values(metadata.get("phones"), field="phone")
    return _identity_values(getattr(metadata, "phones", None), field="phone")


def contact_emails(metadata: Any) -> list[str]:
    if metadata is None:
        return []
    if isinstance(metadata, Mapping):
        return _identity_values(metadata.get("emails"), field="email")
    return _identity_values(getattr(metadata, "emails", None), field="email")


def contact_cluster_keys(metadata: Any) -> list[str]:
    phones = contact_phones(metadata)
    if phones:
        return [f"phone:{item}" for item in phones]
    emails = contact_emails(metadata)
    return [f"email:{item}" for item in emails]


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        parent = self._parent.setdefault(item, item)
        if parent != item:
            parent = self.find(parent)
            self._parent[item] = parent
        return parent

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self._parent[b] = a


def cluster_contact_ids(
    members: Sequence[tuple[str, Sequence[str]]],
) -> dict[str, str]:
    """Map contact id → keep id. Isolated records keep themselves."""
    forest = _UnionFind()
    key_owner: dict[str, str] = {}
    for contact_id, keys in members:
        forest.add(contact_id)
        for key in keys:
            previous = key_owner.get(key)
            if previous:
                forest.union(previous, contact_id)
            else:
                key_owner[key] = contact_id
    return {contact_id: forest.find(contact_id) for contact_id, _ in members}


def _meta_dict(raw: str | Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def annotate_contact_file_rows(
    rows: Iterable[tuple],
    *,
    source_index: int = 2,
    id_index: int = 0,
    meta_index: int = 9,
) -> list[tuple]:
    """Flag duplicate contact file tuples (id, …, meta_json). Keep all rows."""
    material = list(rows)
    members: list[tuple[str, list[str]]] = []
    parsed_meta: dict[str, dict[str, Any]] = {}
    for row in material:
        if str(row[source_index] or "").casefold() not in {"contact", "contacts"}:
            continue
        file_id = str(row[id_index])
        meta = _meta_dict(row[meta_index])
        parsed_meta[file_id] = meta
        keys = [str(item) for item in (meta.get("contact_cluster_keys") or []) if item]
        if not keys:
            keys = contact_cluster_keys(
                {
                    "phones": [
                        {"normalized_value": item}
                        for item in (meta.get("contact_phones") or [])
                    ],
                    "emails": [
                        {"normalized_value": item}
                        for item in (meta.get("contact_emails") or [])
                    ],
                }
            )
        members.append((file_id, keys))
    keep_of = cluster_contact_ids(members)
    updated: list[tuple] = []
    for row in material:
        row_list = list(row)
        file_id = str(row_list[id_index])
        meta = parsed_meta.get(file_id)
        if meta is None:
            updated.append(row)
            continue
        keep_id = keep_of.get(file_id, file_id)
        meta["contact_keep_id"] = keep_id
        meta["contact_duplicate"] = keep_id != file_id
        row_list[meta_index] = json.dumps(meta, ensure_ascii=False)
        updated.append(tuple(row_list))
    return updated
