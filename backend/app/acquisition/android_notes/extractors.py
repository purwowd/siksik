from __future__ import annotations

import html
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.android_notes.contracts import (
    NoteApp,
    NoteRecord,
    NotesExtractionResult,
    NotesFlow,
    NotesGateway,
    NotesPolicy,
    NotesRunBudget,
    NotesState,
    RemoteExport,
)
from app.acquisition.android_notes.ui import (
    UiSnapshot,
    card_signature,
    editor_content,
    find_action,
    first_timestamp,
    looks_like_editor,
    normalize_text,
    note_cards,
    parse_note_timestamp,
    parse_ui,
    selected_count,
)

TAG_RE = re.compile(r"<[^>]{1,512}>")
UTF16_TEXT_RE = re.compile(rb"(?:[\x20-\x7e\xa0-\xff]\x00){4,}")
XML_TEXT_RE = re.compile(r">([^<>]{2,})<")
SDOCX_ENTRY_RE = re.compile(r"\.(?:xml|json|txt|dat)$", re.IGNORECASE)
METADATA_NOISE = frozenset(
    {
        "document",
        "body",
        "text",
        "paragraph",
        "style",
        "content",
        "samsung notes",
        "application/xml",
        "application/json",
        "utf-8",
        "utf-16",
        "true",
        "false",
        "null",
    }
)


