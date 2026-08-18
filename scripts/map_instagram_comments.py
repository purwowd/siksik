#!/usr/bin/env python3
"""Map Instagram archive → Settings → Your activity → Comments via ADB dump.

Uses ADB + uiautomator dump only (no UiAutomator Java findObjects).
Output: <siksik>/temp_crawl/ig_comments_map_<timestamp>/
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from map_social_flow import (
    ARCHIVE_LABELS,
    IG_PKG,
    OPTIONS_LABELS,
    PROFILE_LABELS,
    STORY_ARCHIVE_LABELS,
    AdbDevice,
    FlowMapper,
    StepRecord,
    match_contains,
    match_exact,
    node_as_dict,
    pick_best,
    resolve_serial,
)

ROOT_OUT = Path(__file__).resolve().parents[1] / "temp_crawl"
BACK_LABELS = ("Back", "Kembali", "Navigate up", "Navigate back")
YOUR_ACTIVITY_LABELS = ("Your activity", "Aktivitas Anda")
COMMENTS_LABELS = ("Comments", "Komentar")
INTERACTIONS_LABELS = ("Interactions", "Interaksi")
SETTINGS_SIGNALS = (
    "Your activity",
    "Aktivitas Anda",
    "Archive",
    "Arsip",
    "Saved",
    "Disimpan",
    "How you use Instagram",
    "Cara Anda menggunakan Instagram",
)


def header_back_candidates(nodes, size: tuple[int, int]) -> list:
    w, h = size
    max_top = int(h * 0.18)
    max_right = int(w * 0.40)
    hits = []
    for node in match_exact(nodes, BACK_LABELS) + match_contains(nodes, BACK_LABELS):
        left, top, right, _bottom = node.bounds
        if top < max_top and left < max_right:
            hits.append(node)
    uniq = {n.bounds: n for n in hits}
    return sorted(uniq.values(), key=lambda n: (n.bounds[0], n.bounds[1]))


def on_stories_archive(nodes) -> bool:
    return bool(match_exact(nodes, STORY_ARCHIVE_LABELS))


def on_settings(nodes) -> bool:
    return len(match_exact(nodes, SETTINGS_SIGNALS)) >= 2 or bool(
        match_exact(nodes, YOUR_ACTIVITY_LABELS) and match_exact(nodes, ARCHIVE_LABELS)
    )


def on_comments(nodes) -> bool:
    headers = match_exact(nodes, COMMENTS_LABELS)
    return any(n.bounds[1] < 400 for n in headers)


class CommentsMapper(FlowMapper):
    def tap_header_back(self, nodes) -> None:
        candidates = header_back_candidates(nodes, self.device.size)
        chosen = pick_best(candidates) if candidates else None
        if chosen is None:
            w, h = self.device.size
            x, y = int(w * 0.08), int(h * 0.07)
            self.device.tap(x, y)
            time.sleep(1.4)
            self.steps.append(
                StepRecord(
                    step="ig_header_back_geometry",
                    ok=True,
                    detail=f"geometry tap ({x},{y}) candidates=0",
                )
            )
            print(f"[tap] ig_header_back_geometry ({x},{y})")
            return
        self.device.tap(*chosen.center)
        time.sleep(1.4)
        self.steps.append(
            StepRecord(
                step="ig_header_back",
                ok=True,
                detail=(
                    f"tapped {chosen.center} text={chosen.text!r} "
                    f"desc={chosen.content_desc!r} id={chosen.resource_id!r} "
                    f"class={chosen.class_name!r} bounds={chosen.bounds}"
                ),
                matched=[node_as_dict(chosen)],
                candidates=[node_as_dict(n) for n in candidates[:8]],
            )
        )
        print(f"[tap] ig_header_back: {chosen.center} desc={chosen.content_desc!r}")

    def reach_profile(self) -> list:
        self.device.launch(IG_PKG)
        time.sleep(2.0)
        nodes = self.wait_for(lambda ns: len(ns) > 5, timeout_s=15.0) or []
        nodes = self.capture_state("ig_launch")
        self.find_and_tap(
            "ig_click_profile",
            nodes,
            exact=PROFILE_LABELS,
            resource_suffixes=(
                "profile_tab",
                "profile_tab_button",
                "profile_tab_icon",
                "tab_profile",
                "tab_avatar",
            ),
            prefer_bottom=True,
        )
        self.wait_for(
            lambda ns: bool(
                match_exact(
                    ns,
                    (
                        "Edit profile",
                        "Edit profil",
                        "Share profile",
                        "Bagikan profil",
                    ),
                )
            ),
            timeout_s=12.0,
        )
        return self.capture_state("ig_profile")

    def open_archive(self, nodes) -> list:
        for _ in range(2):
            self.device.swipe_percent(0.50, 0.25, 0.50, 0.75, duration_ms=320)
            time.sleep(0.5)
        nodes = self.capture_state("ig_profile_before_options")
        try:
            self.find_and_tap(
                "ig_click_options",
                nodes,
                exact=OPTIONS_LABELS,
                contains=("Options", "Opsi"),
                resource_suffixes=(
                    "action_bar_overflow_icon",
                    "menu_button",
                    "more_options",
                    "profile_header_menu",
                ),
            )
        except RuntimeError:
            w, h = self.device.size
            top_right = [
                n
                for n in nodes
                if n.clickable and n.bounds[1] < int(h * 0.18) and n.bounds[0] > int(w * 0.55)
            ]
            chosen = pick_best(sorted(top_right, key=lambda n: -n.bounds[2]))
            if chosen is None:
                raise
            self.device.tap(*chosen.center)
            time.sleep(1.4)
            self.steps.append(
                StepRecord(
                    step="ig_click_options_geometry",
                    ok=True,
                    detail=f"geometry fallback {chosen.center}",
                    matched=[node_as_dict(chosen)],
                )
            )
            print(f"[tap] ig_click_options_geometry: {chosen.center}")
        self.wait_for(lambda ns: bool(match_exact(ns, ARCHIVE_LABELS)), timeout_s=10.0)
        nodes = self.capture_state("ig_settings")
        self.find_and_tap("ig_click_archive", nodes, exact=ARCHIVE_LABELS)
        self.wait_for(on_stories_archive, timeout_s=12.0)
        return self.capture_state("ig_stories_archive")

    def leave_archive_to_settings(self, nodes) -> list:
        for step in range(8):
            nodes = self.capture_state(f"ig_archive_back_{step}")
            if on_settings(nodes):
                return nodes
            if not on_stories_archive(nodes) and match_exact(nodes, YOUR_ACTIVITY_LABELS):
                return nodes
            self.tap_header_back(nodes)
            time.sleep(0.8)
        return self.capture_state("ig_archive_back_exhausted")

    def open_comments(self, nodes) -> list:
        if not on_settings(nodes) and not match_exact(nodes, YOUR_ACTIVITY_LABELS):
            raise RuntimeError("not on settings after archive back")
        if not match_exact(nodes, YOUR_ACTIVITY_LABELS):
            self.device.swipe_percent(0.50, 0.35, 0.50, 0.75, duration_ms=280)
            time.sleep(0.6)
            nodes = self.capture_state("ig_settings_scrolled")
        self.find_and_tap("ig_click_your_activity", nodes, exact=YOUR_ACTIVITY_LABELS)
        self.wait_for(
            lambda ns: bool(match_exact(ns, COMMENTS_LABELS + INTERACTIONS_LABELS)),
            timeout_s=10.0,
        )
        nodes = self.capture_state("ig_your_activity")
        if match_exact(nodes, COMMENTS_LABELS):
            self.find_and_tap("ig_click_comments", nodes, exact=COMMENTS_LABELS)
        else:
            self.find_and_tap("ig_click_interactions", nodes, exact=INTERACTIONS_LABELS)
            self.wait_for(
                lambda ns: bool(match_exact(ns, COMMENTS_LABELS)),
                timeout_s=8.0,
            )
            nodes = self.capture_state("ig_interactions")
            self.find_and_tap("ig_click_comments", nodes, exact=COMMENTS_LABELS)
        self.wait_for(on_comments, timeout_s=10.0)
        self.capture_state("ig_comments_list")
        self.device.swipe_percent(0.50, 0.78, 0.50, 0.42, duration_ms=400)
        time.sleep(1.2)
        return self.capture_state("ig_comments_after_scroll")

    def run(self, from_archive: bool = False) -> dict:
        report: dict = {"package": IG_PKG, "phases": {}}
        if from_archive:
            nodes = self.capture_state("ig_stories_archive")
            report["phases"]["profile"] = {"skipped": True, "reason": "from_archive"}
        else:
            nodes = self.reach_profile()
            report["phases"]["profile"] = self.summarize_interesting(nodes)
            nodes = self.open_archive(nodes)
        report["phases"]["archive"] = {
            "on_stories_archive": on_stories_archive(nodes),
            "back_candidates": [
                node_as_dict(n) for n in header_back_candidates(nodes, self.device.size)
            ],
            "interesting": self.summarize_interesting(nodes),
        }
        nodes = self.leave_archive_to_settings(nodes)
        report["phases"]["after_archive_back"] = {
            "on_settings": on_settings(nodes),
            "on_stories_archive": on_stories_archive(nodes),
            "your_activity": [node_as_dict(n) for n in match_exact(nodes, YOUR_ACTIVITY_LABELS)],
            "interesting": self.summarize_interesting(nodes),
        }
        nodes = self.open_comments(nodes)
        report["phases"]["comments"] = {
            "on_comments": on_comments(nodes),
            "comment_headers": [node_as_dict(n) for n in match_exact(nodes, COMMENTS_LABELS)[:8]],
            "sample_texts": [n.text for n in nodes if n.text][:40],
        }
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Map IG comments path from Stories archive")
    parser.add_argument("--serial", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--from-archive",
        action="store_true",
        help="Start from the current Stories archive screen (skip profile/options).",
    )
    args = parser.parse_args()

    serial = resolve_serial(args.serial)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT_OUT / f"ig_comments_map_{ts}"
    ROOT_OUT.mkdir(parents=True, exist_ok=True)

    device = AdbDevice(serial)
    device.run_shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.4)
    mapper = CommentsMapper(device, out_dir)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "serial_redacted": True,
        "model": "Infinix_X6837",
        "display_size": {"width": device.size[0], "height": device.size[1]},
        "note": "ADB dump mapping only. Production Kotlin agent was not used.",
        "steps": [],
    }
    try:
        payload["targets"] = {
            "instagram_comments": mapper.run(from_archive=args.from_archive)
        }
    except Exception as exc:
        mapper.save_error(str(exc))
        payload["error"] = str(exc)
        payload["steps"] = [asdict(s) for s in mapper.steps]
        report = mapper.write_report(payload)
        print(f"[failed] {exc}")
        print(f"[report] {report}")
        return 1

    payload["steps"] = [asdict(s) for s in mapper.steps]
    report = mapper.write_report(payload)
    print(f"[done] report -> {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
