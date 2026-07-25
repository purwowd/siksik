#!/usr/bin/env python3
"""Map Instagram Stories archive: save → scroll↓ ×3 → save. No story item clicks.

Requires ADB device. Output under <siksik>/temp_crawl/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# scripts/ → siksik/
ROOT = Path(__file__).resolve().parents[1] / "temp_crawl"


def adb(serial: str, *args: str, timeout: float = 50.0) -> bytes:
    cmd = ["adb", "-s", serial, *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).decode(errors="replace")[:500]
        raise RuntimeError(f"adb {' '.join(args)} failed: {err}")
    return proc.stdout


def shell(serial: str, cmd: str, timeout: float = 50.0) -> str:
    return adb(serial, "shell", cmd, timeout=timeout).decode(errors="replace")


def wm_size(serial: str) -> tuple[int, int]:
    match = re.search(r"(\d+)x(\d+)", shell(serial, "wm size"))
    if not match:
        raise RuntimeError("wm size parse failed")
    return int(match.group(1)), int(match.group(2))


def parse_nodes(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    nodes: list[dict] = []
    for node in root.iter("node"):
        bounds_raw = node.attrib.get("bounds", "")
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_raw or "")
        if not match:
            continue
        left, top, right, bottom = map(int, match.groups())
        nodes.append(
            {
                "text": (node.attrib.get("text") or "").strip(),
                "desc": (node.attrib.get("content-desc") or "").strip(),
                "id": (node.attrib.get("resource-id") or "").strip(),
                "clickable": node.attrib.get("clickable") == "true",
                "bounds": (left, top, right, bottom),
                "center": ((left + right) // 2, (top + bottom) // 2),
            }
        )
    return nodes


def dump(serial: str, out: Path, tag: str) -> list[dict]:
    remote = "/sdcard/window_dump.xml"
    shell(serial, f"uiautomator dump {remote}")
    text = adb(serial, "exec-out", "cat", remote).decode("utf-8", errors="replace")
    (out / f"{tag}.xml").write_text(text, encoding="utf-8")
    return parse_nodes(text)


def shot(serial: str, out: Path, name: str) -> tuple[Path, str]:
    raw = adb(serial, "exec-out", "screencap", "-p")
    idx = raw.find(b"\x89PNG")
    if idx > 0:
        raw = raw[idx:]
    path = out / f"{name}.png"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    print(f"[save] {path.name} sha={digest}")
    return path, digest


def exact(nodes: list[dict], labels: tuple[str, ...]) -> list[dict]:
    wanted = {label.casefold() for label in labels}
    return [
        node
        for node in nodes
        if node["text"].casefold() in wanted or node["desc"].casefold() in wanted
    ]


def id_suffix(nodes: list[dict], suffixes: tuple[str, ...]) -> list[dict]:
    return [
        node
        for node in nodes
        if any(node["id"].endswith(suffix) for suffix in suffixes)
    ]


def tap(serial: str, node: dict, label: str) -> None:
    x, y = node["center"]
    print(
        f"[tap] {label} ({x},{y}) "
        f"text={node['text']!r} desc={node['desc']!r} "
        f"id={node['id'].split('/')[-1]!r}"
    )
    shell(serial, f"input tap {x} {y}")
    time.sleep(1.6)


def markers(nodes: list[dict]) -> list[str]:
    found: list[str] = []
    for node in nodes:
        text = node["text"]
        if re.fullmatch(r"\d{1,2} \w{3}", text):
            found.append(text)
        elif text in {"Memories", "Stories archive", "Arsip cerita", "On this day"}:
            found.append(text)
        elif re.fullmatch(r"\d:\d{2}", text):
            found.append(text)
    return found


def is_viewer(nodes: list[dict]) -> bool:
    return any("reel_viewer" in node["id"] for node in nodes)


def is_stories_grid(nodes: list[dict]) -> bool:
    if is_viewer(nodes):
        return False
    blob = " ".join(f"{node['text']} {node['desc']}" for node in nodes).casefold()
    return "stories archive" in blob or "arsip cerita" in blob


def is_own_profile(nodes: list[dict]) -> bool:
    blob = " ".join(f"{node['text']} {node['desc']}" for node in nodes).casefold()
    return any(
        token in blob
        for token in (
            "edit profile",
            "ubah profil",
            "share profile",
            "bagikan profil",
            "sunting profil",
        )
    )


def resolve_serial(explicit: str | None) -> str:
    if explicit:
        return explicit
    out = subprocess.run(
        ["adb", "devices"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    serials = [
        line.split()[0]
        for line in out.splitlines()[1:]
        if len(line.split()) >= 2 and line.split()[1] == "device"
    ]
    if not serials:
        raise RuntimeError("no adb device")
    return serials[0]


def open_stories_archive(serial: str, out: Path, width: int, height: int) -> list[dict]:
    shell(
        serial,
        "monkey -p com.instagram.android -c android.intent.category.LAUNCHER 1",
    )
    time.sleep(2.2)
    nodes = dump(serial, out, "01_launch")
    shot(serial, out, "01_launch")

    for _ in range(4):
        if not is_viewer(nodes):
            break
        shell(serial, "input keyevent 4")
        time.sleep(1.0)
        nodes = dump(serial, out, "01_back_viewer")

    for attempt in range(8):
        if is_own_profile(nodes):
            print("[ok] own profile")
            break
        tabs = exact(nodes, ("Profile", "Profil")) or id_suffix(
            nodes, ("profile_tab",)
        )
        if tabs:
            tap(serial, tabs[-1], f"profile#{attempt}")
        else:
            shell(serial, f"input tap {int(width * 0.9)} {int(height * 0.95)}")
            time.sleep(1.5)
        nodes = dump(serial, out, f"02_profile_{attempt}")
    else:
        shot(serial, out, "error_no_profile")
        raise RuntimeError("own profile not reached")

    shot(serial, out, "02_profile")
    opts = exact(nodes, ("Options", "Opsi", "More options", "Opsi lainnya"))
    if not opts:
        top = [
            node
            for node in nodes
            if node["clickable"]
            and node["bounds"][1] < 320
            and node["bounds"][0] > width * 0.65
            and "story" not in node["desc"].casefold()
            and "notification" not in node["id"]
            and "threads" not in node["desc"].casefold()
        ]
        if not top:
            raise RuntimeError("Options not found")
        opts = [max(top, key=lambda node: node["bounds"][0])]
    tap(serial, opts[0], "options")
    nodes = dump(serial, out, "03_options")
    shot(serial, out, "03_options")

    archives: list[dict] = []
    for scroll_i in range(5):
        archives = exact(nodes, ("Archive", "Arsip"))
        if archives:
            break
        shell(
            serial,
            f"input swipe {width // 2} {int(height * 0.75)} "
            f"{width // 2} {int(height * 0.35)} 280",
        )
        time.sleep(1.0)
        nodes = dump(serial, out, f"03_options_scroll_{scroll_i}")
    if not archives:
        raise RuntimeError("Archive row not found")
    tap(
        serial,
        max(archives, key=lambda node: node["bounds"][2] - node["bounds"][0]),
        "archive_row",
    )

    for attempt in range(20):
        time.sleep(0.7)
        nodes = dump(serial, out, f"04_archive_{attempt}")
        if is_viewer(nodes):
            print("[warn] story viewer opened — back")
            shell(serial, "input keyevent 4")
            continue
        if is_stories_grid(nodes):
            print("[ok] Stories archive grid")
            shot(serial, out, "04_stories_archive_grid")
            return nodes

        posts = exact(nodes, ("Posts archive", "Arsip postingan"))
        if posts:
            tap(serial, posts[0], "posts_archive_header")
            time.sleep(0.8)
            nodes = dump(serial, out, "04_picker")
            stories = exact(
                nodes,
                ("Stories archive", "Story archive", "Arsip cerita", "Arsip Cerita"),
            )
            if stories:
                tap(serial, stories[0], "pick_stories")
            continue

        header = [
            node
            for node in exact(nodes, ("Archive", "Arsip"))
            if node["bounds"][1] < 450
        ]
        if header:
            tap(serial, header[0], "archive_header")
            time.sleep(0.8)
            nodes = dump(serial, out, "04_header_picker")
            stories = exact(
                nodes,
                ("Stories archive", "Story archive", "Arsip cerita", "Arsip Cerita"),
            )
            if stories:
                tap(serial, stories[0], "pick_stories_header")

    shot(serial, out, "error_no_grid")
    raise RuntimeError("Stories archive grid not reached")


def scroll_save_loop(
    serial: str,
    out: Path,
    nodes: list[dict],
    width: int,
    height: int,
) -> dict:
    # Dismiss Memories if it blocks the lower grid.
    memories = next((node for node in nodes if node["text"] == "Memories"), None)
    if memories:
        shell(serial, f"input tap {int(width * 0.93)} {memories['center'][1]}")
        time.sleep(1.0)
        nodes = dump(serial, out, "05_memories_dismiss")
        shot(serial, out, "05_after_memories_dismiss")

    _, sha0 = shot(serial, out, "10_save_0")
    prev = markers(nodes)
    print(f"[markers] 0 -> {prev}")
    results = [
        {
            "i": 0,
            "file": "10_save_0.png",
            "sha": sha0,
            "markers": prev,
        }
    ]

    for index in range(1, 4):
        # Left gutter swipe: scroll list without tapping thumbnails.
        x = 22
        y1 = int(height * 0.62)
        y2 = int(height * 0.20)
        print(f"[scroll] {index}/3 gutter {x},{y1} -> {x},{y2}")
        shell(serial, f"input swipe {x} {y1} {x} {y2} 150")
        time.sleep(1.8)
        nodes = dump(serial, out, f"11_after_scroll_{index}")
        if is_viewer(nodes):
            print("[warn] swipe opened viewer — KEYCODE_BACK")
            shell(serial, "input keyevent 4")
            time.sleep(1.2)
            nodes = dump(serial, out, f"11_after_scroll_{index}_recovered")
        path, sha = shot(serial, out, f"11_save_after_scroll_{index}")
        current = markers(nodes)
        changed = current != prev
        print(f"[markers] {index} changed={changed} {prev} -> {current}")
        results.append(
            {
                "i": index,
                "file": path.name,
                "sha": sha,
                "markers": current,
                "markers_changed": changed,
                "sha_changed": sha != results[-1]["sha"],
                "still_grid": is_stories_grid(nodes),
            }
        )
        prev = current

    return {
        "pattern": "save → scroll → save → scroll → save → scroll → save",
        "no_item_clicks": True,
        "any_marker_change": any(item.get("markers_changed") for item in results[1:]),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", default=None)
    parser.add_argument(
        "--assume-grid",
        action="store_true",
        help="Skip navigation; device must already show Stories archive grid",
    )
    args = parser.parse_args()
    serial = resolve_serial(args.serial)
    width, height = wm_size(serial)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / f"archive_real_scroll_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"serial={serial} size={width}x{height} out={out}")

    try:
        if args.assume_grid:
            nodes = dump(serial, out, "00_assume")
            if not is_stories_grid(nodes):
                raise RuntimeError(
                    "not on Stories archive grid; open it manually or omit --assume-grid"
                )
            shot(serial, out, "00_assume_grid")
        else:
            nodes = open_stories_archive(serial, out, width, height)
        scroll_report = scroll_save_loop(serial, out, nodes, width, height)
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "serial": serial,
            "out": str(out),
            **scroll_report,
        }
        (out / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not scroll_report["any_marker_change"]:
            print(
                "[warn] markers never changed — content likely did not scroll "
                "(end of list, Memories blocking, or swipe ineffective)",
                file=sys.stderr,
            )
            return 2
        return 0
    except Exception as exc:  # noqa: BLE001
        try:
            shot(serial, out, "error")
        except Exception:  # noqa: BLE001
            pass
        (out / "report.json").write_text(
            json.dumps(
                {
                    "error": str(exc),
                    "out": str(out),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[failed] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
