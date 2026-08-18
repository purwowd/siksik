#!/usr/bin/env python3
"""Probe portable Instagram header-Back strategies on the connected phone.

Does not hardcode Infinix pixels. Each strategy looks up the control live
(resource-id, content-desc, header-band ImageView, KEYCODE_BACK, %-geometry).
Output: <siksik>/temp_crawl/ig_back_probe_<timestamp>/
"""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from map_instagram_comments import (
    BACK_LABELS,
    CommentsMapper,
    header_back_candidates,
    on_settings,
    on_stories_archive,
)
from map_social_flow import (
    ARCHIVE_LABELS,
    AdbDevice,
    StepRecord,
    match_resource_suffix,
    node_as_dict,
    pick_best,
    resolve_serial,
)

ROOT_OUT = Path(__file__).resolve().parents[1] / "temp_crawl"
BACK_RESOURCE = "action_bar_button_back"


@dataclass
class ProbeResult:
    strategy: str
    elapsed_ms: int
    left_archive: bool
    landed_settings: bool
    detail: str
    matched: dict | None


def header_image_candidates(nodes, size: tuple[int, int]) -> list:
    w, h = size
    max_top = int(h * 0.18)
    max_right = int(w * 0.22)
    hits = [
        n
        for n in nodes
        if n.clickable
        and "Image" in n.class_name
        and n.bounds[1] < max_top
        and n.bounds[2] < max_right
    ]
    return sorted(hits, key=lambda n: (n.bounds[0], n.bounds[1]))


class BackProbe(CommentsMapper):
    def restore_archive(self, nodes) -> list:
        if on_stories_archive(nodes):
            return nodes
        if on_settings(nodes):
            self.find_and_tap("probe_reopen_archive", nodes, exact=ARCHIVE_LABELS)
            time.sleep(1.2)
            self.wait_for(on_stories_archive, timeout_s=10.0)
            return self.capture_state("probe_archive_restored")
        try:
            return self.open_archive(nodes)
        except RuntimeError:
            nodes = self.reach_profile()
            return self.open_archive(nodes)

    def apply(self, name: str, nodes) -> ProbeResult:
        started = time.monotonic()
        matched = None
        detail = ""
        if name == "press_back":
            self.device.run_shell("input keyevent KEYCODE_BACK")
            detail = "KEYCODE_BACK"
        elif name == "dump_resource_id":
            hits = match_resource_suffix(nodes, (BACK_RESOURCE,))
            chosen = pick_best(hits)
            if chosen is None:
                return ProbeResult(name, 0, False, False, "no resource-id match", None)
            matched = node_as_dict(chosen)
            self.device.tap(*chosen.center)
            detail = f"tapped {chosen.center} id={chosen.resource_id}"
        elif name == "dump_desc_back":
            chosen = pick_best(header_back_candidates(nodes, self.device.size))
            if chosen is None:
                return ProbeResult(name, 0, False, False, "no Back/Kembali in header", None)
            matched = node_as_dict(chosen)
            self.device.tap(*chosen.center)
            detail = f"tapped {chosen.center} desc={chosen.content_desc!r}"
        elif name == "dump_header_image":
            chosen = pick_best(header_image_candidates(nodes, self.device.size))
            if chosen is None:
                return ProbeResult(name, 0, False, False, "no header ImageView", None)
            matched = node_as_dict(chosen)
            self.device.tap(*chosen.center)
            detail = f"tapped {chosen.center} class={chosen.class_name}"
        elif name == "geometry_percent":
            w, h = self.device.size
            x, y = int(w * 0.08), int(h * 0.07)
            self.device.tap(x, y)
            detail = f"percent tap ({x},{y}) of {w}x{h}"
        else:
            raise RuntimeError(name)
        time.sleep(1.4)
        after = self.capture_state(f"after_{name}")
        elapsed = int((time.monotonic() - started) * 1000)
        result = ProbeResult(
            strategy=name,
            elapsed_ms=elapsed,
            left_archive=not on_stories_archive(after),
            landed_settings=on_settings(after),
            detail=detail,
            matched=matched,
        )
        self.steps.append(
            StepRecord(
                step=f"probe_{name}",
                ok=result.left_archive,
                detail=f"{detail} left={result.left_archive} settings={result.landed_settings}",
                matched=[matched] if matched else [],
            )
        )
        print(
            f"[probe] {name}: left_archive={result.left_archive} "
            f"settings={result.landed_settings} {elapsed}ms {detail}"
        )
        self.restore_archive(after)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe portable IG header Back strategies")
    parser.add_argument("--serial", default=None)
    args = parser.parse_args()
    serial = resolve_serial(args.serial)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT_OUT / f"ig_back_probe_{ts}"
    ROOT_OUT.mkdir(parents=True, exist_ok=True)
    device = AdbDevice(serial)
    device.run_shell("input keyevent KEYCODE_WAKEUP")
    time.sleep(0.3)
    device.run_shell("input keyevent 82")
    time.sleep(0.3)
    device.swipe_percent(0.50, 0.82, 0.50, 0.28, duration_ms=280)
    time.sleep(0.6)
    probe = BackProbe(device, out_dir)
    nodes = probe.reach_profile()
    nodes = probe.open_archive(nodes)
    if not on_stories_archive(nodes):
        raise RuntimeError("did not reach Stories archive")
    order = (
        "press_back",
        "dump_resource_id",
        "dump_desc_back",
        "dump_header_image",
        "geometry_percent",
    )
    results = []
    for name in order:
        nodes = probe.capture_state(f"before_{name}")
        results.append(asdict(probe.apply(name, nodes)))
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "serial_redacted": True,
        "display_size": {"width": device.size[0], "height": device.size[1]},
        "note": (
            "Portable selector probe. Coordinates come from the live dump, "
            "not a hardcoded Infinix mapping."
        ),
        "results": results,
        "steps": [asdict(s) for s in probe.steps],
    }
    report = probe.write_report(payload)
    print(f"[done] {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
