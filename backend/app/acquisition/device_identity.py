"""Optional identity hints from device documents — never overwrite operator input."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from app.acquisition.contact_identity import canonical_email, canonical_phone

_CV_FILENAME = re.compile(
    r"(?i)^(?:cv|curriculum\s*vitae|resume|riwayat\s*hidup)[\s._-]+(.+)$"
)
_COPY_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")
_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_NIK_LABELED = re.compile(
    r"(?i)\b(?:nik|no\.?\s*ktp|nomor\s*induk)[:\s-]*([0-9]{16})\b"
)
_ORG = re.compile(
    r"(?i)\b(?:di|at|pada)\s+((?:PT|CV|UD|Yayasan)\.?(?-i:(?:\s+[A-Z][A-Za-z0-9.&']+){1,5}))"
)
_PERSON_NAME = re.compile(r"^[A-Za-z][A-Za-z .']{2,60}$")


def _clean_filename_name(raw: str) -> str | None:
    stem = _FILE_EXT.sub("", raw.strip())
    stem = _COPY_SUFFIX.sub("", stem).strip(" ._")
    match = _CV_FILENAME.match(stem)
    if not match:
        return None
    name = " ".join(match.group(1).replace("_", " ").replace("-", " ").split())
    if not name or not _PERSON_NAME.match(name):
        return None
    return name.title() if name.isupper() else name


def _person_from_text_line(line: str) -> str | None:
    cleaned = " ".join(line.split())
    if not cleaned or len(cleaned) > 60:
        return None
    if not _PERSON_NAME.match(cleaned):
        return None
    words = cleaned.split()
    if len(words) < 2 or len(words) > 5:
        return None
    return cleaned.title() if cleaned.isupper() else cleaned


def hints_from_document(
    *,
    display_name: str | None,
    normalized_text: str | None = None,
) -> dict[str, Any]:
    names: list[str] = []
    from_file = _clean_filename_name(display_name or "")
    if from_file:
        names.append(from_file)
    text = (normalized_text or "").replace("\x00", " ")
    emails = []
    phones = []
    orgs = []
    nik = None
    if text.strip():
        first = text.strip().splitlines()[0] if text.strip() else ""
        from_text = _person_from_text_line(first)
        if from_text and from_text.casefold() not in {item.casefold() for item in names}:
            names.append(from_text)
        labeled = _NIK_LABELED.search(text)
        if labeled:
            nik = labeled.group(1)
        for match in _EMAIL.finditer(text):
            email = canonical_email(match.group(0))
            if email and email not in emails:
                emails.append(email)
        for token in re.findall(r"\+?\d[\d\s().-]{8,18}\d", text):
            phone = canonical_phone(token)
            if phone and phone not in phones:
                phones.append(phone)
        org_match = _ORG.search(text)
        if org_match:
            org = " ".join(org_match.group(1).split()).strip(" .,")
            if org:
                orgs.append(org)
    if not names and not emails and not phones and not nik and not orgs:
        return {}
    return {
        "kind": "document",
        "label": display_name or "dokumen",
        "names": names,
        "emails": emails,
        "phones": phones,
        "organization": orgs[0] if orgs else None,
        "nik": nik,
    }


def merge_device_identity_hints(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    names: list[str] = []
    emails: list[str] = []
    phones: list[str] = []
    orgs: list[str] = []
    niks: list[str] = []
    sources: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for item in items:
        if not item:
            continue
        label = str(item.get("label") or "dokumen")
        kind = str(item.get("kind") or "document")
        for name in item.get("names") or []:
            key = str(name).casefold()
            if key in seen_names:
                continue
            seen_names.add(key)
            names.append(str(name))
            sources.append({"name": str(name), "kind": kind, "label": label})
        for email in item.get("emails") or []:
            if email not in emails:
                emails.append(str(email))
        for phone in item.get("phones") or []:
            if phone not in phones:
                phones.append(str(phone))
        org = item.get("organization")
        if isinstance(org, str) and org.strip() and org not in orgs:
            orgs.append(org.strip())
        nik = item.get("nik")
        if isinstance(nik, str) and nik.isdigit() and len(nik) == 16 and nik not in niks:
            niks.append(nik)
    return {
        "names": names,
        "emails": emails,
        "phones": phones,
        "organizations": orgs,
        "nik_candidates": niks,
        "sources": sources,
    }
