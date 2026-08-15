"""iOS IG/X visible-UI social crawl via in-repo ios-media-puller (WDA).

Isolated from Android agent/UiAutomator paths. Maps iOS bundle outputs onto the
existing report package IDs (com.instagram.android / com.twitter.android) so
reports.SOCIAL_PACKAGES / SOCIAL_SCOPES stay unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

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

FLOW_BY_PACKAGE = {
    PACKAGE_INSTAGRAM: "ig-profile",
    PACKAGE_X: "x-profile",
}


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


def _puller_paths() -> tuple[Path, Path, Path]:
    root = settings.ios_media_puller_path.resolve()
    python_bin = root / ".venv" / "bin" / "python"
    automator = root / "ios_automator" / "automator.py"
    return root, python_bin, automator


def ios_social_toolchain_ready() -> dict[str, bool]:
    root, python_bin, automator = _puller_paths()
    stack = root / "ios_automator" / "scripts" / "run_stack.sh"
    return {
        "puller_root": root.is_dir(),
        "venv_python": python_bin.is_file(),
        "automator": automator.is_file(),
        "run_stack": stack.is_file(),
    }


async def _wda_ready(url: str, timeout_s: float = 3.0) -> bool:
    status_url = url.rstrip("/") + "/status"

    def _check() -> bool:
        try:
            with urlopen(status_url, timeout=timeout_s) as response:  # noqa: S310
                return 200 <= int(response.status) < 300
        except (URLError, TimeoutError, OSError, ValueError):
            return False

    import asyncio

    return await asyncio.to_thread(_check)


async def ensure_ios_wda_stack(*, udid: str) -> str:
    """Ensure WDA HTTP is reachable; start run_stack.sh if needed. Returns base URL."""
    base = settings.ios_social_wda_url.rstrip("/")
    if await _wda_ready(base):
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
        "IOS_SKIP_WDA_INSTALL": "1",
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
    await run_process(
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
    if not await _wda_ready(base, timeout_s=5.0):
        raise acquisition_error(
            ErrorCategory.AGENT_UNREACHABLE,
            "WebDriverAgent iOS tidak siap di port lokal.",
            retryable=True,
        )
    return base


async def _run_flow(
    *,
    flow: str,
    output_dir: Path,
    wda_url: str,
    env_extra: dict[str, str],
) -> int:
    root, python_bin, automator = _puller_paths()
    if not python_bin.is_file() or not automator.is_file():
        raise acquisition_error(
            ErrorCategory.DEPENDENCY_NOT_FOUND,
            "ios-media-puller venv/automator tidak siap.",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
        "IOS_SKIP_WDA_INSTALL": "1",
        **env_extra,
    }
    result = await run_process(
        [
            str(python_bin),
            str(automator),
            "--skip-wda-install",
            flow,
            "--http",
            wda_url,
            "-o",
            str(output_dir),
        ],
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
                normalized_text=None,
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
    posts = sorted(out_dir.glob("post_*.png"))
    for index, shot in enumerate(posts, start=1):
        built.append(
            _build_visible_record(
                session_id=session_id,
                crawl_id=crawl_id,
                package_name=PACKAGE_X,
                social_scope="own_tweets",
                screen_sequence=index,
                normalized_text=None,
                screenshot_path=shot,
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
) -> int:
    """Run IG + X WDA flows and stage visible_ui records. Failures are categorized.

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

    targets = [
        package
        for package in settings.ios_social_targets
        if package in FLOW_BY_PACKAGE
    ]
    if not targets:
        return 0

    await on_progress(
        SessionStatus.ACQUIRING,
        42,
        "iOS social UI (IG/X) via WebDriverAgent…",
        acquisition_method="ios_wda_social",
    )
    wda_url = await ensure_ios_wda_stack(udid=udid)
    crawl_id = f"ios_social_{uuid.uuid4().hex[:24]}"
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
    env_extra = {
        "UDID": udid,
        "IOS_ARCHIVE_MAX_SCREENSHOTS": str(archive_shots),
        "IOS_X_MAX_SCREENSHOTS": str(x_shots),
    }

    collected: list[tuple[InventoryRecordV1, Path | None]] = []
    failures: list[str] = []

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
            code = await _run_flow(
                flow=flow,
                output_dir=out_dir,
                wda_url=wda_url,
                env_extra=env_extra,
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
            "iOS social UI tidak menghasilkan record (IG/X).",
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
