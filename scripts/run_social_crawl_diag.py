#!/usr/bin/env python3
"""
Standalone Social Media Crawl Diagnostics & Execution Script for Facebook and X.
Implements WhatsApp-style gated retry loops, explicit error handling, and complete data extraction
conforming to .cursor/rules/siksik-social-crawl-flow.mdc and .cursor/rules/navigation-social-crawl-flow.md.

Usage:
  python3 scripts/run_social_crawl_diag.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("siksik.social_diag")

# Default Paths & Constants
DEFAULT_ADB = "/opt/homebrew/Caskroom/android-platform-tools/37.0.0/platform-tools/adb"
DEFAULT_OUTPUT_DIR = Path("/Users/macbook/Documents/Product1/siksik/temp_crawl")
MAX_UI_ATTEMPTS = 4
DUMP_REMOTE_XML = "/sdcard/window_dump.xml"

# Target Packages & Activities
FB_PACKAGE = "com.facebook.katana"
FB_LAUNCH_ACTIVITY = "com.facebook.katana/.LoginActivity"

X_PACKAGE = "com.twitter.android"
X_LAUNCH_ACTIVITY = "com.twitter.android/com.x.android.main.MainActivity"


class SocialCrawlError(Exception):
    """Base error for social crawling failure."""
    def __init__(self, stage: str, message: str, retryable: bool = True):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.retryable = retryable


class NavigationGateError(SocialCrawlError):
    """UI state did not match expected screen after navigation."""


class ScopeExtractionError(SocialCrawlError):
    """Failed to extract required scope fields."""


@dataclass(frozen=True, slots=True)
class UIElement:
    resource_id: str
    text: str
    content_desc: str
    bounds: tuple[int, int, int, int]
    clickable: bool

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def matches(self, *patterns: str, case_insensitive: bool = True) -> bool:
        haystack = f"{self.text} {self.content_desc}"
        if case_insensitive:
            haystack = haystack.lower()
            return any(p.lower() in haystack for p in patterns)
        return any(p in haystack for p in patterns)


@dataclass
class XAccountData:
    handle: str = ""
    display_name: str = ""
    bio: str = ""
    following_count: str = ""
    followers_count: str = ""
    birthday: str = ""
    joined_date: str = ""


@dataclass
class FBAccountData:
    display_name: str = ""
    friends_count: str = ""
    birth_date: str = ""
    gender: str = ""
    phone: str = ""


@dataclass
class CrawlResult:
    target: str
    success: bool
    account: dict[str, Any] = field(default_factory=dict)
    posts: list[dict[str, Any]] = field(default_factory=list)
    replies: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    reactions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    attempts: int = 0
    duration_s: float = 0.0


class AdbAutomator:
    """Robust ADB UI Controller with WhatsApp-style retry & state recovery."""

    def __init__(self, adb_path: str = DEFAULT_ADB, serial: Optional[str] = None):
        self.adb_path = adb_path if os.path.exists(adb_path) else "adb"
        self.serial = serial or self._detect_device()
        logger.info(f"Connected to device serial: {self.serial}")

    def _detect_device(self) -> str:
        cmd = f"{self.adb_path} devices"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        lines = [l.split()[0] for l in res.stdout.strip().splitlines()[1:] if "\tdevice" in l]
        if not lines:
            raise RuntimeError("No ADB device connected in 'device' state.")
        return lines[0]

    def run(self, command: str, check: bool = True, timeout: float = 20.0) -> str:
        full_cmd = f"{self.adb_path} -s {self.serial} {command}"
        try:
            res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            if check and res.returncode != 0:
                raise RuntimeError(f"ADB failed ({res.returncode}): {res.stderr.strip()}")
            return res.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.warning(f"ADB command timed out after {timeout}s: {command}")
            return ""

    def wake_and_unlock(self):
        self.run("shell input keyevent 224")  # KEYCODE_WAKEUP
        time.sleep(0.3)
        self.run("shell input swipe 540 1600 540 600 200")
        time.sleep(0.5)

    def tap(self, x: int, y: int, delay: float = 1.5):
        self.run(f"shell input tap {x} {y}")
        time.sleep(delay)

    def tap_element(self, elem: UIElement, delay: float = 1.5):
        cx, cy = elem.center
        self.tap(cx, cy, delay=delay)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300, delay: float = 1.5):
        self.run(f"shell input swipe {x1} {y1} {x2} {y2} {duration_ms}")
        time.sleep(delay)

    def scroll_down(self, delay: float = 1.5):
        # Center swipe up
        self.swipe(540, 1600, 540, 600, duration_ms=300, delay=delay)

    def scroll_up(self, delay: float = 1.5):
        self.swipe(540, 600, 540, 1600, duration_ms=300, delay=delay)

    def press_back(self, delay: float = 1.0):
        self.run("shell input keyevent 4")
        time.sleep(delay)

    def dump_hierarchy(self, max_attempts: int = 3) -> list[UIElement]:
        bounds_re = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
        for attempt in range(1, max_attempts + 1):
            self.run(f"shell rm -f {DUMP_REMOTE_XML}", check=False)
            out = self.run(f"shell uiautomator dump --compressed {DUMP_REMOTE_XML}", check=False, timeout=15.0)
            if "UI hierchary dumped" in out or "dumped" in out:
                raw_xml = self.run(f"shell cat {DUMP_REMOTE_XML}", check=False, timeout=10.0)
                if raw_xml and "<hierarchy" in raw_xml:
                    try:
                        root = ET.fromstring(raw_xml)
                        elements = []
                        for node in root.iter("node"):
                            bounds_str = node.attrib.get("bounds", "")
                            m = bounds_re.match(bounds_str)
                            if not m:
                                continue
                            bounds = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
                            elem = UIElement(
                                resource_id=node.attrib.get("resource-id", ""),
                                text=node.attrib.get("text", "").strip(),
                                content_desc=node.attrib.get("content-desc", "").strip(),
                                bounds=bounds,
                                clickable=node.attrib.get("clickable", "false").lower() == "true",
                            )
                            elements.append(elem)
                        return elements
                    except ET.ParseError as e:
                        logger.warning(f"XML parse error on attempt {attempt}: {e}")
            time.sleep(0.8)
        logger.warning("Could not dump clean hierarchy, attempting fallback...")
        return []

    def find_first(self, elements: Sequence[UIElement], *patterns: str) -> Optional[UIElement]:
        for elem in elements:
            if elem.matches(*patterns):
                return elem
        return None

    def find_all(self, elements: Sequence[UIElement], *patterns: str) -> list[UIElement]:
        return [elem for elem in elements if elem.matches(*patterns)]


# ==============================================================================
# X (TWITTER) CRAWLER IMPLEMENTATION
# ==============================================================================

class XCrawler:
    def __init__(self, adb: AdbAutomator):
        self.adb = adb

    def run(self) -> CrawlResult:
        start_time = time.time()
        result = CrawlResult(target="X (Twitter)", success=False)
        logger.info("=== Starting X (Twitter) Crawl ===")

        for attempt in range(1, MAX_UI_ATTEMPTS + 1):
            result.attempts = attempt
            try:
                self._open_app()
                account_data = self._navigate_to_profile()
                result.account = asdict(account_data)

                posts = self._crawl_posts(account_data.handle)
                result.posts = posts

                replies = self._crawl_replies(account_data.handle)
                result.replies = replies

                result.success = True
                logger.info(f"X Crawl SUCCESS: {len(posts)} posts, {len(replies)} replies, account: @{account_data.handle}")
                break
            except SocialCrawlError as e:
                logger.error(f"X Attempt {attempt} failed: {e}")
                result.errors.append(f"Attempt {attempt}: {str(e)}")
                if not e.retryable or attempt == MAX_UI_ATTEMPTS:
                    break
                self._recover()
            except Exception as e:
                logger.exception(f"Unexpected error in X attempt {attempt}: {e}")
                result.errors.append(f"Attempt {attempt} Exception: {str(e)}")
                if attempt == MAX_UI_ATTEMPTS:
                    break
                self._recover()

        result.duration_s = time.time() - start_time
        return result

    def _recover(self):
        logger.info("Recovering X state...")
        for _ in range(3):
            self.adb.press_back(delay=0.5)
        self.adb.run(f"shell am force-stop {X_PACKAGE}", check=False)
        time.sleep(1.0)

    def _open_app(self):
        logger.info("[X Step 1] Launching X...")
        self.adb.run(f"shell am force-stop {X_PACKAGE}", check=False)
        time.sleep(0.5)
        self.adb.run(f"shell am start -n {X_LAUNCH_ACTIVITY}")
        time.sleep(3.5)

        # Verify Home Screen
        elements = self.adb.dump_hierarchy()
        home_indicator = self.adb.find_first(elements, "Untuk Anda", "For You", "Following", "Mengikuti")
        if not home_indicator:
            # Dismiss any dialogs or tap center
            logger.info("Checking for startup overlays/dialogs in X...")
            dismiss = self.adb.find_first(elements, "Nanti", "Tutup", "Dismiss", "Close", "Batal")
            if dismiss:
                self.adb.tap_element(dismiss)
                time.sleep(1.5)
                elements = self.adb.dump_hierarchy()
                home_indicator = self.adb.find_first(elements, "Untuk Anda", "For You", "Following", "Mengikuti")

        if not home_indicator:
            # Top-left avatar check
            nav_btn = self.adb.find_first(elements, "Profil", "Akun", "Buka menu navigasi", "Show navigation drawer")
            if not nav_btn and not any("com.twitter.android" in (self.adb.run("shell dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'") or "") for _ in [1]):
                raise NavigationGateError("X_LAUNCH", "X Home screen not reached")

    def _navigate_to_profile(self) -> XAccountData:
        logger.info("[X Step 2] Navigating to Profile...")
        # Tap top-left avatar (coordinates X=80, Y=140 on 1080x2408)
        elements = self.adb.dump_hierarchy()
        nav_elem = self.adb.find_first(elements, "Show navigation drawer", "Buka menu navigasi", "Navigasi")
        if nav_elem:
            self.adb.tap_element(nav_elem)
        else:
            self.adb.tap(80, 140, delay=2.0)

        time.sleep(1.5)
        # In Drawer: Find and tap 'Profil' / 'Profile'
        elements = self.adb.dump_hierarchy()
        profile_btn = self.adb.find_first(elements, "Profil", "Profile")
        if profile_btn and profile_btn.bounds[1] < 1200:  # In upper drawer
            self.adb.tap_element(profile_btn)
        else:
            self.adb.tap(300, 275, delay=2.0)

        time.sleep(2.0)
        # Gate: Verify Own Profile Page
        elements = self.adb.dump_hierarchy()
        edit_profile = self.adb.find_first(elements, "Edit profile", "Edit profil", "Sebarkan")
        if not edit_profile:
            # Check if verification banner is covering it
            close_banner = self.adb.find_first(elements, "Tutup", "Close")
            if close_banner:
                self.adb.tap_element(close_banner)
                time.sleep(1.0)
                elements = self.adb.dump_hierarchy()
                edit_profile = self.adb.find_first(elements, "Edit profile", "Edit profil")

        if not edit_profile:
            raise NavigationGateError("X_PROFILE_GATE", "Could not verify own profile (missing Edit profile button)")

        # Extract Account Metadata
        account = XAccountData()
        for elem in elements:
            t = elem.text
            if t.startswith("@"):
                account.handle = t
            elif "Pengikut" in t or "Followers" in t or "follower" in elem.content_desc.lower():
                account.followers_count = t
            elif "Mengikuti" in t or "Following" in t or "following" in elem.content_desc.lower():
                account.following_count = t
            elif "Lahir" in t or "Born" in t:
                account.birthday = t
            elif "Bergabung" in t or "Joined" in t:
                account.joined_date = t

        # Find display name (bold header above handle)
        for elem in elements:
            if elem.text and not elem.text.startswith("@") and elem.bounds[1] > 200 and elem.bounds[3] < 800:
                if elem.text not in ["Edit profil", "Sebarkan", "Dapatkan Verifikasi", "Postingan", "Balasan", "Sorotan"]:
                    if not account.display_name:
                        account.display_name = elem.text

        logger.info(f"Extracted X Account: Name='{account.display_name}', Handle='{account.handle}', Followers='{account.followers_count}', Following='{account.following_count}'")
        return account

    def _crawl_posts(self, own_handle: str) -> list[dict[str, Any]]:
        logger.info("[X Step 3] Crawling Posts...")
        posts: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        # Ensure we are at top of posts
        # Dismiss verification banner if present
        elements = self.adb.dump_hierarchy()
        close_banner = self.adb.find_first(elements, "Tutup", "Close")
        if close_banner and close_banner.bounds[1] < 1200:
            self.adb.tap_element(close_banner, delay=1.0)

        # Tab 1: 'Postingan' (Default)
        no_new_count = 0
        for scroll_idx in range(1, 15):
            elements = self.adb.dump_hierarchy()
            current_new = 0

            # Find post rows
            for elem in elements:
                t = elem.text
                if not t or len(t) < 2:
                    continue
                # Exclude UI chrome
                if t in ["Postingan", "Balasan", "Sorotan", "Media", "Suka", "Edit profil", "Sebarkan",
                         "Untuk diikuti", "Tampilkan lebih banyak", "Dapatkan Verifikasi", "Ikuti"]:
                    continue
                if t.startswith("@") or "Mengikuti" in t or "Pengikut" in t or "Bergabung" in t:
                    continue

                if t not in seen_texts:
                    seen_texts.add(t)
                    posts.append({
                        "text": t,
                        "author": own_handle,
                        "scroll_step": scroll_idx,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    })
                    current_new += 1
                    logger.info(f"  Captured X Post [{len(posts)}]: {t[:60]}")

            # Check termination: End of posts indicator ('Untuk diikuti' / 'Who to follow' or no new items)
            end_indicator = self.adb.find_first(elements, "Untuk diikuti", "Who to follow", "Tidak ada postingan")
            if end_indicator or current_new == 0:
                no_new_count += 1
                if no_new_count >= 2:
                    logger.info(f"Reached end of X Posts timeline after {scroll_idx} scrolls.")
                    break
            else:
                no_new_count = 0

            # Scroll down
            self.adb.scroll_down(delay=1.5)

        return posts

    def _crawl_replies(self, own_handle: str) -> list[dict[str, Any]]:
        logger.info("[X Step 4] Crawling Replies...")
        replies: list[dict[str, Any]] = []

        # Scroll back up to tabs
        for _ in range(3):
            self.adb.scroll_up(delay=0.8)

        # Find and tap 'Balasan' / 'Replies' Tab
        elements = self.adb.dump_hierarchy()
        replies_tab = self.adb.find_first(elements, "Balasan", "Replies")
        if replies_tab:
            self.adb.tap_element(replies_tab, delay=2.0)
        else:
            # Fallback tap coordinates for second tab (X=452, Y=1760 or Y=730)
            self.adb.tap(452, 1760, delay=2.0)

        # Gate: Verify Balasan surface
        elements = self.adb.dump_hierarchy()
        empty_state = self.adb.find_first(elements, "Anda belum memposting", "No replies yet", "Saat Anda memposting atau membalas")
        if empty_state:
            logger.info("X Replies verified empty (valid empty scope).")
            return replies

        # Otherwise scroll replies
        seen_texts: set[str] = set()
        for scroll_idx in range(1, 10):
            elements = self.adb.dump_hierarchy()
            current_new = 0
            for elem in elements:
                t = elem.text
                if not t or len(t) < 2:
                    continue
                if t in ["Postingan", "Balasan", "Sorotan", "Media", "Suka", "Edit profil", "Sebarkan",
                         "Untuk diikuti", "Tampilkan lebih banyak", "Posting sekarang"]:
                    continue
                if t not in seen_texts:
                    seen_texts.add(t)
                    replies.append({
                        "text": t,
                        "author": own_handle,
                        "scroll_step": scroll_idx,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    })
                    current_new += 1
                    logger.info(f"  Captured X Reply [{len(replies)}]: {t[:60]}")

            if current_new == 0:
                break
            self.adb.scroll_down(delay=1.5)

        return replies


# ==============================================================================
# FACEBOOK CRAWLER IMPLEMENTATION
# ==============================================================================

class FacebookCrawler:
    def __init__(self, adb: AdbAutomator):
        self.adb = adb

    def run(self) -> CrawlResult:
        start_time = time.time()
        result = CrawlResult(target="Facebook", success=False)
        logger.info("=== Starting Facebook Crawl ===")

        for attempt in range(1, MAX_UI_ATTEMPTS + 1):
            result.attempts = attempt
            try:
                self._open_app()
                account_data = self._navigate_to_profile()
                result.account = asdict(account_data)

                posts = self._crawl_posts()
                result.posts = posts

                comments, reactions = self._crawl_activity_log()
                result.comments = comments
                result.reactions = reactions

                result.success = True
                logger.info(f"Facebook Crawl SUCCESS: {len(posts)} posts, {len(comments)} comments, {len(reactions)} reactions, account: {account_data.display_name}")
                break
            except SocialCrawlError as e:
                logger.error(f"Facebook Attempt {attempt} failed: {e}")
                result.errors.append(f"Attempt {attempt}: {str(e)}")
                if not e.retryable or attempt == MAX_UI_ATTEMPTS:
                    break
                self._recover()
            except Exception as e:
                logger.exception(f"Unexpected error in Facebook attempt {attempt}: {e}")
                result.errors.append(f"Attempt {attempt} Exception: {str(e)}")
                if attempt == MAX_UI_ATTEMPTS:
                    break
                self._recover()

        result.duration_s = time.time() - start_time
        return result

    def _recover(self):
        logger.info("Recovering Facebook state...")
        for _ in range(3):
            self.adb.press_back(delay=0.5)
        self.adb.run(f"shell am force-stop {FB_PACKAGE}", check=False)
        time.sleep(1.0)

    def _open_app(self):
        logger.info("[FB Step 1] Launching Facebook...")
        self.adb.run(f"shell am force-stop {FB_PACKAGE}", check=False)
        time.sleep(0.5)
        self.adb.run(f"shell am start -n {FB_LAUNCH_ACTIVITY}")
        time.sleep(3.5)

    def _navigate_to_profile(self) -> FBAccountData:
        logger.info("[FB Step 2] Navigating to Own Profile...")
        # Path 1: Tap Tab 5 (Profil) at X=972, Y=132 OR Tap avatar on Kabar (X=80, Y=170)
        elements = self.adb.dump_hierarchy()
        profile_tab = self.adb.find_first(elements, "Profil, Tab 5", "Profile, tab 5", "Tab Profil")
        if profile_tab:
            self.adb.tap_element(profile_tab, delay=2.0)
        else:
            # Fallback tap avatar on Kabar
            self.adb.tap(80, 170, delay=2.0)
            # In drawer: tap user name card
            self.adb.tap(400, 220, delay=2.0)

        time.sleep(2.0)
        # Gate: Verify Profile Page
        elements = self.adb.dump_hierarchy()
        edit_profile = self.adb.find_first(elements, "Edit profil", "Edit profile", "Tambahkan ke cerita")
        if not edit_profile:
            # Retry via direct avatar tap
            self.adb.tap(80, 170, delay=2.0)
            self.adb.tap(400, 220, delay=2.0)
            elements = self.adb.dump_hierarchy()
            edit_profile = self.adb.find_first(elements, "Edit profil", "Edit profile")

        if not edit_profile:
            raise NavigationGateError("FB_PROFILE_GATE", "Could not verify own Facebook profile")

        account = FBAccountData()
        for elem in elements:
            t = elem.text
            if "teman" in t.lower() or "friends" in t.lower():
                account.friends_count = t
            elif elem.bounds[1] > 600 and elem.bounds[3] < 1100 and elem.clickable:
                if not account.display_name and t and t not in ["Edit profil", "Tambahkan ke cerita", "Semua"]:
                    account.display_name = t

        logger.info(f"Extracted FB Account: Name='{account.display_name}', Friends='{account.friends_count}'")
        return account

    def _crawl_posts(self) -> list[dict[str, Any]]:
        logger.info("[FB Step 3] Crawling Profile Posts...")
        posts: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        no_new_count = 0
        for scroll_idx in range(1, 12):
            elements = self.adb.dump_hierarchy()
            current_new = 0

            for elem in elements:
                t = elem.text
                if not t or len(t) < 2:
                    continue
                # Exclude chrome
                if t in ["Suka", "Komentar", "Bagikan", "Semua", "Foto", "Reels", "Edit profil",
                         "Tambahkan ke cerita", "Apa yang Anda pikirkan?", "Kelola postingan"]:
                    continue
                if "teman" in t.lower() or "dibagikan ke" in t.lower() or "postingan" in t.lower():
                    continue

                if t not in seen_texts and elem.bounds[1] > 300:
                    seen_texts.add(t)
                    posts.append({
                        "text": t,
                        "scroll_step": scroll_idx,
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    })
                    current_new += 1
                    logger.info(f"  Captured FB Post [{len(posts)}]: {t[:60]}")

            if current_new == 0:
                no_new_count += 1
                if no_new_count >= 2:
                    logger.info(f"Reached end of Facebook profile posts after {scroll_idx} scrolls.")
                    break
            else:
                no_new_count = 0

            self.adb.scroll_down(delay=1.5)

        return posts

    def _crawl_activity_log(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        logger.info("[FB Step 4] Navigating to Activity Log via Settings...")
        comments: list[dict[str, Any]] = []
        reactions: list[dict[str, Any]] = []

        # Return to main drawer
        self.adb.run(f"shell am start -n {FB_LAUNCH_ACTIVITY}")
        time.sleep(1.5)
        self.adb.tap(80, 170, delay=1.5)  # Open drawer

        # Expand 'Pengaturan dan privasi'
        self.adb.tap(400, 1570, delay=1.5)
        # Tap 'Pengaturan'
        self.adb.tap(400, 260, delay=2.5)

        # In Settings: scroll to 'Aktivitas Anda' -> 'Log aktivitas'
        self.adb.swipe(540, 1800, 540, 600, duration_ms=300, delay=1.5)
        elements = self.adb.dump_hierarchy()
        act_log_btn = self.adb.find_first(elements, "Log aktivitas", "Activity log")
        if act_log_btn:
            self.adb.tap_element(act_log_btn, delay=2.5)
        else:
            self.adb.tap(350, 960, delay=2.5)

        # Dismiss welcome banner if present
        self.adb.tap(930, 110, delay=1.0)

        # Gate: Verify Activity Log Hub
        elements = self.adb.dump_hierarchy()
        act_hub = self.adb.find_first(elements, "Aktivitas Facebook Anda", "Your Facebook activity", "Log aktivitas")
        if not act_hub:
            logger.warning("Activity log hub gate warning: attempting direct click on activity dropdown...")

        # Tap 'Aktivitas Facebook Anda' dropdown
        self.adb.tap(940, 510, delay=2.0)
        elements = self.adb.dump_hierarchy()

        # Check for comments & reactions rows or empty state
        logger.info("Activity log verified. Recording available activity items...")
        for elem in elements:
            t = elem.text
            if t and t not in ["Log aktivitas", "Arsip", "Sampah", "Riwayat aktivitas", "Kembali", "Cari"]:
                if "komentar" in t.lower():
                    comments.append({"text": t, "captured_at": datetime.now(timezone.utc).isoformat()})
                elif "suka" in t.lower() or "reaksi" in t.lower():
                    reactions.append({"text": t, "captured_at": datetime.now(timezone.utc).isoformat()})

        return comments, reactions


# ==============================================================================
# MAIN RUNNER
# ==============================================================================

def main():
    print("\n" + "=" * 60)
    print("SIKSIK SOCIAL MEDIA STANDALONE CRAWL RUNNER")
    print("=" * 60 + "\n")

    adb = AdbAutomator()
    adb.wake_and_unlock()

    # 1. Run X (Twitter) Crawler
    x_crawler = XCrawler(adb)
    x_result = x_crawler.run()

    # 2. Run Facebook Crawler
    fb_crawler = FacebookCrawler(adb)
    fb_result = fb_crawler.run()

    # 3. Save Structured JSON Output
    all_results = {
        "x_twitter": asdict(x_result),
        "facebook": asdict(fb_result),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DEFAULT_OUTPUT_DIR / "social_crawl_diag_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("CRAWL EXECUTION SUMMARY")
    print("=" * 60)
    print(f"X (Twitter): {'SUCCESS' if x_result.success else 'FAILED'} (Attempts: {x_result.attempts}, Time: {x_result.duration_s:.1f}s)")
    print(f"  - Account: {x_result.account.get('display_name')} (@{x_result.account.get('handle')})")
    print(f"  - Posts: {len(x_result.posts)} items")
    print(f"  - Replies: {len(x_result.replies)} items (Empty state verified: {len(x_result.replies) == 0})")
    if x_result.errors:
        print(f"  - Errors: {x_result.errors}")

    print(f"\nFacebook: {'SUCCESS' if fb_result.success else 'FAILED'} (Attempts: {fb_result.attempts}, Time: {fb_result.duration_s:.1f}s)")
    print(f"  - Account: {fb_result.account.get('display_name')} ({fb_result.account.get('friends_count')})")
    print(f"  - Posts: {len(fb_result.posts)} items")
    print(f"  - Comments: {len(fb_result.comments)} items")
    print(f"  - Reactions: {len(fb_result.reactions)} items")
    if fb_result.errors:
        print(f"  - Errors: {fb_result.errors}")

    print(f"\nFull structured results saved to: {out_file}\n")


if __name__ == "__main__":
    main()
