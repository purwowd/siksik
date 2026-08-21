"""Visible-text helpers from WDA accessibility XML (iOS equivalent of Android a11y)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_WS = re.compile(r"\s+")
_AX_ID = re.compile(
    r"(ViewController|AXIdentifier|NavigationBar|tab-bar-item|"
    r"RootView|HeaderView|ImageView|Badge-?\d*|_tab|_button|_component)$"
)
_RESOURCEISH = re.compile(r"^[A-Za-z][A-Za-z0-9.]{6,}$")


def _is_ax_chrome(text: str) -> bool:
    compact = text.replace(" ", "")
    if _AX_ID.search(compact):
        return True
    lower = text.lower()
    if lower.startswith("vertical scroll bar") or lower.startswith("horizontal scroll bar"):
        return True
    if text.endswith("-button") or text.endswith("-bar") or "tab-bar-item" in lower:
        return True
    if " " not in text and _RESOURCEISH.match(text) and any(ch.isupper() for ch in text[1:]):
        return True
    return False


def _node_blob(element: ET.Element) -> str:
    parts: list[str] = []
    for key in ("label", "name", "value"):
        raw = (element.attrib.get(key) or "").strip()
        if raw and raw not in parts:
            parts.append(raw)
    return " ".join(parts)


def xml_lines(xml: str, *, skip: Sequence[str] = ()) -> list[str]:
    """Unique visible strings from a WDA page source, chrome filtered."""
    skip_l = {item.strip().lower() for item in skip if item.strip()}
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        if (element.attrib.get("visible") or "true").lower() == "false":
            continue
        text = _WS.sub(" ", _node_blob(element)).strip()
        if not text or len(text) > 500:
            continue
        if _is_ax_chrome(text):
            continue
        key = text.casefold()
        if key in seen or key in skip_l:
            continue
        seen.add(key)
        lines.append(text)
    return lines


def xml_tap_point(xml: str, labels: Sequence[str]) -> tuple[int, int] | None:
    """Center of the first visible node whose name/label matches a label.

    Exact (case-insensitive) match wins; otherwise a contained match. Prefers
    reasonably-sized controls so we do not tap the whole window.
    """
    needles = [item.strip().lower() for item in labels if item.strip()]
    if not needles:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    contained: tuple[int, int] | None = None
    for element in root.iter():
        if (element.attrib.get("visible") or "true").lower() == "false":
            continue
        blob = _node_blob(element).strip().lower()
        if not blob:
            continue
        try:
            x = int(float(element.attrib.get("x") or 0))
            y = int(float(element.attrib.get("y") or 0))
            width = int(float(element.attrib.get("width") or 0))
            height = int(float(element.attrib.get("height") or 0))
        except ValueError:
            continue
        if width < 8 or height < 8 or width > 2000 or height > 400:
            continue
        point = (x + width // 2, y + height // 2)
        if any(blob == needle for needle in needles):
            return point
        if contained is None and any(needle in blob for needle in needles):
            contained = point
    return contained


_X_STATUS_LINE = re.compile(
    r"(?:Pinned\.\s*)?.{3,400}?\.\s+\d{1,2}\s+[A-Za-z]+\s+20\d{2}\.\s+[\d,.]+\s+Views",
)
_X_SKIP_LINE = (
    "profileheader",
    "navigation",
    "tab-bar",
    "compose post",
    "who to follow",
    "get verified",
)
_FB_COMPOSER = (
    "what's on your mind",
    "what is on your mind",
    "new post",
    "composer-view",
    "ada apa hari ini",
)


def looks_like_fb_composer(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _FB_COMPOSER)


def extract_x_status_cells(xml: str) -> list[str]:
    """One tweet per XCUIElementTypeCell label — Android TEXT_ONLY row equivalent."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if "Cell" not in tag:
            continue
        if (element.attrib.get("visible") or "true").lower() == "false":
            continue
        label = _WS.sub(" ", (element.attrib.get("label") or "")).strip()
        if " views" not in label.lower():
            continue
        if not _X_STATUS_LINE.search(label):
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(label)
    return found


def extract_x_statuses(text: str) -> list[str]:
    """Pull individual tweet/reply bodies from XML cells or dumped X page text."""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        cells = extract_x_status_cells(text)
        if cells:
            return cells
    found: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = _WS.sub(" ", raw).strip()
        low = line.lower()
        if " views" not in low:
            continue
        if any(marker in low for marker in _X_SKIP_LINE):
            continue
        match = _X_STATUS_LINE.search(line)
        if not match:
            continue
        chunk = match.group(0).strip()
        key = chunk.casefold()
        if len(chunk) < 24 or key in seen:
            continue
        seen.add(key)
        found.append(chunk)
    return found


