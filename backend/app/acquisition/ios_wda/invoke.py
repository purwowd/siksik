#!/usr/bin/env python3
"""Run one in-tree WDA social flow from a JSON job file.

Host automation writes the job; this process has no operator -- flags.
Executed with the in-repo ios-media-puller venv so pymobiledevice3 stays isolated.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

FLOW_FUNCS = {
    "ig-profile": ("flows.ig_profile", "run_ig_profile"),
    "x-profile": ("flows.x_profile", "run_x_profile"),
    "fb-profile": ("flows.fb_profile", "run_fb_profile"),
}


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: invoke.py <job.json>\n")
        return 2
    job_path = Path(sys.argv[1])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    automator_root = Path(str(job["automator_root"]))
    sys.path.insert(0, str(automator_root))
    os.environ["UDID"] = str(job.get("udid") or "")
    os.environ["IOS_SKIP_WDA_INSTALL"] = "1"
    os.environ["IOS_ARCHIVE_MAX_SCREENSHOTS"] = str(int(job.get("archive_shots") or 3))
    os.environ["IOS_X_MAX_SCREENSHOTS"] = str(int(job.get("x_shots") or 3))
    os.environ["IOS_POST_MAX_SCREENSHOTS"] = str(int(job.get("post_shots") or job.get("archive_shots") or 3))
    os.environ["IOS_COMMENT_MAX_SCREENS"] = str(int(job.get("comment_shots") or 3))
    os.environ["IOS_FB_MAX_PAGES"] = str(int(job.get("x_shots") or 3))
    flow = str(job["flow"])
    mapped = FLOW_FUNCS.get(flow)
    if mapped is None:
        sys.stderr.write(f"unknown flow: {flow}\n")
        return 2
    module_name, func_name = mapped
    module = __import__(module_name, fromlist=[func_name])
    func = getattr(module, func_name)
    args = SimpleNamespace(
        output=str(job["output_dir"]),
        http=str(job["wda_url"]),
        timeout=float(job.get("timeout_s") or 30.0),
        stop_after=str(job.get("stop_after") or "all"),
        skip_wda_install=True,
        install_wda=False,
        cmd=flow,
    )
    return int(asyncio.run(func(args)))


if __name__ == "__main__":
    raise SystemExit(main())
