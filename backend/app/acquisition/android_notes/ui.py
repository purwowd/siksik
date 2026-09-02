from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
COUNT_RE = re.compile(r"\b(\d{1,7})\s*(?:catatan|notes?|dipilih|selected)\b", re.IGNORECASE)
ISO_RE = re.compile(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])(?:[T\s]+([0-2]?\d):([0-5]\d)(?::([0-5]\d))?)?\b")
DMY_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[-/.](0?[1-9]|1[0-2])[-/.](20\d{2})(?:\s+([0-2]?\d):([0-5]\d))?\b")
MONTH_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})(?:\s+([0-2]?\d):([0-5]\d))?\b", re.IGNORECASE)
MONTH_FIRST_RE = re.compile(r"\b([A-Za-zÀ-ÿ]+)\s+(0?[1-9]|[12]\d|3[01]),?\s+(20\d{2})(?:\s+([0-2]?\d):([0-5]\d))?\b", re.IGNORECASE)
DAY_MONTH_SHORT_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])\s+([A-Za-zÀ-ÿ]+)(?:\s+([0-2]?\d):([0-5]\d))?\b", re.IGNORECASE)
MONTH_DAY_SHORT_RE = re.compile(r"\b([A-Za-zÀ-ÿ]+)\s+(0?[1-9]|[12]\d|3[01])(?:,)?(?:\s+([0-2]?\d):([0-5]\d))?\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b([0-2]?\d):([0-5]\d)\b")
MONTHS = {
    "januari": 1,
    "january": 1,
    "jan": 1,
    "februari": 2,
    "february": 2,
    "feb": 2,
    "maret": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "agustus": 8,
    "august": 8,
    "agu": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "desember": 12,
    "december": 12,
    "des": 12,
    "dec": 12,
}
EXCLUDED_CARD_LABELS = frozenset(
    {
        "add",
        "add note",
        "add a note",
        "all notes",
        "buat",
        "buat catatan",
        "catatan",
        "catatan baru",
        "create",
        "create note",
        "edit",
        "hapus",
        "delete",
        "folder",
        "folders",
        "kelola folder",
        "manage folders",
        "menu",
        "more",
        "lainnya",
        "new note",
        "notes",
        "search",
        "cari",
        "cari catatan",
        "search notes",
        "semua catatan",
        "settings",
        "pengaturan",
        "sort",
        "urutkan",
        "tambah catatan",
        "tambahkan catatan",
        "tap to create a note",
        "ketuk untuk buat catatan",
        "tasks",
        "tugas",
        "back",
        "kembali",
        "cancel",
        "batal",
    }
)
CARD_RESOURCE_TOKENS = frozenset(
    {
        "preview",
        "card",
        "cardview",
        "root_cardview",
        "item",
        "row",
    }
)
CARD_CONTROL_RESOURCE_TOKENS = frozenset(
    {
        "action",
        "add",
        "button",
        "create",
        "fab",
        "filter",
        "menu",
        "navigation",
        "new",
        "search",
        "setting",
        "settings",
        "sort",
        "toolbar",
    }
)
EDITOR_RESOURCE_TOKENS = (
    "content_editor",
    "edit_note",
    "memo_content",
    "memo_editor",
    "note_edit",
    "note_editor",
    "rich_editor",
)
LIST_RESOURCE_TOKENS = (
    "list_root",
    "list_view",
    "memo_list",
    "note_list",
    "note_recycler",
    "notes_list",
    "notes_recycler",
    "recycler_view",
)


@dataclass(frozen=True, slots=True)
class Bounds:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)


