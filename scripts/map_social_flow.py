#!/usr/bin/env python3
"""Map Instagram (+ optional X) UI flow without touching the Android agent.

Uses ADB + `uiautomator dump` only (no Appium / uiautomator2).
Outputs screenshots, hierarchy XML, and a flow map JSON under:

  <siksik>/temp_crawl/flow_map_<timestamp>/

Does not modify production Kotlin automation.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

# scripts/ → siksik/
ROOT_OUT = Path(__file__).resolve().parents[1] / "temp_crawl"
IG_PKG = "com.instagram.android"
X_PKG = "com.twitter.android"

PROFILE_LABELS = ("Profile", "Profil", "Your profile", "profil Anda", "You", "Anda")
OPTIONS_LABELS = ("Options", "Opsi", "More options", "Opsi lainnya", "Menu")
ARCHIVE_LABELS = ("Archive", "Arsip")
STORY_ARCHIVE_LABELS = (
    "Stories archive",
    "Story archive",
    "Arsip cerita",
    "Arsip Cerita",
)
POSTS_ARCHIVE_LABELS = ("Posts archive", "Arsip postingan", "Post archive")
COMPANION_LABELS = (
    "Your activity",
    "Aktivitas Anda",
    "Saved",
    "Disimpan",
    "Settings and activity",
    "Setelan dan aktivitas",
    "Settings",
    "Setelan",
)
POSTS_TAB_LABELS = ("Posts", "Postingan", "Kiriman")
REPLIES_LABELS = ("Replies", "Balasan")
X_PROFILE_LABELS = ("Profile", "Profil")


@dataclass
class NodeHit:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    clickable: bool
    bounds: tuple[int, int, int, int]
    center: tuple[int, int]

    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id or self.class_name


@dataclass
class StepRecord:
    step: str
    ok: bool
    detail: str = ""
    screenshot: str | None = None
    hierarchy: str | None = None
    matched: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)


class AdbDevice:
    def __init__(self, serial: str | None) -> None:
        self.serial = serial
        self.size = self._wm_size()

    def _base(self) -> list[str]:
        cmd = ["adb"]
        if self.serial:
            cmd.extend(["-s", self.serial])
        return cmd

    def run(self, args: Sequence[str], timeout: float = 30.0) -> str:
        proc = subprocess.run(
            [*self._base(), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"adb {' '.join(args)} failed ({proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()}"
            )
        return proc.stdout

    def run_shell(self, shell: str, timeout: float = 30.0) -> str:
        return self.run(["shell", shell], timeout=timeout)

    def _wm_size(self) -> tuple[int, int]:
        out = self.run_shell("wm size")
        match = re.search(r"(\d+)x(\d+)", out)
        if not match:
            raise RuntimeError(f"cannot parse wm size: {out!r}")
        return int(match.group(1)), int(match.group(2))

    def launch(self, package: str) -> None:
        self.run(
            [
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]
        )

    def dump_hierarchy(self) -> str:
        remote = "/sdcard/window_dump.xml"
        # Infinix/Transsion uncompressed dump often prints
        # "could not get idle state" with exit 0 and no file.
        # Compressed dump succeeds; try it first.
        errors: list[str] = []
        for cmd in (
            f"timeout 15s uiautomator dump --compressed {remote}",
            f"timeout 15s uiautomator dump {remote}",
        ):
            try:
                out = self.run_shell(cmd, timeout=20.0)
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            if "ERROR" in out.upper() and "dumped" not in out.lower():
                errors.append(out.strip() or cmd)
                continue
            local_bytes = subprocess.run(
                [*self._base(), "exec-out", "cat", remote],
                check=False,
                capture_output=True,
                timeout=30.0,
            )
            if local_bytes.returncode != 0 or not local_bytes.stdout:
                errors.append("failed to pull uiautomator dump")
                continue
            text = local_bytes.stdout.decode("utf-8", errors="replace")
            if "<hierarchy" not in text and "<node" not in text:
                errors.append("uiautomator dump empty or invalid")
                continue
            return text
        raise RuntimeError("; ".join(errors) or "uiautomator dump failed")

    def screenshot_png(self) -> bytes:
        proc = subprocess.run(
            [*self._base(), "exec-out", "screencap", "-p"],
            check=False,
            capture_output=True,
            timeout=30.0,
        )
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError("screencap failed")
        data = proc.stdout
        # Some devices prefix an unwanted CRLF; strip until PNG magic.
        idx = data.find(b"\x89PNG")
        if idx > 0:
            data = data[idx:]
        return data

    def tap(self, x: int, y: int) -> None:
        self.run_shell(f"input tap {x} {y}")

    def swipe_percent(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration_ms: int = 400,
    ) -> None:
        w, h = self.size
        self.run_shell(
            "input swipe "
            f"{int(w * x1)} {int(h * y1)} {int(w * x2)} {int(h * y2)} {duration_ms}"
        )


def parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw or "")
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def iter_nodes(xml_text: str) -> list[NodeHit]:
    root = ET.fromstring(xml_text)
    hits: list[NodeHit] = []
    for node in root.iter("node"):
        bounds = parse_bounds(node.attrib.get("bounds", ""))
        if bounds is None:
            continue
        left, top, right, bottom = bounds
        hits.append(
            NodeHit(
                text=(node.attrib.get("text") or "").strip(),
                content_desc=(node.attrib.get("content-desc") or "").strip(),
                resource_id=(node.attrib.get("resource-id") or "").strip(),
                class_name=(node.attrib.get("class") or "").strip(),
                clickable=node.attrib.get("clickable") == "true",
                bounds=bounds,
                center=((left + right) // 2, (top + bottom) // 2),
            )
        )
    return hits


def node_as_dict(node: NodeHit) -> dict:
    return asdict(node)


def match_exact(nodes: Iterable[NodeHit], labels: Sequence[str]) -> list[NodeHit]:
    wanted = {label.casefold() for label in labels}
    out: list[NodeHit] = []
    for node in nodes:
        values = {
            node.text.casefold(),
            node.content_desc.casefold(),
        }
        if values & wanted:
            out.append(node)
    return out


def match_contains(nodes: Iterable[NodeHit], fragments: Sequence[str]) -> list[NodeHit]:
    frags = [f.casefold() for f in fragments]
    out: list[NodeHit] = []
    for node in nodes:
        blob = f"{node.text} {node.content_desc} {node.resource_id}".casefold()
        if any(frag in blob for frag in frags):
            out.append(node)
    return out


def match_resource_suffix(nodes: Iterable[NodeHit], suffixes: Sequence[str]) -> list[NodeHit]:
    out: list[NodeHit] = []
    for node in nodes:
        rid = node.resource_id
        if any(rid.endswith(f"/{suffix}") or rid.endswith(f":id/{suffix}") for suffix in suffixes):
            out.append(node)
    return out


def pick_best(candidates: list[NodeHit], prefer_bottom: bool = False) -> NodeHit | None:
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda n: (
            0 if n.clickable else 1,
            -n.bounds[3] if prefer_bottom else n.bounds[1],
            n.bounds[0],
        ),
    )
    return ranked[0]


class FlowMapper:
    def __init__(self, device: AdbDevice, out_dir: Path) -> None:
        self.device = device
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.steps: list[StepRecord] = []
        self.counter = 0

    def save_debug_screenshot(self, state_name: str) -> Path:
        self.counter += 1
        path = self.out_dir / f"{self.counter:02d}_{state_name}.png"
        path.write_bytes(self.device.screenshot_png())
        return path

    def save_hierarchy(self, state_name: str, xml_text: str) -> Path:
        path = self.out_dir / f"{self.counter:02d}_{state_name}.xml"
        path.write_text(xml_text, encoding="utf-8")
        return path

    def save_error(self, reason: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            path = self.out_dir / f"error_{ts}.png"
            path.write_bytes(self.device.screenshot_png())
            print(f"[error] screenshot -> {path} ({reason})", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] could not capture error screenshot: {exc}", file=sys.stderr)

    def wait_for(
        self,
        predicate,
        timeout_s: float = 12.0,
        poll_s: float = 0.6,
    ) -> list[NodeHit] | None:
        deadline = time.time() + timeout_s
        last: list[NodeHit] | None = None
        while time.time() < deadline:
            try:
                xml_text = self.device.dump_hierarchy()
                last = iter_nodes(xml_text)
                if predicate(last):
                    return last
            except Exception:  # noqa: BLE001
                pass
            time.sleep(poll_s)
        return last

    def capture_state(self, step: str, nodes: list[NodeHit] | None = None) -> list[NodeHit]:
        shot = self.save_debug_screenshot(step)
        xml_text = self.device.dump_hierarchy()
        hier = self.save_hierarchy(step, xml_text)
        parsed = nodes or iter_nodes(xml_text)
        record = StepRecord(
            step=step,
            ok=True,
            detail=f"nodes={len(parsed)} size={self.device.size}",
            screenshot=str(shot),
            hierarchy=str(hier),
        )
        self.steps.append(record)
        print(f"[ok] {step} -> {shot.name} ({len(parsed)} nodes)")
        return parsed

    def dynamic_swipe_up(self) -> None:
        # Plan rule: percentage swipe, not fixed pixels.
        self.device.swipe_percent(0.50, 0.80, 0.50, 0.20, duration_ms=450)

    def find_and_tap(
        self,
        step: str,
        nodes: list[NodeHit],
        *,
        exact: Sequence[str] = (),
        contains: Sequence[str] = (),
        resource_suffixes: Sequence[str] = (),
        prefer_bottom: bool = False,
        required: bool = True,
    ) -> NodeHit | None:
        candidates: list[NodeHit] = []
        if exact:
            candidates.extend(match_exact(nodes, exact))
        if contains:
            candidates.extend(match_contains(nodes, contains))
        if resource_suffixes:
            candidates.extend(match_resource_suffix(nodes, resource_suffixes))
        # de-dupe by bounds
        uniq: dict[tuple[int, int, int, int], NodeHit] = {}
        for node in candidates:
            uniq[node.bounds] = node
        candidates = list(uniq.values())
        chosen = pick_best(candidates, prefer_bottom=prefer_bottom)
        record = StepRecord(
            step=step,
            ok=chosen is not None,
            detail="tap" if chosen else "no_match",
            matched=[node_as_dict(chosen)] if chosen else [],
            candidates=[node_as_dict(n) for n in candidates[:20]],
        )
        if chosen is None:
            self.steps.append(record)
            msg = f"selector miss: {step} exact={exact} contains={contains} res={resource_suffixes}"
            print(f"[miss] {msg}")
            if required:
                self.save_error(msg)
                raise RuntimeError(msg)
            return None
        # Prefer a clickable ancestor-sized row: if target itself is not clickable,
        # expand to the widest same-band node that shares the label.
        tap_node = chosen
        if not chosen.clickable:
            band = [
                n for n in candidates
                if n.clickable and abs(n.bounds[1] - chosen.bounds[1]) < 80
            ]
            if band:
                tap_node = max(band, key=lambda n: n.bounds[2] - n.bounds[0])
            else:
                # Full-width-ish sibling in same vertical band from all nodes.
                pass
        self.device.tap(*tap_node.center)
        time.sleep(1.8)
        record.detail = (
            f"tapped ({chosen.center[0]},{chosen.center[1]}) "
            f"text={chosen.text!r} desc={chosen.content_desc!r} id={chosen.resource_id!r}"
        )
        self.steps.append(record)
        print(f"[tap] {step}: {record.detail}")
        return chosen

    def summarize_interesting(self, nodes: list[NodeHit]) -> dict:
        return {
            "profile_hits": [node_as_dict(n) for n in match_exact(nodes, PROFILE_LABELS)[:10]],
            "options_hits": [node_as_dict(n) for n in match_exact(nodes, OPTIONS_LABELS)[:10]],
            "archive_hits": [node_as_dict(n) for n in match_exact(nodes, ARCHIVE_LABELS)[:10]],
            "story_archive_hits": [
                node_as_dict(n) for n in match_exact(nodes, STORY_ARCHIVE_LABELS)[:10]
            ],
            "companion_hits": [
                node_as_dict(n) for n in match_exact(nodes, COMPANION_LABELS)[:10]
            ],
            "profile_tab_resources": [
                node_as_dict(n)
                for n in match_resource_suffix(
                    nodes,
                    ("profile_tab", "profile_tab_button", "tab_profile", "tab_avatar"),
                )[:10]
            ],
            "grid_resources": [
                node_as_dict(n)
                for n in match_resource_suffix(
                    nodes,
                    ("media_grid_item", "image_button", "profile_grid_item"),
                )[:15]
            ],
        }

    def map_instagram(self) -> dict:
        report: dict = {"package": IG_PKG, "phases": {}}
        self.device.launch(IG_PKG)
        nodes = self.wait_for(lambda ns: len(ns) > 5, timeout_s=15.0) or []
        nodes = self.capture_state("ig_home", nodes)
        report["phases"]["home"] = self.summarize_interesting(nodes)

        # Profile via text/desc/resource — no hardcoded coordinates.
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
        nodes = self.wait_for(
            lambda ns: bool(
                match_exact(ns, ("Edit profile", "Edit profil", "Ubah profil", "Share profile", "Bagikan profil"))
                or match_resource_suffix(ns, ("profile_header_container", "row_profile_header"))
                or match_exact(ns, ("posts", "postingan", "Posts", "Postingan", "followers", "pengikut"))
            ),
            timeout_s=12.0,
        ) or []
        nodes = self.capture_state("ig_profile_home", nodes)
        report["phases"]["profile"] = self.summarize_interesting(nodes)
        report["phases"]["profile"]["post_count_guess"] = guess_post_count(nodes)

        # Grid content map (no open each post).
        grid = match_resource_suffix(
            nodes,
            ("media_grid_item", "image_button", "profile_grid_item", "image_view"),
        )
        report["phases"]["profile"]["grid_thumb_count"] = len(grid)
        for i in range(1, 4):
            before = viewport_signature(nodes)
            self.dynamic_swipe_up()
            time.sleep(1.5)
            nodes = self.capture_state(f"ig_posts_scroll_{i}")
            after = viewport_signature(nodes)
            report.setdefault("phases", {}).setdefault("posts_scroll", []).append(
                {
                    "i": i,
                    "changed": before != after,
                    "interesting": self.summarize_interesting(nodes),
                }
            )
            if before == after:
                break

        # Re-open profile top for archive path: launch again keeps session.
        # Prefer Options by label/desc; do not use fixed x/y.
        # Scroll profile back up a bit first.
        for _ in range(2):
            self.device.swipe_percent(0.50, 0.25, 0.50, 0.75, duration_ms=350)
            time.sleep(0.6)
        nodes = self.capture_state("ig_profile_before_menu")
        try:
            self.find_and_tap(
                "ig_click_options",
                nodes,
                exact=OPTIONS_LABELS,
                contains=("Options", "Opsi", "More options"),
                resource_suffixes=(
                    "action_bar_overflow_icon",
                    "menu_button",
                    "more_options",
                    "profile_header_menu",
                ),
            )
        except RuntimeError:
            # Fallback: top-right clickable without fixed absolute pixels —
            # choose the rightmost clickable in the top 18% of the screen.
            w, h = self.device.size
            top_band = [
                n
                for n in nodes
                if n.clickable and n.bounds[1] < int(h * 0.18) and n.bounds[0] > int(w * 0.55)
            ]
            chosen = pick_best(sorted(top_band, key=lambda n: -n.bounds[2]), prefer_bottom=False)
            if chosen is None:
                raise
            self.device.tap(*chosen.center)
            time.sleep(1.2)
            self.steps.append(
                StepRecord(
                    step="ig_click_options_geometry",
                    ok=True,
                    detail=f"geometry fallback {chosen.center} id={chosen.resource_id!r} desc={chosen.content_desc!r}",
                    matched=[node_as_dict(chosen)],
                )
            )
            print(f"[tap] ig_click_options_geometry: {chosen.center}")

        nodes = self.wait_for(
            lambda ns: bool(match_exact(ns, ARCHIVE_LABELS)),
            timeout_s=8.0,
        ) or []
        nodes = self.capture_state("ig_options_sheet", nodes)
        report["phases"]["options_sheet"] = self.summarize_interesting(nodes)
        report["phases"]["options_sheet"]["has_archive"] = bool(
            match_exact(nodes, ARCHIVE_LABELS)
        )
        report["phases"]["options_sheet"]["has_companion"] = bool(
            match_exact(nodes, COMPANION_LABELS)
        )

        self.find_and_tap(
            "ig_click_archive",
            nodes,
            exact=ARCHIVE_LABELS,
            prefer_bottom=False,
        )
        nodes = self.wait_for(
            lambda ns: bool(
                match_exact(ns, STORY_ARCHIVE_LABELS)
                or match_exact(ns, ARCHIVE_LABELS)
                or match_exact(ns, POSTS_ARCHIVE_LABELS)
            ),
            timeout_s=10.0,
        ) or []
        nodes = self.capture_state("ig_archive_page", nodes)
        report["phases"]["archive_page"] = self.summarize_interesting(nodes)

        if match_exact(nodes, POSTS_ARCHIVE_LABELS) and not match_exact(
            nodes, STORY_ARCHIVE_LABELS
        ):
            # Open picker then Stories archive.
            self.find_and_tap(
                "ig_open_archive_picker",
                nodes,
                exact=POSTS_ARCHIVE_LABELS + ARCHIVE_LABELS,
                required=False,
            )
            nodes = self.capture_state("ig_archive_picker")
            self.find_and_tap(
                "ig_select_stories_archive",
                nodes,
                exact=STORY_ARCHIVE_LABELS,
            )
            nodes = self.capture_state("ig_stories_archive_top")
        elif match_exact(nodes, STORY_ARCHIVE_LABELS):
            nodes = self.capture_state("ig_stories_archive_top", nodes)
        else:
            # Still map whatever page we landed on.
            self.capture_state("ig_archive_unknown_mode", nodes)

        nodes = self.capture_state("ig_stories_archive_before_scroll")
        report["phases"]["stories_archive"] = self.summarize_interesting(nodes)
        for i in range(1, 4):
            before = viewport_signature(nodes)
            self.dynamic_swipe_up()
            time.sleep(1.5)
            nodes = self.capture_state(f"ig_archive_scroll_{i}")
            after = viewport_signature(nodes)
            report.setdefault("phases", {}).setdefault("archive_scroll", []).append(
                {
                    "i": i,
                    "changed": before != after,
                    "interesting": self.summarize_interesting(nodes),
                }
            )
            if before == after:
                break
        return report

    def map_x(self) -> dict:
        report: dict = {"package": X_PKG, "phases": {}}
        self.device.launch(X_PKG)
        nodes = self.wait_for(lambda ns: len(ns) > 5, timeout_s=15.0) or []
        nodes = self.capture_state("x_home", nodes)
        report["phases"]["home"] = self.summarize_interesting(nodes)

        # Drawer / avatar often content-desc "Show navigation drawer" / similar.
        self.find_and_tap(
            "x_open_drawer",
            nodes,
            contains=(
                "navigation drawer",
                "Show navigation",
                "Account",
                "akun",
                "Profile",
                "Profil",
            ),
            resource_suffixes=("toolbar", "drawer", "avatar"),
            required=False,
        )
        nodes = self.capture_state("x_after_drawer")
        self.find_and_tap(
            "x_click_profile",
            nodes,
            exact=X_PROFILE_LABELS,
            prefer_bottom=False,
        )
        nodes = self.wait_for(
            lambda ns: bool(match_exact(ns, ("Edit profile", "Edit profil", "Posts", "Postingan"))),
            timeout_s=12.0,
        ) or []
        nodes = self.capture_state("x_profile_home", nodes)
        report["phases"]["profile"] = extract_x_profile_text(nodes)
        return report

    def write_report(self, payload: dict) -> Path:
        path = self.out_dir / "flow_map.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path


def viewport_signature(nodes: list[NodeHit]) -> str:
    parts = []
    for node in nodes:
        if not (node.text or node.content_desc or node.resource_id):
            continue
        parts.append(
            f"{node.text}|{node.content_desc}|{node.resource_id}|{node.bounds}"
        )
    return "\n".join(parts)


def guess_post_count(nodes: list[NodeHit]) -> int | None:
    labels = {"posts", "postingan", "kiriman"}
    texts = [(n.text, n.bounds) for n in nodes if n.text]
    for i, (text, bounds) in enumerate(texts):
        if text.casefold() not in labels:
            continue
        # Prefer a nearby numeric neighbor.
        for j in range(max(0, i - 3), min(len(texts), i + 4)):
            if j == i:
                continue
            candidate = texts[j][0].replace(",", "").replace(".", "")
            if candidate.isdigit():
                return int(candidate)
    return None


def extract_x_profile_text(nodes: list[NodeHit]) -> dict:
    texts = [n.text for n in nodes if n.text]
    username = next((t for t in texts if t.startswith("@") and len(t) > 1), None)
    return {
        "sample_texts": texts[:40],
        "username_guess": username,
        "replies_hits": [node_as_dict(n) for n in match_exact(nodes, REPLIES_LABELS)[:5]],
        "posts_hits": [node_as_dict(n) for n in match_exact(nodes, POSTS_TAB_LABELS)[:5]],
    }


def resolve_serial(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    out = subprocess.run(
        ["adb", "devices"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    serials = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    if not serials:
        raise RuntimeError("no adb device connected")
    if len(serials) > 1:
        print(f"[info] multiple devices {serials}; using {serials[0]}")
    return serials[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Map IG/X social crawl UI flow via ADB")
    parser.add_argument("--serial", default=None)
    parser.add_argument(
        "--targets",
        default="instagram",
        help="comma list: instagram,x",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output directory (default: temp_crawl/flow_map_<ts>)",
    )
    args = parser.parse_args()

    serial = resolve_serial(args.serial)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else ROOT_OUT / f"flow_map_{ts}"
    ROOT_OUT.mkdir(parents=True, exist_ok=True)

    device = AdbDevice(serial)
    mapper = FlowMapper(device, out_dir)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "serial": serial,
        "display_size": {"width": device.size[0], "height": device.size[1]},
        "targets": {},
        "steps": [],
        "note": "Mapping only — production Kotlin agent was not modified.",
    }

    targets = [t.strip().lower() for t in args.targets.split(",") if t.strip()]
    try:
        if "instagram" in targets:
            payload["targets"]["instagram"] = mapper.map_instagram()
        if "x" in targets or "twitter" in targets:
            payload["targets"]["x"] = mapper.map_x()
    except Exception as exc:  # noqa: BLE001
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
    print(f"[done] screenshots -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
