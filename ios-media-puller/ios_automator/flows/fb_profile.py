"""Facebook: Home screenshot → Profile → name / friends / posts / followers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from lib.apps import resolve_bundle_id
from lib.ax_text import (
    capture_text_pages,
    env_int,
    extract_fb_comment_items,
    extract_fb_feed_items,
    hit_overlaps_tab_bar,
    looks_like_fb_activity_log_hub,
    looks_like_fb_comments_empty,
    looks_like_fb_comments_surface,
    looks_like_fb_composer,
    looks_like_fb_groups_tab,
    mirror_to_temp_crawl,
    tap_label,
    write_jsonl,
    xml_find_control,
    xml_tab_bar_top,
)
from lib.run_log import fb_done, fb_phase
from lib.session import AutomatorSession, default_output_dir

logger = logging.getLogger("ios_automator.fb_profile")

SELECTORS = Path(__file__).resolve().parents[1] / "appium" / "selectors.json"

SKIP_NAMES = frozenset(
    {
        "facebook",
        "home",
        "beranda",
        "profile",
        "profil",
        "your profile",
        "menu",
        "friends",
        "teman",
        "followers",
        "pengikut",
        "following",
        "posts",
        "postingan",
        "see all",
        "lihat semua",
        "edit profile",
        "edit profil",
        "edit profile picture",
        "edit cover photo button",
        "cover photo",
        "profile picture",
        "profile tools",
        "search facebook",
        "add to story",
        "create",
        "buat",
        "photos",
        "reels",
        "notifications",
        "groups",
        "manage posts",
        "",
    }
)

PROFILE_TAB_LABELS = (
    "your profile",
    "profil anda",
    "profil kamu",
    "profile",
    "profil",
)

# "1,411 friends" | "1.4K friends" | "1,4 ribu teman" | "234 posts"
STAT_RE = re.compile(
    r"^([\d.,]+)\s*"
    r"(?:([KMBkmb])|thousand|ribu|million|juta)?\s*"
    r"(friends?|teman|followers?|pengikut|following|mengikuti|posts?|postingan)\b",
    re.IGNORECASE,
)
NUMBER_ONLY_RE = re.compile(r"^[\d.,]+$")


def _load_selectors() -> dict:
    with SELECTORS.open(encoding="utf-8") as fh:
        return json.load(fh)["facebook"]


def _normalize_locale_number(raw: str) -> float:
    text = raw.strip().lower().replace("\u00a0", " ")
    word_mult = {
        "k": 1_000,
        "thousand": 1_000,
        "ribu": 1_000,
        "m": 1_000_000,
        "million": 1_000_000,
        "juta": 1_000_000,
        "b": 1_000_000_000,
    }
    m = re.match(
        r"^([\d.,]+)\s*([kmb]|thousand|ribu|million|juta)?$",
        text,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError(f"bukan angka: {raw!r}")
    num_s = m.group(1)
    suffix = (m.group(2) or "").lower()
    mult = float(word_mult.get(suffix, 1))

    if "," in num_s and "." in num_s:
        if num_s.rfind(",") > num_s.rfind("."):
            num_s = num_s.replace(".", "").replace(",", ".")
        else:
            num_s = num_s.replace(",", "")
    elif "," in num_s:
        parts = num_s.split(",")
        num_s = "".join(parts[:-1]) + "." + parts[-1] if len(parts[-1]) <= 2 else num_s.replace(",", "")
    elif "." in num_s:
        parts = num_s.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit():
            num_s = "".join(parts)
    return float(num_s) * mult


def _parse_count(raw: str) -> int | str:
    try:
        return int(round(_normalize_locale_number(raw)))
    except ValueError:
        return raw.strip()


def _node_text(node: ET.Element) -> str:
    for key in ("value", "label", "name"):
        val = (node.attrib.get(key) or "").strip()
        if val:
            return val
    return ""


async def _tap_any(
    session: AutomatorSession,
    block: dict,
    *,
    timeout: float = 18.0,
) -> None:
    strategies = block.get("strategies", [])
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        for strat in strategies:
            using = strat["using"]
            value = strat["value"]
            try:
                await session.tap(value, using=using)
                logger.info("tapped [%s] %s", using, value)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
        await session.sleep(0.35)

    fb = block.get("fallback_xy")
    if fb:
        xml = await session.source_xml()
        if looks_like_fb_composer(xml) or "review audience" in xml.lower():
            logger.warning("skip fallback_xy — interstitial/composer terlihat")
            return
        size = await session.window_size()
        x = int(size["width"] * float(fb["x_ratio"]))
        y = int(size["height"] * float(fb["y_ratio"]))
        logger.warning("selector gagal, tap_xy fallback (%d, %d)", x, y)
        await session.tap_xy(x, y)
        return

    raise RuntimeError(f"Element not found: {strategies!r}; last={last}")


async def _element_id(session: AutomatorSession, using: str, value: str) -> str | None:
    try:
        await session.ensure_session()
        eid = await session._call(  # noqa: SLF001
            session.client.find_element,
            using,
            value,
            session.session_id,
        )
        return eid or None
    except Exception:  # noqa: BLE001
        return None


def _find_tab_bar_button(xml: str, labels: tuple[str, ...]) -> tuple[int, int] | None:
    """Cari tombol tab bar by label → kembalikan pusat (x, y)."""
    root = ET.fromstring(xml)
    wanted = [item.lower() for item in labels]
    hits: list[tuple[int, int, int, int]] = []
    max_bottom = 0
    for node in root.iter():
        try:
            x = int(node.attrib.get("x", "0") or 0)
            y = int(node.attrib.get("y", "0") or 0)
            w = int(node.attrib.get("width", "0") or 0)
            h = int(node.attrib.get("height", "0") or 0)
        except ValueError:
            continue
        max_bottom = max(max_bottom, y + h)
        if not node.tag.endswith("Button"):
            continue
        label = (node.attrib.get("label") or "").strip().lower()
        name = (node.attrib.get("name") or "").strip().lower()
        if not any(
            label == needle or name == needle or needle in label or (needle in name and "tab-bar" not in name)
            for needle in wanted
        ):
            continue
        if w < 20 or h < 20:
            continue
        hits.append((x, y, w, h))
    floor = max(int(max_bottom * 0.72), 1)
    for x, y, w, h in hits:
        if y >= floor:
            return x + w // 2, y + h // 2
    return None


async def _wait_and_tap_profile(session: AutomatorSession, fb: dict) -> None:
    """Tap tab Profile. FB iOS pakai label 'Your profile' (id tab-bar-item-* dinamis)."""
    block = fb["profile_tab"]
    strategies = block.get("strategies", [])
    max_wait = float(fb.get("profile_tab_wait_sec", 12.0))
    poll = float(fb.get("profile_tab_poll_sec", 0.25))
    deadline = time.time() + max_wait
    labels = tuple(fb.get("profile_tab_labels", list(PROFILE_TAB_LABELS)))

    while time.time() < deadline:
        # 1) Dari page source — paling andal untuk label "Your profile"
        try:
            xml = await session.source_xml()
            point = _find_tab_bar_button(xml, labels)
            if point:
                x, y = point
                await session.tap_xy(x, y)
                logger.info("profile tab via page source — tap_xy (%d, %d)", x, y)
                return
        except Exception as exc:  # noqa: BLE001
            logger.debug("page source tab lookup: %s", exc)

        # 2) Selector WDA biasa
        for strat in strategies:
            using = strat["using"]
            value = strat["value"]
            if await _element_id(session, using, value):
                await session.tap(value, using=using)
                logger.info("profile tab ready — tapped [%s] %s", using, value)
                return
        await session.sleep(poll)

    logger.warning("profile tab belum ready dalam %.1fs — fallback tap_any", max_wait)
    await _tap_any(session, block)


def _looks_like_display_name(text: str) -> bool:
    t = text.strip()
    if not t or len(t) < 2 or len(t) > 60:
        return False
    low = t.lower()
    if low in SKIP_NAMES:
        return False
    if "search" in low or "facebook" in low:
        return False
    if low.startswith(("tap ", "add ", "create ", "see ", "find ", "edit ", "manage ")):
        return False
    if any(k in low for k in ("friend", "follower", "post", "teman", "pengikut", "postingan", "story", "photo")):
        return False
    if t.startswith("http"):
        return False
    if NUMBER_ONLY_RE.match(t):
        return False
    return t[0].isalpha()


def _assign_stat(kind: str, value: int | str, info: dict[str, Any]) -> None:
    kind = kind.lower()
    if kind.startswith("friend") or kind == "teman":
        if info.get("friends") in ("", None):
            info["friends"] = value
    elif kind.startswith("follower") or kind == "pengikut":
        if info.get("followers") in ("", None):
            info["followers"] = value
    elif kind.startswith("following") or kind == "mengikuti":
        if info.get("following") in ("", None):
            info["following"] = value
    elif kind.startswith("post"):
        if info.get("posts") in ("", None):
            info["posts"] = value


def _read_profile_info(xml: str) -> dict[str, Any]:
    root = ET.fromstring(xml)
    info: dict[str, Any] = {
        "display_name": "",
        "friends": "",
        "posts": "",
        "followers": "",
        "following": "",
    }

    # Stats dari teks "N friends / N posts / N followers" (button name/label)
    for node in root.iter():
        for key in ("name", "label", "value"):
            field = (node.attrib.get(key) or "").strip()
            if not field:
                continue
            m = STAT_RE.match(field)
            if not m:
                continue
            num_raw = m.group(1)
            suffix = m.group(2) or ""
            kind = m.group(3)
            low = field.lower()
            if re.search(r"\b(thousand|ribu)\b", low):
                parsed = _parse_count(f"{num_raw} thousand")
            elif re.search(r"\b(million|juta)\b", low):
                parsed = _parse_count(f"{num_raw} million")
            elif suffix and re.fullmatch(r"[KMBkmb]", suffix):
                parsed = _parse_count(f"{num_raw}{suffix}")
            else:
                parsed = _parse_count(num_raw)
            _assign_stat(kind, parsed, info)

    # Nama: Button name==label di area header (dekat foto profil), contoh "Deni Irwan"
    name_candidates: list[tuple[int, str]] = []
    for node in root.iter():
        if not node.tag.endswith("Button"):
            continue
        name = (node.attrib.get("name") or "").strip()
        label = (node.attrib.get("label") or "").strip()
        if not name or name != label or not _looks_like_display_name(name):
            continue
        y = int(node.attrib.get("y", "0") or 0)
        if 100 <= y <= 280:
            name_candidates.append((y, name))
    if name_candidates:
        name_candidates.sort(key=lambda c: c[0])
        info["display_name"] = name_candidates[0][1]

    if not info["display_name"]:
        for node in root.iter():
            if not node.tag.endswith("StaticText"):
                continue
            text = _node_text(node)
            if not _looks_like_display_name(text):
                continue
            y = int(node.attrib.get("y", "0") or 0)
            h = int(node.attrib.get("height", "0") or 0)
            if 40 <= y <= 320 and h >= 18:
                info["display_name"] = text
                break

    return info


async def _on_profile_screen(session: AutomatorSession) -> bool:
    xml = await session.source_xml()
    low = xml.lower()
    return (
        'label="your profile"' in low and "selected" in low
    ) or ("edit profile" in low and "2 posts" in low) or (
        "edit profile" in low and "profile picture" in low and "cover photo" in low
    )


# Locale-stable labels + accessibility names (iOS Facebook varies EN/ID; names stay).
_PROFILE_TOOLS_LABELS = (
    "Profile Tools",
    "Alat Profil",
    "More profile settings",
)
_PROFILE_TOOLS_NAMES = ("profile-tools-action-button",)
_ACTIVITY_LOG_LABELS = (
    "Activity log",
    "Log aktivitas",
    "See activity log",
    "Lihat log aktivitas",
)
_YOUR_ACTIVITY_LABELS = (
    "Your Facebook activity",
    "Aktivitas Facebook Anda",
    "Your activity",
    "Aktivitas Anda",
)
_YOUR_ACTIVITY_NAMES = ("YOURACTIVITYGROUPING-SECTION-ITEM",)
_COMMENTS_REACTIONS_LABELS = (
    "Comments and reactions",
    "Komentar dan reaksi",
    "Comments & reactions",
)
_COMMENTS_REACTIONS_NAMES = ("YOURACTIVITYCOMMENTSANDREACTIONSSCHEMA-SECTION-ITEM",)
_COMMENTS_CLUSTER_NAMES = ("COMMENTSCLUSTER",)
_LIKES_CLUSTER_NAMES = ("LIKEDCONTENT",)
_MANAGE_COMMENTS_NAMES = ("YOURACTIVITYCOMMENTSANDREACTIONSSCHEMA-LANDING-BUTTON",)
_MANAGE_COMMENTS_LABELS = (
    "Manage comments and reactions",
    "Kelola komentar dan reaksi",
)
_COMMENTS_LABELS = ("Comments", "Komentar")
_REACTIONS_LABELS = (
    "Likes and reactions",
    "Suka dan reaksi",
    "Reactions",
    "Reaksi",
)


async def _tap_wda(
    session: AutomatorSession,
    *,
    names: tuple[str, ...] = (),
    labels: tuple[str, ...] = (),
) -> bool:
    """Tap via WDA find+click so off-screen Activity log rows still work."""
    for name in names:
        for using in ("name", "accessibility id"):
            try:
                await session.tap(name, using=using)
                logger.info("FB WDA tap [%s] %s", using, name)
                return True
            except Exception:  # noqa: BLE001
                continue
        try:
            await session.tap(f"//*[@name='{name}']", using="xpath")
            logger.info("FB WDA tap [xpath name] %s", name)
            return True
        except Exception:  # noqa: BLE001
            continue
    for label in labels:
        try:
            await session.tap(f"//*[@label='{label}']", using="xpath")
            logger.info("FB WDA tap [xpath label] %s", label)
            return True
        except Exception:  # noqa: BLE001
            continue
        try:
            await session.tap(label, using="name")
            logger.info("FB WDA tap [name/label] %s", label)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _dump_xml(session: AutomatorSession, out_dir: Path, stem: str) -> str:
    xml = await session.source_xml()
    (out_dir / f"{stem}.xml").write_text(xml, encoding="utf-8")
    return xml


async def _reveal_and_tap(
    session: AutomatorSession,
    labels: tuple[str, ...],
    *,
    names: tuple[str, ...] = (),
    scrolls: int = 6,
    exact: bool = False,
) -> bool:
    """Tap a row even if it is parked off-screen (Activity log hub scrolls Your activity above)."""
    if await _tap_wda(session, names=names, labels=()):
        return True
    for _ in range(scrolls + 1):
        xml = await session.source_xml()
        hit = xml_find_control(
            xml, labels=labels, names=names, include_hidden=True, exact=exact
        )
        if hit and hit["visible"]:
            await session.tap_xy(hit["x"] + hit["width"] // 2, hit["y"] + hit["height"] // 2)
            return True
        if hit and not hit["visible"] and int(hit["y"]) < 120:
            await session.scroll("up", distance=0.55, duration=0.3)
        else:
            await session.scroll_feed(distance=0.45, duration=0.28)
        await session.sleep(0.35)
    if await _tap_wda(session, names=names, labels=labels):
        return True
    if exact:
        return False
    return await tap_label(session, labels, timeout=1.5)


async def _bring_above_tab_bar(
    session: AutomatorSession,
    *,
    names: tuple[str, ...],
    labels: tuple[str, ...] = (),
    scrolls: int = 12,
) -> dict[str, Any] | None:
    """Scroll the Activity log accordion until the named row sits above the tab bar.

    Dump 38490b13: Comments at y=724 overlapped Groups tab at y=734. Never tap_xy
    a control whose center is in the tab strip.
    """
    for _ in range(scrolls):
        xml = await session.source_xml()
        tab_top = xml_tab_bar_top(xml)
        hit = xml_find_control(xml, labels=labels, names=names, include_hidden=True)
        if hit is None:
            await session.scroll_feed(distance=0.4, duration=0.28)
            await session.sleep(0.3)
            continue
        if hit["visible"] and int(hit["y"]) >= 90 and not hit_overlaps_tab_bar(hit, tab_top):
            return hit
        if int(hit["y"]) + int(hit["height"]) >= tab_top - 8 or not hit["visible"]:
            await session.scroll_feed(distance=0.42, duration=0.28)
        elif int(hit["y"]) < 80:
            await session.scroll("up", distance=0.4, duration=0.28)
        else:
            await session.scroll_feed(distance=0.35, duration=0.28)
        await session.sleep(0.3)
    xml = await session.source_xml()
    hit = xml_find_control(xml, labels=labels, names=names, include_hidden=True)
    if hit and hit["visible"] and not hit_overlaps_tab_bar(hit, xml_tab_bar_top(xml)):
        return hit
    return None


async def _tap_hit(session: AutomatorSession, hit: dict[str, Any]) -> None:
    await session.tap_xy(int(hit["x"]) + int(hit["width"]) // 2, int(hit["y"]) + int(hit["height"]) // 2)


async def _open_facebook_comments(session: AutomatorSession, out_dir: Path) -> bool:
    """iOS Activity log accordion (not Android page-stack): expand then open Manage comments.

    Profile Tools → Activity log → expand Your Facebook activity → expand Comments
    and reactions → scroll Comments/Manage above the tab bar → tap by AX name.
    Never tap label Comments while it overlaps the Groups tab.
    """
    if not await _reveal_and_tap(session, _PROFILE_TOOLS_LABELS, names=_PROFILE_TOOLS_NAMES, scrolls=1):
        logger.warning("FB Profile Tools tidak ketemu")
        await _dump_xml(session, out_dir, "page_source_comments_fail_tools")
        return False
    await session.sleep(1.0)
    await _dump_xml(session, out_dir, "page_source_after_profile_tools")
    if not await _reveal_and_tap(session, _ACTIVITY_LOG_LABELS, scrolls=6):
        logger.warning("FB Activity log tidak ketemu")
        await _dump_xml(session, out_dir, "page_source_comments_fail_activity_log")
        return False
    await session.sleep(1.2)
    xml = await _dump_xml(session, out_dir, "page_source_after_activity_log")
    if looks_like_fb_activity_log_hub(xml):
        await _reveal_and_tap(session, _YOUR_ACTIVITY_LABELS, names=_YOUR_ACTIVITY_NAMES, scrolls=4)
        await session.sleep(1.0)
        await _dump_xml(session, out_dir, "page_source_after_your_activity")
        await _reveal_and_tap(
            session,
            _COMMENTS_REACTIONS_LABELS,
            names=_COMMENTS_REACTIONS_NAMES,
            scrolls=6,
        )
        await session.sleep(0.9)
        await _dump_xml(session, out_dir, "page_source_after_comments_reactions")
        manage = await _bring_above_tab_bar(
            session,
            names=_MANAGE_COMMENTS_NAMES,
            labels=_MANAGE_COMMENTS_LABELS,
        )
        if manage is not None:
            await _tap_hit(session, manage)
            logger.info("FB tap Manage comments and reactions (landing)")
        else:
            cluster = await _bring_above_tab_bar(session, names=_COMMENTS_CLUSTER_NAMES, labels=_COMMENTS_LABELS)
            if cluster is None:
                logger.warning("FB Comments cluster masih di belakang tab bar")
                await _dump_xml(session, out_dir, "page_source_comments_fail_cluster")
                return False
            await _tap_hit(session, cluster)
            logger.info("FB tap COMMENTSCLUSTER di atas tab bar")
        await session.sleep(1.2)
        xml = await _dump_xml(session, out_dir, "page_source_comments")
        if looks_like_fb_groups_tab(xml):
            logger.warning("FB tap kena tab Groups — mundur")
            try:
                await session.tap("Back", using="name")
            except Exception:  # noqa: BLE001
                await session.tap_xy(24, 76)
            await session.sleep(0.8)
            await _dump_xml(session, out_dir, "page_source_comments_after_groups_back")
            return False
        if looks_like_fb_comments_surface(xml) or looks_like_fb_comments_empty(xml):
            likes = await _bring_above_tab_bar(
                session,
                names=_LIKES_CLUSTER_NAMES,
                labels=_REACTIONS_LABELS,
                scrolls=4,
            )
            if likes is not None:
                await _tap_hit(session, likes)
                await session.sleep(0.8)
                await _dump_xml(session, out_dir, "page_source_reactions")
            return True
        if not looks_like_fb_activity_log_hub(xml):
            return True
        logger.warning("FB masih di accordion Activity log setelah tap Manage/Comments")
        return False
    logger.warning("FB Activity log hub tidak terbuka")
    return False


async def _dismiss_fb_sheets(session: AutomatorSession) -> None:
    for _ in range(4):
        xml = await session.source_xml()
        low = xml.lower()
        if "review audience" in low or "choose who can see" in low:
            await tap_label(session, ("Continue", "Lanjutkan", "Not now", "Nanti"), timeout=2.0)
            await session.sleep(0.8)
            continue
        if looks_like_fb_composer(xml):
            await tap_label(session, ("Close", "Tutup", "Cancel", "Batal", "Back"), timeout=2.0)
            await session.sleep(0.6)
            continue
        break


async def run_fb_profile(args) -> int:
    fb = _load_selectors()
    bundle = resolve_bundle_id("facebook")
    out_dir = Path(args.output) if args.output else default_output_dir("fb_profile")
    out_dir.mkdir(parents=True, exist_ok=True)
    wda_url = args.http or "http://127.0.0.1:8100"

    session = AutomatorSession.connect_http(wda_url, timeout=max(args.timeout, 30.0))
    try:
        fb_phase("launch", f"bundle={bundle}")
        await session.start(bundle)
        wait_sec = float(fb.get("launch_wait_sec", 2.0))
        logger.info("Tunggu Facebook shell %.1fs", wait_sec)
        await session.sleep(wait_sec)
        await _dismiss_fb_sheets(session)

        fb_phase("home", "screenshot homepage")
        await session.screenshot(out_dir / "home.png")
        try:
            (out_dir / "page_source_home.xml").write_text(await session.source_xml(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        fb_phase("profile", "tap tab Your profile")
        logger.info("Tap Profile tab (Your profile)")
        await _wait_and_tap_profile(session, fb)
        await session.sleep(float(fb.get("profile_settle_sec", 1.5)))

        if not await _on_profile_screen(session):
            logger.warning("Belum di layar profile — tap ulang Your profile")
            await _wait_and_tap_profile(session, fb)
            await session.sleep(1.5)

        xml = await session.source_xml()
        (out_dir / "page_source_profile.xml").write_text(xml, encoding="utf-8")
        profile = _read_profile_info(xml)
        (out_dir / "profile.json").write_text(
            json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        fb_phase(
            "profile",
            f"name={profile['display_name']!r} friends={profile['friends']} "
            f"posts={profile['posts']} followers={profile['followers']}",
        )
        logger.info(
            "Profile: name=%r friends=%s posts=%s followers=%s following=%s",
            profile["display_name"],
            profile["friends"],
            profile["posts"],
            profile["followers"],
            profile["following"],
        )

        await session.screenshot(out_dir / "profile.png")
        fb_phase("screenshot", "profile.png")

        await _dismiss_fb_sheets(session)
        xml = await session.source_xml()
        if looks_like_fb_composer(xml):
            logger.warning("Masih di composer Facebook — skip wall TEXT_ONLY")
            write_jsonl(out_dir / "fb_post.jsonl", [])
            write_jsonl(out_dir / "fb_post_items.jsonl", [])
            write_jsonl(out_dir / "fb_comment.jsonl", [])
            dest = mirror_to_temp_crawl(
                out_dir, "fb-profile", session_id=os.environ.get("IOS_TEMP_CRAWL_SESSION", "")
            )
            if dest:
                logger.info("temp_crawl copy → %s", dest)
            fb_done(out_dir, ok=True)
            return 0

        pages = env_int("IOS_FB_MAX_PAGES", 3)
        fb_chrome = (
            "facebook",
            "home",
            "beranda",
            "profile",
            "profil",
            "friends",
            "teman",
            "menu",
            "reels",
            "notifications",
            "edit profile",
            "edit profil",
        )
        fb_phase("posts", "teks wall profil (TEXT_ONLY)")
        posts = await capture_text_pages(
            session,
            out_dir,
            prefix="fb_post",
            max_pages=pages,
            skip=fb_chrome,
            take_screenshot=False,
        )
        logger.info("FB post text pages: %d", len(posts))
        post_items: list[dict[str, Any]] = []
        seen_posts: set[str] = set()
        for xml_path in sorted(out_dir.glob("fb_post_*.xml")):
            for item in extract_fb_feed_items(xml_path.read_text(encoding="utf-8")):
                key = item.casefold()
                if key in seen_posts:
                    continue
                seen_posts.add(key)
                post_items.append({"index": len(post_items) + 1, "text": item})
        write_jsonl(out_dir / "fb_post_items.jsonl", post_items)
        logger.info("FB parsed wall rows: %d", len(post_items))
        try:
            fb_phase("comments", "Profile Tools → Activity log → Comments")
            if not await _open_facebook_comments(session, out_dir):
                logger.warning("FB comments surface tidak ketemu")
                write_jsonl(out_dir / "fb_comment.jsonl", [])
            else:
                xml = await session.source_xml()
                (out_dir / "page_source_comments.xml").write_text(xml, encoding="utf-8")
                if looks_like_fb_comments_empty(xml):
                    logger.info("FB comments empty — 0 row (sama Android)")
                    write_jsonl(out_dir / "fb_comment.jsonl", [])
                    write_jsonl(out_dir / "fb_comment_items.jsonl", [])
                else:
                    comments = await capture_text_pages(
                        session,
                        out_dir,
                        prefix="fb_comment",
                        max_pages=pages,
                        skip=fb_chrome,
                        take_screenshot=False,
                    )
                    logger.info("FB comment text pages: %d", len(comments))
                    comment_items: list[dict[str, Any]] = []
                    seen_comments: set[str] = set()
                    for xml_path in sorted(out_dir.glob("fb_comment_*.xml")):
                        for item in extract_fb_comment_items(xml_path.read_text(encoding="utf-8")):
                            key = item.casefold()
                            if key in seen_comments:
                                continue
                            seen_comments.add(key)
                            comment_items.append({"index": len(comment_items) + 1, "text": item})
                    write_jsonl(out_dir / "fb_comment_items.jsonl", comment_items)
                    logger.info("FB parsed comment rows: %d", len(comment_items))
        except Exception:  # noqa: BLE001
            logger.warning("FB comments skipped — posts already captured")
            write_jsonl(out_dir / "fb_comment.jsonl", [])

        dest = mirror_to_temp_crawl(out_dir, "fb-profile", session_id=os.environ.get("IOS_TEMP_CRAWL_SESSION", ""))
        if dest:
            logger.info("temp_crawl copy → %s", dest)
        logger.info("Done → %s", out_dir.resolve())
        fb_done(out_dir, ok=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("Facebook profile flow failed: %s", exc)
        fb_phase("error", str(exc))
        try:
            await session.screenshot(out_dir / "error.png")
            (out_dir / "page_source_error.xml").write_text(await session.source_xml(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        fb_done(out_dir, ok=False)
        return 1
    finally:
        await session.close()