_FB_COMMENT_NOISE = {
    "comments",
    "komentar",
    "comments and reactions",
    "komentar dan reaksi",
    "activity log",
    "log aktivitas",
    "archive",
    "trash",
    "no items",
    "tidak ada item",
    "back",
    "search",
    "learn more",
    "not all of your items may appear here. learn more.",
}


def looks_like_fb_comments_empty(xml_or_text: str) -> bool:
    low = xml_or_text.lower()
    if looks_like_fb_activity_log_hub(xml_or_text):
        return False
    return any(
        marker in low
        for marker in (
            "no items",
            "tidak ada item",
            "no comments",
            "belum ada komentar",
            "you haven't commented",
            "you haven’t commented",
        )
    )


def looks_like_fb_activity_log_hub(xml_or_text: str) -> bool:
    """iOS Activity log accordion (Welcome + groupings), not the comments list."""
    low = xml_or_text.lower()
    return (
        "youractivitygrouping-section-item" in low
        or "personalinfogrouping-section-item" in low
        or "welcome to activity log" in low
    )


def looks_like_fb_groups_tab(xml_or_text: str) -> bool:
    """Tab bar Groups was tapped — dump 38490b13 after Comments overlapped the tab."""
    low = xml_or_text.lower()
    return 'name="groups"' in low and (
        "create group" in low or "open groups settings" in low or "discover" in low
    )


def xml_tab_bar_top(xml: str) -> int:
    """Top Y of the iOS tab bar, or 734 if not found (iPhone dump default)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return 734
    top = 10_000
    for element in root.iter():
        name = (element.attrib.get("name") or "")
        if not name.startswith("tab-bar-item"):
            continue
        try:
            y = int(float(element.attrib.get("y") or 0))
        except ValueError:
            continue
        if y > 400:
            top = min(top, y)
    return 734 if top == 10_000 else top


def hit_overlaps_tab_bar(hit: dict[str, Any], tab_top: int) -> bool:
    bottom = int(hit["y"]) + int(hit["height"])
    mid = int(hit["y"]) + int(hit["height"]) // 2
    return mid >= tab_top - 4 or bottom > tab_top + 2


def looks_like_fb_comments_surface(xml_or_text: str) -> bool:
    """True on the comments/reactions LIST, not Activity log accordion or Groups tab."""
    if looks_like_fb_groups_tab(xml_or_text):
        return False
    if looks_like_fb_activity_log_hub(xml_or_text):
        return False
    if looks_like_fb_comments_empty(xml_or_text):
        return True
    hit = xml_find_control(
        xml_or_text,
        labels=(
            "Comments",
            "Komentar",
            "Comments and reactions",
            "Komentar dan reaksi",
            "Likes and reactions",
            "Suka dan reaksi",
        ),
        include_hidden=False,
        exact=True,
    )
    return hit is not None and int(hit["y"]) < 180


def xml_find_control(
    xml: str,
    *,
    labels: Sequence[str] = (),
    names: Sequence[str] = (),
    include_hidden: bool = True,
    exact: bool = False,
) -> dict[str, Any] | None:
    """Find a control by label prefix or accessibility name fragment.

    Prefers visible Buttons. Hidden matches (y < 0) are returned so the caller
    can scroll toward them — iOS Activity log parks 'Your Facebook activity' above the fold.
    """
    label_needles = [item.strip().lower() for item in labels if item.strip()]
    name_needles = [item.strip().lower() for item in names if item.strip()]
    if not label_needles and not name_needles:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    visible_hit: dict[str, Any] | None = None
    hidden_hit: dict[str, Any] | None = None
    for element in root.iter():
        visible = (element.attrib.get("visible") or "true").lower() != "false"
        if not include_hidden and not visible:
            continue
        label = (element.attrib.get("label") or "").strip().lower()
        name = (element.attrib.get("name") or "").strip().lower()
        value = (element.attrib.get("value") or "").strip().lower()
        matched = False
        for needle in label_needles:
            if label == needle or name == needle or value == needle:
                matched = True
                break
            if not exact and (label.startswith(f"{needle},") or label.startswith(f"{needle} ")):
                matched = True
                break
        if not matched:
            for needle in name_needles:
                if needle in name:
                    matched = True
                    break
        if not matched:
            continue
        try:
            x = int(float(element.attrib.get("x") or 0))
            y = int(float(element.attrib.get("y") or 0))
            width = int(float(element.attrib.get("width") or 0))
            height = int(float(element.attrib.get("height") or 0))
        except ValueError:
            continue
        if width < 8 or height < 8 or width > 2000 or height > 500:
            continue
        hit = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "visible": visible,
            "label": (element.attrib.get("label") or "").strip(),
            "name": (element.attrib.get("name") or "").strip(),
            "is_button": element.tag.endswith("Button"),
        }
        if visible:
            if visible_hit is None or (hit["is_button"] and not visible_hit["is_button"]):
                visible_hit = hit
        elif hidden_hit is None or (hit["is_button"] and not hidden_hit["is_button"]):
            hidden_hit = hit
    return visible_hit or hidden_hit


def _fb_node_label(element: ET.Element) -> str:
    return _WS.sub(
        " ",
        (element.attrib.get("label") or element.attrib.get("value") or element.attrib.get("name") or ""),
    ).strip()


def extract_fb_feed_items(xml: str) -> list[str]:
    """One wall post per feed-unit: title + timestamp + body (Android TEXT_ONLY)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for unit in root.iter():
        name = (unit.attrib.get("name") or "").strip().lower()
        if name != "feed-unit":
            continue
        if (unit.attrib.get("visible") or "true").lower() == "false":
            continue
        title = ""
        stamp = ""
        bodies: list[str] = []
        for child in unit.iter():
            cname = (child.attrib.get("name") or "").strip().lower()
            label = _fb_node_label(child)
            if not label or looks_like_fb_composer(label) or _is_ax_chrome(label):
                continue
            if "header-title" in cname:
                title = label
            elif "header-timestamp" in cname:
                stamp = label
            elif child.tag.endswith("StaticText") and len(label) >= 8:
                if label not in {title, stamp} and label not in bodies:
                    bodies.append(label)
        chunks = [part for part in (title, stamp, *bodies) if part]
        if not chunks:
            continue
        text = " · ".join(chunks)
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found