@dataclass(frozen=True, slots=True)
class UiNode:
    index: int
    parent: int | None
    depth: int
    text: str
    description: str
    package_name: str
    resource_id: str
    class_name: str
    clickable: bool
    scrollable: bool
    selected: bool
    checked: bool
    bounds: Bounds

    @property
    def label(self) -> str:
        values = [value.strip() for value in (self.text, self.description) if value.strip()]
        return " ".join(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class UiSnapshot:
    nodes: tuple[UiNode, ...]

    def descendants(self, parent: int) -> tuple[UiNode, ...]:
        output: list[UiNode] = []
        parents = {parent}
        for node in self.nodes:
            if node.parent in parents:
                output.append(node)
                parents.add(node.index)
        return tuple(output)

    def text_values(self) -> tuple[str, ...]:
        output: list[str] = []
        for node in self.nodes:
            for value in (node.text, node.description):
                cleaned = normalize_text(value, 4096)
                if cleaned and cleaned not in output:
                    output.append(cleaned)
        return tuple(output)

    def package_names(self) -> frozenset[str]:
        return frozenset(node.package_name for node in self.nodes if node.package_name)


@dataclass(frozen=True, slots=True)
class EditorContent:
    title: str
    body: str
    timestamp_raw: str | None
    source_modified_at: str | None
    folder: str | None


def normalize_text(value: str, limit: int) -> str:
    cleaned = "\n".join(
        line
        for line in (
            " ".join(part for part in raw.strip().split() if part)
            for raw in value.replace("\x00", "").replace("\r", "\n").split("\n")
        )
        if line
    )
    return cleaned[:limit]


def parse_ui(xml: str, max_nodes: int = 20_000) -> UiSnapshot:
    if not xml.strip():
        return UiSnapshot(())
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return UiSnapshot(())
    nodes: list[UiNode] = []

    def visit(element: ET.Element, parent: int | None, depth: int) -> None:
        if len(nodes) >= max_nodes:
            return
        bounds_match = BOUNDS_RE.fullmatch(element.attrib.get("bounds", ""))
        if bounds_match is None:
            bounds = Bounds(0, 0, 0, 0)
        else:
            bounds = Bounds(*(int(value) for value in bounds_match.groups()))
        index = len(nodes)
        nodes.append(
            UiNode(
                index=index,
                parent=parent,
                depth=depth,
                text=normalize_text(element.attrib.get("text", ""), 4096),
                description=normalize_text(element.attrib.get("content-desc", ""), 4096),
                package_name=element.attrib.get("package", "")[:255],
                resource_id=element.attrib.get("resource-id", "")[:512],
                class_name=element.attrib.get("class", "")[:256],
                clickable=element.attrib.get("clickable") == "true",
                scrollable=element.attrib.get("scrollable") == "true",
                selected=element.attrib.get("selected") == "true",
                checked=element.attrib.get("checked") == "true",
                bounds=bounds,
            )
        )
        for child in element:
            visit(child, index, depth + 1)

    visit(root, None, 0)
    return UiSnapshot(tuple(nodes))


def find_action(snapshot: UiSnapshot, aliases: tuple[str, ...]) -> UiNode | None:
    wanted = tuple(alias.casefold() for alias in aliases)
    exact: list[UiNode] = []
    partial: list[UiNode] = []
    for node in snapshot.nodes:
        label = node.label.casefold()
        resource = node.resource_id.casefold()
        if not label and not resource:
            continue
        if any(label == alias or resource.endswith(f"/{alias.replace(' ', '_')}") for alias in wanted):
            exact.append(node)
        elif any(alias in label or alias in resource for alias in wanted):
            partial.append(node)
    candidates = exact or partial
    if not candidates:
        return None
    return min(candidates, key=lambda node: (not node.clickable, node.bounds.area == 0, node.depth))


def selected_count(snapshot: UiSnapshot) -> int | None:
    values: list[int] = []
    for text in snapshot.text_values():
        for match in COUNT_RE.finditer(text):
            values.append(int(match.group(1)))
    return max(values) if values else None


def note_cards(
    snapshot: UiSnapshot,
    screen_size: tuple[int, int] | None = None,
) -> tuple[UiNode, ...]:
    cards: list[UiNode] = []
    for node in snapshot.nodes:
        if not node.clickable or node.bounds.area <= 0:
            continue
        if not _within_card_region(node, screen_size):
            continue
        resource_parts = _resource_parts(node.resource_id)
        if resource_parts & CARD_CONTROL_RESOURCE_TOKENS:
            continue
        label_values = [node.label]
        label_values.extend(child.label for child in snapshot.descendants(node.index))
        meaningful = [normalize_text(value, 4096) for value in label_values if value.strip()]
        meaningful = [value for value in meaningful if value.casefold() not in EXCLUDED_CARD_LABELS]
        joined = normalize_text("\n".join(dict.fromkeys(meaningful)), 8192)
        node_width = node.bounds.right - node.bounds.left
        node_height = node.bounds.bottom - node.bounds.top
        likely_card = bool(resource_parts & CARD_RESOURCE_TOKENS)
        if resource_parts & {"memo", "note"} and "list" not in resource_parts:
            likely_card = True
        card_sized_content = (
            node_width >= 200
            and node_height >= 60
            and len(meaningful) >= 2
        )
        if joined and (likely_card or card_sized_content):
            cards.append(node)
    filtered: list[UiNode] = []
    for card in cards:
        if any(
            other.index != card.index
            and other.index in {node.parent for node in snapshot.descendants(card.index)}
            and other.bounds.area < card.bounds.area
            for other in cards
        ):
            continue
        filtered.append(card)
    return tuple(sorted(filtered, key=lambda node: (node.bounds.top, node.bounds.left)))


def _resource_name(resource_id: str) -> str:
    return resource_id.rsplit("/", 1)[-1].casefold()


def _resource_parts(resource_id: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", _resource_name(resource_id)))


def _within_card_region(
    node: UiNode,
    screen_size: tuple[int, int] | None,
) -> bool:
    if screen_size is None:
        return True
    width, height = screen_size
    if width <= 0 or height <= 0:
        return True
    node_width = node.bounds.right - node.bounds.left
    node_height = node.bounds.bottom - node.bounds.top
    header_bottom = max(120, int(height * 0.08))
    footer_top = height - max(120, int(height * 0.06))
    if node.bounds.bottom <= header_bottom or node.bounds.top >= footer_top:
        return False
    if (
        node.bounds.left > width - 250
        and node_width < 200
        and node_height < 200
    ):
        return False
    return node.bounds.area < int(width * height * 0.85)


def card_signature(snapshot: UiSnapshot, card: UiNode) -> str:
    values = [card.label]
    values.extend(node.label for node in snapshot.descendants(card.index))
    return normalize_text("\n".join(dict.fromkeys(value for value in values if value)), 8192).casefold()


def looks_like_editor(snapshot: UiSnapshot) -> bool:
    if any(
        "edittext" in node.class_name.casefold() and node.bounds.area > 0
        for node in snapshot.nodes
    ):
        return True
    resources = tuple(_resource_name(node.resource_id) for node in snapshot.nodes)
    if any(
        token in resource
        for resource in resources
        for token in EDITOR_RESOURCE_TOKENS
    ):
        return True
    has_body = any(
        token in resource
        for resource in resources
        for token in ("body", "note_content", "note_text")
    )
    has_navigation_up = any(
        node.bounds.left < 220
        and node.bounds.top < 300
        and any(
            token in f"{node.label} {_resource_name(node.resource_id)}".casefold()
            for token in ("back", "kembali", "navigate up", "arrow", "home")
        )
        for node in snapshot.nodes
    )
    return has_body and has_navigation_up


def looks_like_note_list(
    snapshot: UiSnapshot,
    screen_size: tuple[int, int] | None = None,
) -> bool:
    if not snapshot.nodes or looks_like_editor(snapshot):
        return False
    if note_cards(snapshot, screen_size):
        return True
    for node in snapshot.nodes:
        resource = _resource_name(node.resource_id)
        class_name = node.class_name.casefold()
        if any(token in resource for token in LIST_RESOURCE_TOKENS):
            return True
        if node.bounds.area > 0 and class_name.endswith(
            ("recyclerview", "listview", "gridview")
        ):
            return True
    return False


def editor_content(snapshot: UiSnapshot, reference: datetime, max_chars: int) -> EditorContent:
    candidates: list[tuple[UiNode, str]] = []
    for node in snapshot.nodes:
        value = normalize_text(node.text or node.description, max_chars)
        if not value:
            continue
        resource = node.resource_id.casefold()
        class_name = node.class_name.casefold()
        if "edittext" in class_name or any(
            token in resource for token in ("title", "content", "body", "editor", "memo", "note_text")
        ):
            candidates.append((node, value))
    if not candidates:
        candidates = [
            (node, normalize_text(node.text, max_chars))
            for node in snapshot.nodes
            if node.text and node.bounds.area > 0
        ]
    title_candidates = [
        value
        for node, value in candidates
        if "title" in node.resource_id.casefold() and value
    ]
    title = title_candidates[0] if title_candidates else (candidates[0][1] if candidates else "")
    body_values = [
        value
        for node, value in candidates
        if value != title
        and not any(token in node.resource_id.casefold() for token in ("toolbar", "action_bar"))
    ]
    body = normalize_text("\n".join(dict.fromkeys(body_values)), max_chars)
    all_values = snapshot.text_values()
    timestamp_values = tuple(
        node.label
        for node in snapshot.nodes
        if node.label
        and any(
            token in f"{node.resource_id} {node.description}".casefold()
            for token in ("date", "time", "modified", "updated", "created", "tanggal", "waktu")
        )
    )
    timestamp_raw, modified = first_timestamp(timestamp_values or all_values, reference)
    folder = next(
        (
            value
            for value in all_values
            if re.search(r"(?i)\b(folder|notebook|catatan di|buku catatan)\b", value)
        ),
        None,
    )
    return EditorContent(title, body, timestamp_raw, modified, folder)


def first_timestamp(values: tuple[str, ...] | list[str], reference: datetime) -> tuple[str | None, str | None]:
    for value in values:
        parsed = parse_note_timestamp(value, reference)
        if parsed is not None:
            return value[:256], parsed.isoformat().replace("+00:00", "Z")
    return None, None


def parse_note_timestamp(value: str, reference: datetime) -> datetime | None:
    current = reference.astimezone(timezone.utc)
    text = " ".join(value.strip().split())
    lowered = text.casefold()
    relative_days = 0 if any(token in lowered for token in ("hari ini", "today")) else 1 if any(token in lowered for token in ("kemarin", "yesterday")) else None
    if relative_days is not None:
        clock = TIME_RE.search(text)
        hour = int(clock.group(1)) if clock else 0
        minute = int(clock.group(2)) if clock else 0
        target = current - timedelta(days=relative_days)
        return target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    match = ISO_RE.search(text)
    if match:
        year, month, day = (int(match.group(index)) for index in (1, 2, 3))
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
        return _safe_datetime(year, month, day, hour, minute, second)
    match = DMY_RE.search(text)
    if match:
        day, month, year = (int(match.group(index)) for index in (1, 2, 3))
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        return _safe_datetime(year, month, day, hour, minute, 0)
    match = MONTH_RE.search(text)
    if match:
        month = MONTHS.get(match.group(2).casefold())
        if month is not None:
            return _safe_datetime(
                int(match.group(3)),
                month,
                int(match.group(1)),
                int(match.group(4) or 0),
                int(match.group(5) or 0),
                0,
            )
    match = MONTH_FIRST_RE.search(text)
    if match:
        month = MONTHS.get(match.group(1).casefold())
        if month is not None:
            return _safe_datetime(
                int(match.group(3)),
                month,
                int(match.group(2)),
                int(match.group(4) or 0),
                int(match.group(5) or 0),
                0,
            )
    match = DAY_MONTH_SHORT_RE.search(text)
    if match:
        month = MONTHS.get(match.group(2).casefold())
        if month is not None:
            return _inferred_year_datetime(
                current,
                month,
                int(match.group(1)),
                int(match.group(3) or 0),
                int(match.group(4) or 0),
            )
    match = MONTH_DAY_SHORT_RE.search(text)
    if match:
        month = MONTHS.get(match.group(1).casefold())
        if month is not None:
            return _inferred_year_datetime(
                current,
                month,
                int(match.group(2)),
                int(match.group(3) or 0),
                int(match.group(4) or 0),
            )
    return None


def _safe_datetime(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime | None:
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None


def _inferred_year_datetime(
    reference: datetime,
    month: int,
    day: int,
    hour: int,
    minute: int,
) -> datetime | None:
    parsed = _safe_datetime(reference.year, month, day, hour, minute, 0)
    if parsed is None:
        return None
    if parsed > reference + timedelta(days=1):
        return _safe_datetime(reference.year - 1, month, day, hour, minute, 0)
    return parsed
