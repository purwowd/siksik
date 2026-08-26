"""iOS IG/X/FB visible-UI social crawl via in-repo WDA flows.

Isolated from Android agent/UiAutomator paths. Maps iOS bundle outputs onto the
existing report package IDs so reports.SOCIAL_PACKAGES / SOCIAL_SCOPES stay
unchanged. Flows are invoked with a JSON job file (no operator -- flags).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from app.acquisition.agent_client import InventoryRecordV1
from app.acquisition.contracts import ProgressCallback
from app.acquisition.errors import ErrorCategory, AcquisitionError, acquisition_error
from app.acquisition.process import run_process
from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.ios_social")

IOS_UDID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}$")
AGENT_VERSION = "ios-media-puller-wda"

# Report / contract package IDs (Android-shaped; reports already keyed on these).
PACKAGE_INSTAGRAM = "com.instagram.android"
PACKAGE_X = "com.twitter.android"
PACKAGE_FACEBOOK = "com.facebook.katana"

FLOW_BY_PACKAGE = {
    PACKAGE_INSTAGRAM: "ig-profile",
    PACKAGE_X: "x-profile",
    PACKAGE_FACEBOOK: "fb-profile",
}

_INVOKE_SCRIPT = Path(__file__).resolve().parent / "ios_wda" / "invoke.py"

def _ios_device_ref(udid: str) -> str:
    digest = hashlib.sha256(f"siksik-ios-device:{udid}".encode("utf-8")).hexdigest()
    return f"ios:{digest[:24]}"


def validate_ios_udid(udid: str) -> str:
    if not isinstance(udid, str) or not IOS_UDID_RE.fullmatch(udid):
        raise acquisition_error(
            ErrorCategory.VALIDATION_ERROR,
            "UDID perangkat iOS tidak valid.",
        )
    return udid


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace(".", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


_STACK_STATE_DIR = Path(os.environ.get("IOS_STACK_STATE_DIR", "/tmp/ios-media-puller-stack"))


def _puller_paths() -> tuple[Path, Path, Path]:
    root = settings.ios_media_puller_path.resolve()
    python_bin = root / ".venv" / "bin" / "python"
    automator = root / "ios_automator" / "automator.py"
    return root, python_bin, automator


def stack_udid_matches(udid: str, *, state_dir: Path | None = None) -> bool:
    """True if the last successful WDA stack was bound to this iPhone."""
    path = (state_dir or _STACK_STATE_DIR) / "udid"
    try:
        bound = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(bound) and bound == udid


def ios_social_toolchain_ready() -> dict[str, bool]:
    root, python_bin, _automator = _puller_paths()
    stack = root / "ios_automator" / "scripts" / "run_stack.sh"
    flows = root / "ios_automator" / "flows"
    return {
        "puller_root": root.is_dir(),
        "venv_python": python_bin.is_file(),
        "invoke_script": _INVOKE_SCRIPT.is_file(),
        "run_stack": stack.is_file(),
        "ig_flow": (flows / "ig_profile.py").is_file(),
        "x_flow": (flows / "x_profile.py").is_file(),
        "fb_flow": (flows / "fb_profile.py").is_file(),
    }


async def _wda_ready(url: str, timeout_s: float = 3.0) -> bool:
    """Probe WDA /status the same way run_stack.sh does (curl).

    Python urllib and http.client often get IncompleteRead / RemoteDisconnected
    from go-ios forward even while `curl -sf /status` returns 200. That made the
    host skip social after the stack had already logged WDA ready.
    """
    parsed = urlparse(url.rstrip("/") + "/status")
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8100)
    path = parsed.path or "/status"
    status_url = f"http://{host}:{port}{path}"

    def _check() -> bool:
        import subprocess

        try:
            curl = subprocess.run(
                [
                    "curl",
                    "-sf",
                    "--max-time",
                    str(max(1, int(timeout_s))),
                    "--connect-timeout",
                    "2",
                    status_url,
                ],
                capture_output=True,
                timeout=timeout_s + 2,
                check=False,
            )
            if curl.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass

        import http.client

        conn: http.client.HTTPConnection | None = None
        try:
            conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
            conn.request("GET", path, headers={"Accept": "*/*", "Host": f"{host}:{port}"})
            response = conn.getresponse()
            status = int(response.status)
            try:
                response.read()
            except http.client.IncompleteRead:
                pass
            return 200 <= status < 300
        except (OSError, ValueError, TimeoutError, http.client.HTTPException):
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass

    return await asyncio.to_thread(_check)


async def ensure_ios_wda_stack(*, udid: str, restart: bool = False) -> str:
    """Ensure WDA HTTP is reachable for this UDID; start run_stack.sh if needed.

    Fast-path only when :8100 is up AND the last stack was recorded for the same
    iPhone. `restart=True` kills and relaunches WDA (needed between apps: launching
    IG often drops the WDA HTTP listener, so X/FB then write only job.json).
    """
    base = settings.ios_social_wda_url.rstrip("/")
    if not restart:
        already = await _wda_ready(base)
        if already and stack_udid_matches(udid):
            return base

    root, _python_bin, _automator = _puller_paths()
    stack = root / "ios_automator" / "scripts" / "run_stack.sh"
    if not stack.is_file():
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "Script run_stack.sh iOS tidak ditemukan.",
        )

    env = {
        **os.environ,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
        "UDID": udid,
        "IOS_ENSURE_DEV_IMAGE": "1",
        "IOS_SKIP_WDA_INSTALL": "0",
        "IOS_STACK_STATE_DIR": str(_STACK_STATE_DIR),
        "IOS_FORCE_WDA_RESTART": "1" if restart else "0",
        "WDA_PORT": base.rsplit(":", 1)[-1] if ":" in base else "8100",
    }
    logger.info(
        "ios_wda_stack_starting",
        extra={
            "session_id": None,
            "device_ref": _ios_device_ref(udid),
            "timeout_ms": int(settings.ios_social_wda_boot_timeout_s * 1000),
        },
    )
    stack_result = await run_process(
        ["bash", str(stack)],
        timeout=settings.ios_social_wda_boot_timeout_s,
        cwd=root,
        env=env,
        check=False,
        output_limit_bytes=512 * 1024,
        operation="ios_wda_stack",
        not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
        timeout_category=ErrorCategory.ADB_TIMEOUT,
        failure_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
    )
    # Stack's curl may succeed a few seconds before the host probe; poll.
    ready = False
    for _ in range(15):
        ready = await _wda_ready(base, timeout_s=2.0)
        if ready:
            break
        await asyncio.sleep(1)
    if not ready:
        logger.warning(
            "ios_wda_stack_not_ready",
            extra={
                "device_ref": _ios_device_ref(udid),
                "dependency_exit_code": stack_result.returncode,
            },
        )
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "WebDriverAgent iOS tidak siap. Cek USB Trust, Developer Mode, internet (Developer Image), dan Trust profil WDA.",
            retryable=True,
        )
    return base


def build_wda_flow_job(
    *,
    flow: str,
    output_dir: Path,
    wda_url: str,
    udid: str,
    archive_shots: int,
    x_shots: int,
) -> dict[str, Any]:
    root, _python_bin, _automator = _puller_paths()
    return {
        "automator_root": str(root / "ios_automator"),
        "flow": flow,
        "wda_url": wda_url,
        "output_dir": str(output_dir),
        "udid": udid,
        "timeout_s": 90.0,
        "stop_after": "all",
        "archive_shots": archive_shots,
        "x_shots": x_shots,
    }


async def _run_flow(
    *,
    flow: str,
    output_dir: Path,
    wda_url: str,
    udid: str,
    archive_shots: int,
    x_shots: int,
) -> int:
    root, python_bin, _automator = _puller_paths()
    if not python_bin.is_file() or not _INVOKE_SCRIPT.is_file():
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "ios-media-puller venv / invoke script tidak siap.",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    job = build_wda_flow_job(
        flow=flow,
        output_dir=output_dir,
        wda_url=wda_url,
        udid=udid,
        archive_shots=archive_shots,
        x_shots=x_shots,
    )
    job_path = output_dir / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
        "IOS_SKIP_WDA_INSTALL": "1",
        "IOS_TEMP_CRAWL_SESSION": str(os.environ.get("IOS_TEMP_CRAWL_SESSION") or ""),
    }
    result = await run_process(
        [str(python_bin), str(_INVOKE_SCRIPT), str(job_path)],
        timeout=settings.ios_social_flow_timeout_s,
        cwd=root,
        env=env,
        check=False,
        output_limit_bytes=512 * 1024,
        operation=f"ios_social_{flow}",
        not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
        timeout_category=ErrorCategory.ADB_TIMEOUT,
        failure_category=ErrorCategory.AGENT_UNREACHABLE,
    )
    log_path = output_dir / "invoke.log"
    try:
        log_path.write_text(
            (result.stdout or "") + "\n--- stderr ---\n" + (result.stderr or ""),
            encoding="utf-8",
        )
    except OSError:
        pass
    return result.returncode


def _load_profile(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "profile.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _profile_normalized_text(profile: dict[str, Any]) -> str:
    lines: list[str] = []
    username = str(profile.get("username") or "").strip()
    if username:
        lines.append(username if username.startswith("@") else f"@{username}")
    display = str(profile.get("display_name") or "").strip()
    if display:
        lines.append(display)
    bio = str(profile.get("bio") or "").strip()
    if bio:
        lines.append(bio)
    for key, label in (
        ("posts", "posts"),
        ("followers", "followers"),
        ("friends", "friends"),
        ("following", "following"),
    ):
        number = _as_int(profile.get(key))
        if number is not None:
            lines.append(f"{number} {label}")
    return "\n".join(lines)[:65536]


def _build_visible_record(
    *,
    session_id: str,
    crawl_id: str,
    package_name: str,
    social_scope: str,
    screen_sequence: int,
    normalized_text: str | None,
    screenshot_path: Path | None,
    artifacts_dir: Path,
    profile: dict[str, Any] | None = None,
) -> tuple[InventoryRecordV1, Path | None]:
    observed = _utc_now_iso()
    record_id = f"ios_{uuid.uuid4().hex}"
    attachment_ids: list[str] = []
    staged_shot: Path | None = None
    if screenshot_path is not None and screenshot_path.is_file():
        shot_id = f"shot_{uuid.uuid4().hex[:16]}"
        staged_shot = artifacts_dir / f"{shot_id}.png"
        shutil.copy2(screenshot_path, staged_shot)
        attachment_ids = [shot_id]

    metadata: dict[str, Any] = {
        "package_name": package_name,
        "social_scope": social_scope,
        "window_id": -1,
        "activity_context": "ios_wda",
        "event_type": 2048,
        "screen_sequence": screen_sequence,
        "nodes": [],
        "screenshot_ids": list(attachment_ids),
        "warning_codes": [],
    }
    if social_scope == "own_profile" and profile is not None:
        username = str(profile.get("username") or "").strip().lstrip("@") or None
        display = str(profile.get("display_name") or "").strip() or None
        bio = str(profile.get("bio") or "").strip() or None
        metrics = {
            "posts": _as_int(profile.get("posts")),
            "followers": _as_int(profile.get("followers")),
            "friends": _as_int(profile.get("friends")),
            "following": _as_int(profile.get("following")),
        }
        if any(value is not None for value in metrics.values()):
            metadata["profile_metrics"] = metrics
        if username:
            metadata["profile_username"] = username[:64]
        if display:
            metadata["profile_display_name"] = display[:256]
        if bio:
            metadata["profile_bio"] = bio[:4096]
        metadata["profile_links"] = []

    payload = {
        "schema_version": 1,
        "record_id": record_id,
        "crawl_id": crawl_id,
        "siksik_session_id": session_id,
        "source_kind": "visible_ui",
        "source_app": package_name,
        "source_locator": f"ios_wda:{package_name}:{social_scope}:{screen_sequence}",
        "observed_at": observed,
        "source_created_at": observed,
        "source_modified_at": observed,
        "normalized_text": normalized_text,
        "metadata": metadata,
        "attachment_ids": attachment_ids,
        "content_sha256": None,
        "preprocessing": None,
        "selection": None,
        "provenance": {
            "source_adapter": "ios_wda_visible_ui",
            "enumeration_method": "ios_webdriveragent",
            "agent_version": AGENT_VERSION,
            "original_staged": False,
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    payload["content_sha256"] = _sha256_text(raw)
    record = InventoryRecordV1.model_validate(payload)
    return record, staged_shot


def _load_jsonl_first(*paths: Path) -> list[dict[str, Any]]:
    for path in paths:
        if path.is_file():
            return _load_jsonl(path)
    return []


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _copy_flow_debug(out_dir: Path, flow: str, session_id: str) -> Path | None:
    """Keep flow artifacts under siksik/temp_crawl/ios_wda/<session>/<flow>."""
    dest = settings.android_social_debug_dir / "ios_wda" / session_id / flow
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        if out_dir.is_dir():
            shutil.copytree(out_dir, dest)
        return dest
    except OSError:
        return None


def _sidecar_text(out_dir: Path, screenshot: Path | None, stem: str) -> str | None:
    candidates: list[Path] = [out_dir / f"{stem}.txt"]
    if screenshot is not None:
        candidates.insert(0, screenshot.with_suffix(".txt"))
    for path in candidates:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text[:65536]
    return None


def records_from_ig_output(
    *,
    session_id: str,
    crawl_id: str,
    out_dir: Path,
    artifacts_dir: Path,
) -> list[tuple[InventoryRecordV1, Path | None]]:
    profile = _load_profile(out_dir)
    built: list[tuple[InventoryRecordV1, Path | None]] = []
    profile_png = out_dir / "profile.png"
    built.append(
        _build_visible_record(
            session_id=session_id,
            crawl_id=crawl_id,
            package_name=PACKAGE_INSTAGRAM,
            social_scope="own_profile",
            screen_sequence=1,
            normalized_text=_profile_normalized_text(profile) or None,
            screenshot_path=profile_png if profile_png.is_file() else None,
            artifacts_dir=artifacts_dir,
            profile=profile,
        )
    )
    archive_files = sorted(out_dir.glob("archive_*.png"))
    if not archive_files and (out_dir / "archive.png").is_file():
        archive_files = [out_dir / "archive.png"]
    for index, shot in enumerate(archive_files, start=1):
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_INSTAGRAM,
                social_scope="own_story_archive",
                screen_sequence=index,
                normalized_text=_sidecar_text(out_dir, shot, shot.stem),
                screenshot_path=shot,
                artifacts_dir=artifacts_dir,
            )
        )
    for row in _load_jsonl_first(out_dir / "posts.jsonl", out_dir / "posts.jsonl"):
        shot_name = str(row.get("screenshot") or "")
        shot = out_dir / shot_name if shot_name else None
        if shot is not None and not shot.is_file():
            shot = None
        text = str(row.get("text") or "").strip() or _sidecar_text(
            out_dir, shot, f"post_{int(row.get('index') or 0):02d}"
        )
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_INSTAGRAM,
                social_scope="own_posts",
                screen_sequence=int(row.get("index") or 1),
                normalized_text=text or None,
                screenshot_path=shot,
                artifacts_dir=artifacts_dir,
            )
        )
    for row in _load_jsonl_first(out_dir / "comments.jsonl", out_dir / "comments.jsonl"):
        shot_name = str(row.get("screenshot") or "")
        shot = out_dir / shot_name if shot_name else None
        if shot is not None and not shot.is_file():
            shot = None
        text = str(row.get("text") or "").strip() or None
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_INSTAGRAM,
                social_scope="own_comments",
                screen_sequence=int(row.get("index") or 1),
                normalized_text=text,
                screenshot_path=shot,
                artifacts_dir=artifacts_dir,
            )
        )
    return built


def records_from_x_output(
    *,
    session_id: str,
    crawl_id: str,
    out_dir: Path,
    artifacts_dir: Path,
) -> list[tuple[InventoryRecordV1, Path | None]]:
    profile = _load_profile(out_dir)
    built: list[tuple[InventoryRecordV1, Path | None]] = []
    profile_png = out_dir / "profile.png"
    built.append(
        _build_visible_record(
            session_id=session_id,
            crawl_id=crawl_id,
            package_name=PACKAGE_X,
            social_scope="own_profile",
            screen_sequence=1,
            normalized_text=_profile_normalized_text(profile) or None,
            screenshot_path=profile_png if profile_png.is_file() else None,
            artifacts_dir=artifacts_dir,
            profile=profile,
        )
    )
    tweet_rows = _load_jsonl_first(
        out_dir / "tweet_items.jsonl",
        out_dir / "tweet_items.jsonl",
        out_dir / "tweet.jsonl",
    )
    if tweet_rows:
        for row in tweet_rows:
            text = str(row.get("text") or "").strip() or None
            if not text:
                continue
            built.append(
                _build_visible_record(
                    session_id=session_id,
                    crawl_id=crawl_id,
                    package_name=PACKAGE_X,
                    social_scope="own_tweets",
                    screen_sequence=int(row.get("index") or 1),
                    normalized_text=text,
                    screenshot_path=None,
                    artifacts_dir=artifacts_dir,
                )
            )
    else:
        posts = sorted(out_dir.glob("post_*.png"))
        for index, shot in enumerate(posts, start=1):
            built.append(
                _build_visible_record(
                    session_id=session_id,
                    crawl_id=crawl_id,
                    package_name=PACKAGE_X,
                    social_scope="own_tweets",
                    screen_sequence=index,
                    normalized_text=_sidecar_text(out_dir, shot, shot.stem),
                    screenshot_path=shot,
                    artifacts_dir=artifacts_dir,
                )
            )
    for row in _load_jsonl_first(
        out_dir / "reply_items.jsonl",
        out_dir / "reply_items.jsonl",
        out_dir / "reply.jsonl",
    ):
        text = str(row.get("text") or "").strip() or None
        if not text:
            continue
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_X,
                social_scope="own_replies",
                screen_sequence=int(row.get("index") or 1),
                normalized_text=text,
                screenshot_path=None,
                artifacts_dir=artifacts_dir,
            )
        )
    return built


def records_from_fb_output(
    *,
    session_id: str,
    crawl_id: str,
    out_dir: Path,
    artifacts_dir: Path,
) -> list[tuple[InventoryRecordV1, Path | None]]:
    profile = _load_profile(out_dir)
    built: list[tuple[InventoryRecordV1, Path | None]] = []
    profile_png = out_dir / "profile.png"
    built.append(
        _build_visible_record(
            session_id=session_id,
            crawl_id=crawl_id,
            package_name=PACKAGE_FACEBOOK,
            social_scope="own_profile",
            screen_sequence=1,
            normalized_text=_profile_normalized_text(profile) or None,
            screenshot_path=profile_png if profile_png.is_file() else None,
            artifacts_dir=artifacts_dir,
            profile=profile,
        )
    )
    for row in _load_jsonl_first(
        out_dir / "fb_post_items.jsonl",
        out_dir / "fb_post.jsonl",
        out_dir / "fb_post.jsonl",
    ):
        text = str(row.get("text") or "").strip() or None
        if not text:
            continue
        low = text.lower()
        if "what's on your mind" in low or "new post" in low or "composer-view" in low:
            continue
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_FACEBOOK,
                social_scope="own_posts",
                screen_sequence=int(row.get("index") or 1),
                normalized_text=text,
                screenshot_path=None,
                artifacts_dir=artifacts_dir,
            )
        )
    for row in _load_jsonl_first(
        out_dir / "fb_comment_items.jsonl",
        out_dir / "fb_comment.jsonl",
    ):
        text = str(row.get("text") or "").strip() or None
        if not text:
            continue
        low = text.lower()
        if (
            "grouping-section-item" in low
            or "personal information" in low
            or "badgeview" in low
            or low in {"facebook", "activity log", "search"}
        ):
            continue
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_FACEBOOK,
                social_scope="own_comments",
                screen_sequence=int(row.get("index") or 1),
                normalized_text=text,
                screenshot_path=None,
                artifacts_dir=artifacts_dir,
            )
        )
    return built


async def _persist_records(
    *,
    session_id: str,
    crawl_id: str,
    staging: Path,
    records: list[tuple[InventoryRecordV1, Path | None]],
) -> int:
    if not records:
        return 0
    (staging / "visible_ui").mkdir(parents=True, exist_ok=True)
    now = utcnow()
    fingerprint = _sha256_text(f"ios_wda_social:{session_id}:{crawl_id}")
    async with db.transaction(immediate=True) as conn:
        existing = await (
            await conn.execute(
                "SELECT crawl_id FROM crawl_runs WHERE session_id = ?",
                (session_id,),
            )
        ).fetchone()
        effective_crawl_id = str(existing["crawl_id"]) if existing is not None else crawl_id

        rewritten_records: list[tuple[object, ...]] = []
        rewritten_artifacts: list[tuple[object, ...]] = []
        for record, shot_path in records:
            # Keep DB + on-disk crawl_id aligned when session already has a run.
            if record.crawl_id != effective_crawl_id:
                record = record.model_copy(update={"crawl_id": effective_crawl_id})
            rel_json = Path("visible_ui") / f"{record.record_id}.json"
            abs_json = staging / rel_json
            payload = record.model_dump(mode="json")
            abs_json.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
            rewritten_records.append(
                (
                    record.record_id,
                    effective_crawl_id,
                    session_id,
                    record.source_kind,
                    record.source_app,
                    record.metadata.social_scope,
                    record.normalized_text,
                    record.content_sha256,
                    1,
                    fingerprint,
                    json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                    str(rel_json),
                    now,
                )
            )
            if shot_path is not None and shot_path.is_file() and record.attachment_ids:
                shot_id = record.attachment_ids[0]
                rel_shot = Path("visible_ui") / "artifacts" / shot_path.name
                rewritten_artifacts.append(
                    (
                        shot_id,
                        effective_crawl_id,
                        session_id,
                        record.record_id,
                        "visible_ui",
                        "screenshot",
                        "image/png",
                        str(rel_shot),
                        shot_path.stat().st_size,
                        _sha256_file(shot_path),
                        1,
                        now,
                    )
                )

        if existing is None:
            await conn.execute(
                """
                INSERT INTO crawl_runs (
                    crawl_id, session_id, state, policy_version, policy_fingerprint,
                    selection_revision, selection_fingerprint, review_candidates,
                    selection_confirmed, totals_json, started_at, updated_at,
                    frozen_at, confirmed_at, failure_reason
                ) VALUES (?, ?, 'completed', 'ios_wda_social', ?, 1, ?, 0, 1, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    effective_crawl_id,
                    session_id,
                    fingerprint,
                    fingerprint,
                    json.dumps({"records": len(records)}, separators=(",", ":")),
                    now,
                    now,
                    now,
                    now,
                ),
            )
        else:
            await conn.execute(
                """
                UPDATE crawl_runs
                SET state = 'completed', updated_at = ?, selection_fingerprint = ?,
                    totals_json = ?, frozen_at = COALESCE(frozen_at, ?),
                    confirmed_at = COALESCE(confirmed_at, ?)
                WHERE session_id = ?
                """,
                (
                    now,
                    fingerprint,
                    json.dumps({"records": len(records)}, separators=(",", ":")),
                    now,
                    now,
                    session_id,
                ),
            )

        await conn.executemany(
            """
            INSERT OR REPLACE INTO crawl_records (
                record_id, crawl_id, session_id, source_kind, source_app, social_scope,
                normalized_text, content_sha256, selection_revision, selection_fingerprint,
                canonical_json, canonical_path, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rewritten_records,
        )
        if rewritten_artifacts:
            await conn.executemany(
                """
                INSERT OR REPLACE INTO crawl_artifacts (
                    artifact_id, crawl_id, session_id, record_id, source_kind, role,
                    mime_type, relative_path, size_bytes, sha256, verified, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rewritten_artifacts,
            )
    return len(records)


async def acquire_ios_social_ui(
    session_id: str,
    device_id: str,
    staging: Path,
    mode: AcquisitionMode,
    on_progress: ProgressCallback,
    target_packages: Sequence[str] | None = None,
) -> int:
    """Run IG + X + FB WDA flows and stage visible_ui records. Failures are categorized.

    Returns number of persisted inventory records. Raises AcquisitionError when the
    toolchain is enabled but cannot run; caller may catch to keep backup success.
    """
    if not settings.ios_social_ui_enabled:
        return 0

    udid = validate_ios_udid(device_id)
    tools = ios_social_toolchain_ready()
    if not all(tools.values()):
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "Toolchain iOS social UI (ios-media-puller) tidak lengkap.",
        )

    wanted = (
        list(target_packages)
        if target_packages is not None
        else list(settings.ios_social_targets)
    )
    targets = [
        package
        for package in wanted
        if package in FLOW_BY_PACKAGE
    ]
    if not targets:
        return 0

    await on_progress(
        SessionStatus.ACQUIRING,
        42,
        "Menyiapkan iPhone (pairing, Developer Image, WebDriverAgent)…",
        acquisition_method="ios_wda_social",
    )
    wda_url = await ensure_ios_wda_stack(udid=udid)
    await on_progress(
        SessionStatus.ACQUIRING,
        43,
        "Membuka Instagram, Facebook, dan X…",
        acquisition_method="ios_wda_social",
    )
    crawl_id = f"ios_social_{uuid.uuid4().hex[:24]}"
    os.environ["IOS_TEMP_CRAWL_SESSION"] = session_id
    work_root = staging / "_ios_social_work"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    artifacts_dir = staging / "visible_ui" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    archive_shots = (
        settings.ios_social_quick_archive_shots
        if mode == AcquisitionMode.QUICK
        else settings.ios_social_full_archive_shots
    )
    x_shots = (
        settings.ios_social_quick_x_shots
        if mode == AcquisitionMode.QUICK
        else settings.ios_social_full_x_shots
    )
    # FULL with 0 = uncapped within WDA hard ceiling (500).
    if mode != AcquisitionMode.QUICK and archive_shots <= 0:
        archive_shots = 500
    if mode != AcquisitionMode.QUICK and x_shots <= 0:
        x_shots = 500
    if mode == AcquisitionMode.QUICK:
        archive_shots = max(1, archive_shots)
        x_shots = max(1, x_shots)
    collected: list[tuple[InventoryRecordV1, Path | None]] = []
    failures: list[str] = []
    first_flow = True

    for package in targets:
        flow = FLOW_BY_PACKAGE[package]
        out_dir = work_root / flow
        logger.info(
            "ios_social_flow_started",
            extra={
                "session_id": session_id,
                "crawl_id": crawl_id,
                "device_ref": _ios_device_ref(udid),
                "target_package": package,
                "source_adapter": "ios_wda_visible_ui",
            },
        )
        await on_progress(
            SessionStatus.ACQUIRING,
            44,
            f"iOS social: {package}",
            acquisition_method="ios_wda_social",
        )
        try:
            wda_url = await ensure_ios_wda_stack(udid=udid, restart=not first_flow)
            first_flow = False
            code = await _run_flow(
                flow=flow,
                output_dir=out_dir,
                wda_url=wda_url,
                udid=udid,
                archive_shots=archive_shots,
                x_shots=x_shots,
            )
        except AcquisitionError as exc:
            failures.append(exc.category.value if hasattr(exc, "category") else "failed")
            logger.info(
                "ios_social_flow_failed",
                extra={
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": package,
                    "error_category": (
                        exc.category.value
                        if isinstance(exc.category, ErrorCategory)
                        else str(exc.category)
                    ),
                    "retryable": exc.retryable,
                },
            )
            _copy_flow_debug(out_dir, flow, session_id)
            continue
        if code != 0:
            failures.append(f"{package}:exit_{code}")
            logger.info(
                "ios_social_flow_failed",
                extra={
                    "session_id": session_id,
                    "crawl_id": crawl_id,
                    "target_package": package,
                    "dependency_exit_code": code,
                    "error_category": ErrorCategory.AGENT_UNREACHABLE.value,
                },
            )
            # IG may still have written profile before archive failure — ingest what exists.
        _copy_flow_debug(out_dir, flow, session_id)
        if package == PACKAGE_INSTAGRAM:
            collected.extend(
                records_from_ig_output(
                    session_id=session_id,
                    crawl_id=crawl_id,
                    out_dir=out_dir,
                    artifacts_dir=artifacts_dir,
                )
            )
        elif package == PACKAGE_X:
            collected.extend(
                records_from_x_output(
                    session_id=session_id,
                    crawl_id=crawl_id,
                    out_dir=out_dir,
                    artifacts_dir=artifacts_dir,
                )
            )
        elif package == PACKAGE_FACEBOOK:
            collected.extend(
                records_from_fb_output(
                    session_id=session_id,
                    crawl_id=crawl_id,
                    out_dir=out_dir,
                    artifacts_dir=artifacts_dir,
                )
            )

    # Drop profile-less empty IG/X if profile.json missing (flow never started).
    usable = [
        item
        for item in collected
        if item[0].metadata.social_scope != "own_profile"
        or bool(item[0].metadata.profile_username)
        or bool(item[0].normalized_text)
        or bool(item[0].attachment_ids)
    ]
    count = await _persist_records(
        session_id=session_id,
        crawl_id=crawl_id,
        staging=staging,
        records=usable,
    )
    shutil.rmtree(work_root, ignore_errors=True)

    logger.info(
        "ios_social_completed",
        extra={
            "session_id": session_id,
            "crawl_id": crawl_id,
            "device_ref": _ios_device_ref(udid),
            "item_count": count,
            "error_category": failures[0] if failures and count == 0 else None,
        },
    )
    if count == 0 and failures:
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "iOS social UI tidak menghasilkan record (IG/X/FB).",
            retryable=True,
        )
    await on_progress(
        SessionStatus.ACQUIRING,
        48,
        f"iOS social UI selesai ({count} record)",
        acquisition_method="ios_wda_social",
        files_pulled=count,
    )
    return count