def extract_fb_comment_items(xml: str) -> list[str]:
    """Activity-log comment rows (Android own_comments TEXT_ONLY). Empty list if none."""
    if looks_like_fb_activity_log_hub(xml) or looks_like_fb_comments_empty(xml):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for line in xml_lines(xml):
        key = line.strip().casefold()
        if len(key) < 2 or key in _FB_COMMENT_NOISE:
            continue
        if key in seen:
            continue
        seen.add(key)
        found.append(line.strip())
    return found


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


async def tap_label(session: Any, labels: Sequence[str], *, timeout: float = 8.0) -> bool:
    """Poll page source and tap the first matching visible control."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        source = getattr(session, "source_xml", None) or session.source_xml
        xml = await source()
        point = xml_tap_point(xml, labels)
        if point is not None:
            tap = getattr(session, "tap_xy", None) or session.tap_xy
            await tap(point[0], point[1])
            return True
        await session.sleep(0.35)
    return False


async def capture_text_pages(
    session: Any,
    out_dir: Path,
    *,
    prefix: str,
    max_pages: int,
    skip: Sequence[str] = (),
    take_screenshot: bool = True,
    scroll_direction: str = "down",
) -> list[dict]:
    """Scroll a surface and dump accessibility text (Android TEXT_ONLY equivalent)."""
    rows: list[dict] = []
    previous = ""
    identical = 0
    source = getattr(session, "source_xml", None) or session.source_xml
    shot_fn = getattr(session, "screenshot", None) or session.screenshot
    feed = getattr(session, "scroll_feed", None)
    for index in range(1, max_pages + 1):
        xml = await source()
        (out_dir / f"{prefix}_{index:02d}.xml").write_text(xml, encoding="utf-8")
        lines = xml_lines(xml, skip=skip)
        text = "\n".join(lines)
        screenshot_name = ""
        if take_screenshot:
            path = out_dir / f"{prefix}_{index:02d}.png"
            await shot_fn(path)
            screenshot_name = path.name
        (out_dir / f"{prefix}_{index:02d}.txt").write_text(text, encoding="utf-8")
        rows.append({"index": index, "screenshot": screenshot_name, "text": text, "lines": lines})
        if previous and text == previous:
            identical += 1
            if identical >= 2:
                break
        else:
            identical = 0
        previous = text
        if index >= max_pages:
            break
        if feed is not None and scroll_direction == "down":
            await feed(distance=0.62, duration=0.35)
        else:
            await session.scroll(scroll_direction, distance=0.62, duration=0.35)
        await session.sleep(0.85)
    write_jsonl(out_dir / f"{prefix}.jsonl", rows)
    return rows


def mirror_to_temp_crawl(out_dir: Path, flow: str, *, session_id: str = "") -> Path | None:
    """Copy a flow output tree to siksik/temp_crawl/ios_wda/ for debugging."""
    import os
    import shutil
    from datetime import datetime, timezone

    env = os.environ.get("IOS_TEMP_CRAWL_DIR", "").strip()
    root = Path(env) if env else Path(__file__).resolve().parents[3] / "temp_crawl" / "ios_wda"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = session_id.strip() or stamp
    dest = root / label / flow
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(out_dir, dest)
        return dest
    except OSError:
        return None


def env_int(name: str, default: int, *, lo: int = 1, hi: int = 40) -> int:
    import os

    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(lo, min(hi, value))


xml_lines = xml_lines
env_int = env_int
extract_x_statuses = extract_x_statuses
looks_like_fb_composer = looks_like_fb_composer