class GenericNotesExtractor:
    def __init__(self, gateway: NotesGateway) -> None:
        self._gateway = gateway

    async def extract(self, app: NoteApp, policy: NotesPolicy) -> NotesExtractionResult:
        if not await self._gateway.launch(app):
            return NotesExtractionResult((), NotesFlow.UI_WALK, NotesState.UNAVAILABLE, ("notes_launch_failed",))
        width, height = await self._gateway.screen_size()
        seen_cards: set[str] = set()
        records: list[NoteRecord] = []
        warnings: set[str] = set()
        skipped = 0
        stagnant = 0
        scrolls = 0
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        while len(records) < policy.max_notes and scrolls <= policy.max_list_scrolls and stagnant < 2:
            snapshot = await self._snapshot(policy)
            cards = note_cards(snapshot)
            pending = []
            for card in cards:
                signature = card_signature(snapshot, card)
                if signature and signature not in seen_cards:
                    pending.append((card, signature))
            if not pending:
                stagnant += 1
                if scrolls >= policy.max_list_scrolls:
                    break
                if not await self._gateway.swipe(
                    (width // 2, int(height * 0.78)),
                    (width // 2, int(height * 0.28)),
                    450,
                ):
                    warnings.add("notes_ui_input_denied")
                    break
                scrolls += 1
                await self._gateway.settle(0.7)
                continue
            stagnant = 0
            card, signature = pending[0]
            seen_cards.add(signature)
            visible_date = parse_note_timestamp(signature, datetime.now(timezone.utc))
            if visible_date is not None and visible_date < policy.not_before:
                skipped += 1
                continue
            if not await self._gateway.tap(*card.bounds.center):
                warnings.add("notes_ui_input_denied")
                break
            await self._gateway.settle(0.6)
            editor = await self._snapshot(policy)
            if not looks_like_editor(editor):
                skipped += 1
                warnings.add("notes_editor_unrecognized")
                if not await self._return_to_list(policy):
                    warnings.add("notes_list_restore_failed")
                    break
                continue
            record = await self._read_editor(
                app,
                editor,
                policy,
                observed_at,
                width,
                height,
            )
            if record is None:
                skipped += 1
            elif record.source_modified_at is not None and _parse_iso(record.source_modified_at) < policy.not_before:
                skipped += 1
            elif record.normalized_text:
                records.append(record)
            else:
                skipped += 1
            if not await self._return_to_list(policy):
                warnings.add("notes_list_restore_failed")
                break
        if len(records) >= policy.max_notes or scrolls >= policy.max_list_scrolls:
            warnings.add("notes_limit_reached")
        unique = tuple({record.stable_id: record for record in records}.values())
        state = NotesState.COMPLETE if unique and not warnings else NotesState.PARTIAL if unique or warnings else NotesState.UNAVAILABLE
        return NotesExtractionResult(unique, NotesFlow.UI_WALK, state, tuple(sorted(warnings)), skipped)

    async def _snapshot(self, policy: NotesPolicy) -> UiSnapshot:
        return parse_ui(await self._gateway.dump_ui(policy.max_ui_bytes))

    async def _return_to_list(self, policy: NotesPolicy) -> bool:
        for _ in range(4):
            if not await self._gateway.back():
                return False
            await self._gateway.settle(0.4)
            snapshot = await self._snapshot(policy)
            if note_cards(snapshot):
                return True
        return False

    async def _read_editor(
        self,
        app: NoteApp,
        initial: UiSnapshot,
        policy: NotesPolicy,
        observed_at: str,
        width: int,
        height: int,
    ) -> NoteRecord | None:
        reference = datetime.now(timezone.utc)
        values: list[str] = []
        title = ""
        timestamp_raw: str | None = None
        source_modified_at: str | None = None
        folder: str | None = None
        stagnant = 0
        snapshot = initial
        for _ in range(policy.max_editor_scrolls + 1):
            content = editor_content(snapshot, reference, policy.max_note_chars)
            title = title or content.title
            timestamp_raw = timestamp_raw or content.timestamp_raw
            source_modified_at = source_modified_at or content.source_modified_at
            folder = folder or content.folder
            before = len(values)
            for value in (content.body, content.title):
                cleaned = normalize_text(value, policy.max_note_chars)
                if cleaned and cleaned not in values:
                    values.append(cleaned)
            stagnant = stagnant + 1 if len(values) == before else 0
            if stagnant >= 2 or sum(len(value) for value in values) >= policy.max_note_chars:
                break
            if not await self._gateway.swipe(
                (width // 2, int(height * 0.76)),
                (width // 2, int(height * 0.3)),
                400,
            ):
                break
            await self._gateway.settle(0.4)
            snapshot = await self._snapshot(policy)
            if not looks_like_editor(snapshot):
                break
        body_values = [value for value in values if value != title]
        body = normalize_text("\n".join(body_values), policy.max_note_chars)
        if not title and body:
            first, _separator, remainder = body.partition("\n")
            title = first[:256]
            body = remainder or body
        if not title and not body:
            return None
        return NoteRecord(
            package_name=app.package_name,
            app_label=app.label,
            title=normalize_text(title, 512),
            body=body,
            observed_at=observed_at,
            source_modified_at=source_modified_at,
            timestamp_raw=timestamp_raw,
            folder=folder,
            extraction_method="android_notes_ui_walk",
        )


class SamsungNotesExtractor:
    def __init__(self, gateway: NotesGateway) -> None:
        self._gateway = gateway

    async def extract(self, app: NoteApp, policy: NotesPolicy) -> NotesExtractionResult:
        if not await self._gateway.launch(app):
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_launch_failed",))
        warnings: set[str] = set()
        baseline = {item.path: item for item in await self._gateway.list_exports()}
        snapshot = await self._snapshot(policy)
        drawer = find_action(snapshot, ("navigation drawer", "open navigation drawer", "menu"))
        if drawer is not None:
            if not await self._gateway.tap(*drawer.bounds.center):
                return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_ui_input_denied",))
            await self._gateway.settle(0.5)
            snapshot = await self._snapshot(policy)
            all_notes = find_action(snapshot, ("all notes", "semua catatan"))
            if all_notes is not None:
                await self._gateway.tap(*all_notes.bounds.center)
                await self._gateway.settle(0.6)
                snapshot = await self._snapshot(policy)
        cards = note_cards(snapshot)
        if not cards:
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_list_unrecognized",))
        if not await self._gateway.long_press(*cards[0].bounds.center):
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_ui_input_denied",))
        await self._gateway.settle(0.5)
        selection = await self._snapshot(policy)
        select_all = find_action(selection, ("select all", "pilih semua"))
        if select_all is None:
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_select_all_unavailable",))
        if not await self._gateway.tap(*select_all.bounds.center):
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_ui_input_denied",))
        await self._gateway.settle(0.4)
        selected_snapshot = await self._snapshot(policy)
        count = selected_count(selected_snapshot)
        if count is not None and count > policy.max_notes:
            warnings.add("notes_selection_exceeds_mode_limit")
        more = find_action(selected_snapshot, ("more options", "more", "lainnya"))
        if more is None or not await self._gateway.tap(*more.bounds.center):
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_export_menu_unavailable",))
        await self._gateway.settle(0.4)
        menu = await self._snapshot(policy)
        save_as = find_action(menu, ("save as file", "simpan sebagai file", "save as"))
        if save_as is None or not await self._gateway.tap(*save_as.bounds.center):
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, ("notes_export_action_unavailable",))
        await self._gateway.settle(0.5)
        format_snapshot = await self._snapshot(policy)
        export_format = find_action(format_snapshot, ("samsung notes file", "samsung notes", "sdocx"))
        if export_format is None:
            export_format = find_action(format_snapshot, ("text file", "text", "txt"))
            warnings.add("notes_sdocx_unavailable")
        if export_format is not None:
            await self._gateway.tap(*export_format.bounds.center)
            await self._gateway.settle(0.8)
        picker = await self._snapshot(policy)
        done = find_action(picker, ("done", "save", "simpan", "selesai"))
        if done is not None:
            if not await self._gateway.tap(*done.bounds.center):
                warnings.add("notes_ui_input_denied")
            await self._gateway.settle(1.2)
        changed: tuple[RemoteExport, ...] = ()
        for _ in range(6):
            after = await self._gateway.list_exports()
            changed = tuple(
                item
                for item in after
                if item.path not in baseline
                or baseline[item.path].size_bytes != item.size_bytes
                or baseline[item.path].modified_epoch_s != item.modified_epoch_s
            )
            if changed:
                break
            await self._gateway.settle(0.8)
        if not changed:
            warnings.add("notes_export_not_found")
            return NotesExtractionResult((), NotesFlow.SAMSUNG_EXPORT, NotesState.UNAVAILABLE, tuple(sorted(warnings)))
        budget = NotesRunBudget(policy)
        records: list[NoteRecord] = []
        skipped = 0
        with tempfile.TemporaryDirectory(prefix="siksik-notes-") as temporary:
            root = Path(temporary)
            for index, remote in enumerate(changed):
                if len(records) >= policy.max_notes:
                    warnings.add("notes_limit_reached")
                    break
                if not budget.reserve_export(remote.size_bytes):
                    skipped += 1
                    continue
                suffix = Path(remote.path).suffix.casefold()
                local = root / f"export-{index:04d}{suffix}"
                if not await self._gateway.pull_export(remote, local, min(policy.timeout_s, 120.0)):
                    warnings.add("notes_export_pull_failed")
                    skipped += 1
                    continue
                actual_size = local.stat().st_size
                accounted_size = remote.size_bytes or 0
                if actual_size > accounted_size and not budget.reserve_export(
                    actual_size - accounted_size
                ):
                    skipped += 1
                    continue
                if actual_size > policy.max_export_file_bytes:
                    warnings.add("notes_export_file_oversized")
                    skipped += 1
                    continue
                parsed = _parse_export(local, app, policy)
                for record in parsed:
                    if record.source_modified_at is not None and _parse_iso(record.source_modified_at) < policy.not_before:
                        skipped += 1
                        continue
                    if record.normalized_text:
                        records.append(record)
                    if len(records) >= policy.max_notes:
                        break
        warnings.update(budget.warnings)
        unique = tuple({record.stable_id: record for record in records}.values())
        if any(record.source_modified_at is None for record in unique):
            warnings.add("notes_timestamp_unknown")
        state = NotesState.COMPLETE if unique and not warnings else NotesState.PARTIAL if unique or warnings else NotesState.UNAVAILABLE
        return NotesExtractionResult(unique, NotesFlow.SAMSUNG_EXPORT, state, tuple(sorted(warnings)), skipped)

    async def _snapshot(self, policy: NotesPolicy) -> UiSnapshot:
        return parse_ui(await self._gateway.dump_ui(policy.max_ui_bytes))


def _parse_export(path: Path, app: NoteApp, policy: NotesPolicy) -> tuple[NoteRecord, ...]:
    try:
        if path.stat().st_size > policy.max_export_file_bytes:
            return ()
        with path.open("rb") as stream:
            raw = stream.read(policy.max_export_file_bytes + 1)
    except OSError:
        return ()
    if len(raw) > policy.max_export_file_bytes:
        return ()
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if path.suffix.casefold() == ".txt":
        text = _decode_text(raw)
        return _record_from_values((text,), app, policy, observed_at)
    values = _sdocx_values(path, raw, policy.max_export_file_bytes)
    return _record_from_values(values, app, policy, observed_at)


def _sdocx_values(path: Path, raw: bytes, limit: int) -> tuple[str, ...]:
    values: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            expanded = 0
            for info in archive.infolist()[:512]:
                if info.is_dir() or not SDOCX_ENTRY_RE.search(info.filename):
                    continue
                if info.file_size < 0 or info.file_size > limit or expanded + info.file_size > limit * 2:
                    break
                expanded += info.file_size
                with archive.open(info) as entry:
                    data = entry.read(min(info.file_size + 1, limit + 1))
                values.extend(_text_fragments(data))
    except (OSError, zipfile.BadZipFile, RuntimeError):
        values.extend(_text_fragments(raw))
    if not values:
        values.extend(_text_fragments(raw))
    return tuple(dict.fromkeys(value for value in values if value))


def _text_fragments(raw: bytes) -> list[str]:
    output: list[str] = []
    for encoding in ("utf-8", "utf-16-le"):
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        for match in XML_TEXT_RE.finditer(decoded):
            value = normalize_text(html.unescape(TAG_RE.sub(" ", match.group(1))), 200_000)
            if _useful_fragment(value):
                output.append(value)
    for match in UTF16_TEXT_RE.finditer(raw):
        try:
            value = normalize_text(match.group(0).decode("utf-16-le"), 200_000)
        except UnicodeDecodeError:
            continue
        if _useful_fragment(value):
            output.append(value)
    return list(dict.fromkeys(output))


def _useful_fragment(value: str) -> bool:
    lowered = value.casefold().strip()
    return len(lowered) >= 2 and lowered not in METADATA_NOISE and not lowered.startswith(("http://schemas.", "urn:", "xmlns"))


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return normalize_text(raw.decode(encoding), 200_000)
        except (UnicodeDecodeError, LookupError):
            continue
    return normalize_text(raw.decode("latin-1", errors="replace"), 200_000)


def _record_from_values(
    values: tuple[str, ...] | list[str],
    app: NoteApp,
    policy: NotesPolicy,
    observed_at: str,
) -> tuple[NoteRecord, ...]:
    cleaned = [normalize_text(value, policy.max_note_chars) for value in values]
    cleaned = [value for value in cleaned if _useful_fragment(value)]
    if not cleaned:
        return ()
    timestamp_raw, modified = first_timestamp(cleaned, datetime.now(timezone.utc))
    content = [value for value in cleaned if value != timestamp_raw]
    if not content:
        return ()
    title = next((value for value in content if 1 < len(value) <= 512), content[0][:512])
    body = normalize_text("\n".join(value for value in content if value != title), policy.max_note_chars)
    if not body and len(content[0]) > len(title):
        body = content[0][len(title) :].strip()
    return (
        NoteRecord(
            package_name=app.package_name,
            app_label=app.label,
            title=title,
            body=body,
            observed_at=observed_at,
            source_modified_at=modified,
            timestamp_raw=timestamp_raw,
            folder=None,
            extraction_method="android_notes_samsung_export",
        ),
    )


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
