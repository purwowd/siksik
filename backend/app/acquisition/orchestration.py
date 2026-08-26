from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.contracts import AcquisitionContext, AcquisitionResult, UploadedArchive
from app.acquisition.analysis_plan import default_analysis_plan
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.file_identity import stable_file_id
from app.acquisition.indexing import hash_file, index_staging
from app.acquisition.media_types import (
    AUDIO_EXT,
    CHAT_HINTS,
    DOC_EXT,
    IMG_EXT,
    TEXT_EXT,
    VID_EXT,
    _bucket_for_file,
    _classify_source,
    _is_junk_media_path,
    _zip_skip,
    guess_mime,
    looks_favorite_path,
)
from app.acquisition.process import run_process
from app.acquisition.providers import AcquisitionProviderRegistry
from app.acquisition.session_defaults import empty_progress, empty_timing
from app.acquisition.toolchain import _run, detect_devices, toolchain_status
from app.core.branding import CANONICAL_CRAWL_RECORD_MIME, crawl_record_filename_mime, is_crawl_record_mime
from app.core.config import settings
from app.core.request_context import current_request_id
from app.core.db import db
from app.models.schemas import (
    AcquisitionMode,
    DeviceInfo,
    DeviceType,
    Scenario,
    SessionProgress,
    SessionStatus,
    TimingBreakdown,
)

logger = logging.getLogger("siksik.acquisition.orchestration")

def _scenario_seed(scenario: Scenario, n: int) -> list[dict]:
    """Synthetic dataset — gallery-heavy (fokus PoC saat ini)."""
    # ~70% gallery, sisa video/documents ringan (tanpa chat DB)
    files: list[dict] = []
    risk_ratio = 0.12 if scenario == Scenario.TIDAK_LULUS else 0.0
    risk_keywords = settings.risk_keywords

    for i in range(n):
        roll = i % 10
        if roll < 7:
            source = "gallery"
        elif roll < 9:
            source = "video"
        else:
            source = "documents"

        is_risk = scenario == Scenario.TIDAK_LULUS and (i % max(1, int(1 / risk_ratio)) == 0)

        if source == "documents":
            ext = "txt"
            if is_risk:
                kw = risk_keywords[(i // 3) % len(risk_keywords)]
                content = f"Dokumen catatan: indikasi {kw}."
            else:
                content = f"Dokumen administratif nomor {i}."
        elif source == "video":
            ext = "vidmeta"
            content = json.dumps(
                {
                    "name": f"VID_{i:05d}.mp4",
                    "keyframes": 3,
                    "tags": [risk_keywords[i % len(risk_keywords)]] if is_risk else ["traveling"],
                    "risk": is_risk,
                }
            )
        else:
            ext = "imgmeta"
            content = json.dumps(
                {
                    "name": f"IMG_{i:05d}.jpg",
                    "tags": [risk_keywords[i % len(risk_keywords)]] if is_risk else ["liburan", "keluarga"],
                    "risk": is_risk,
                }
            )

        files.append(
            {
                "name": f"{source}_{i:05d}.{ext}",
                "source": source,
                "content": content,
                "is_risk_planted": is_risk,
            }
        )
    return files


async def acquire_simulated(
    session_id: str,
    device_id: str,
    mode: AcquisitionMode,
    scenario: Scenario,
    file_count: int,
    on_progress,
) -> tuple[Path, int, float, str]:
    t0 = time.perf_counter()
    staging = settings.staging_dir / session_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    target = file_count
    if mode == AcquisitionMode.QUICK:
        target = min(file_count, max(settings.image_cap_quick, 400))

    descriptors = _scenario_seed(scenario, target)
    pulled = 0
    chunk = 100
    for start in range(0, len(descriptors), chunk):
        batch = descriptors[start : start + chunk]

        def _write_batch(items: list[dict], base: Path) -> int:
            count = 0
            for d in items:
                src_dir = base / d["source"]
                src_dir.mkdir(parents=True, exist_ok=True)
                path = src_dir / d["name"]
                path.write_text(d["content"], encoding="utf-8")
                if d["is_risk_planted"]:
                    (src_dir / f"{d['name']}.risk").write_text("1", encoding="utf-8")
                count += 1
            return count

        pulled += await asyncio.to_thread(_write_batch, batch, staging)
        pct = 10 + (pulled / max(target, 1)) * 35
        await on_progress(
            SessionStatus.ACQUIRING,
            pct,
            f"Akuisisi sintetis [{device_id}] ({pulled}/{target})",
            files_listed=target,
            files_pulled=pulled,
            acquisition_method="simulated",
        )
        await asyncio.sleep(0)

    return staging, pulled, (time.perf_counter() - t0) * 1000, "simulated"


async def _adb_list_files(
    device_id: str,
    remote_dirs: list[str],
    limit: int,
    transport: AsyncAdbTransport | None = None,
    not_before_epoch_s: float | None = None,
) -> list[str]:
    """List files via ADB and prioritize relevant paths and extensions."""
    scored: list[tuple[float, str]] = []
    prefer = tuple(settings.android_prefer_ext)
    adb = transport or AsyncAdbTransport(
        settings.adb_path,
        timeout_seconds=settings.adb_command_timeout_s,
    )

    for remote in remote_dirs:
        result = await adb.run(
            device_id,
            ["shell", "find", remote, "-type", "f", "-printf", "%T@ %p\\n"],
            operation="legacy_file_listing",
            timeout=90,
            check=False,
        )
        if result.returncode != 0:
            result = await adb.run(
                device_id,
                ["shell", "find", remote, "-type", "f"],
                operation="legacy_file_listing_fallback",
                timeout=90,
                check=False,
            )
        if result.returncode != 0 or not result.stdout.strip():
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            mtime = 0.0
            path = line
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                try:
                    mtime = float(parts[0])
                    path = parts[1].strip()
                except ValueError:
                    path = line
            if not path or path.endswith("/"):
                continue
            if (
                not_before_epoch_s is not None
                and mtime > 0
                and mtime < not_before_epoch_s
                and not looks_favorite_path(path)
            ):
                continue
            if _is_junk_media_path(path):
                continue
            low = path.lower()
            bonus = 1_000_000.0 if any(low.endswith(ext) for ext in prefer) else 0.0
            # Gallery-first scoring (msgstore/DB diabaikan)
            if any(x in low for x in ("/dcim/", "/pictures/", "/camera/", "img_", "screenshot")):
                bonus += 800_000.0
            if low.endswith((".db", ".sqlite")) or "msgstore" in low or "/databases/" in low:
                continue  # skip chat DB entirely for now
            # Video path boost — agar Movies/Download/*.mp4 tidak kalah dari foto massal
            if any(low.endswith(e) for e in (".mp4", ".mov", ".3gp", ".mkv", ".webm")):
                bonus += 500_000.0
            if any(x in low for x in ("/movies/", "/video/", "whatsapp video", "telegram video")):
                bonus += 350_000.0
            if any(x in low for x in ("whatsapp", "telegram")) and any(
                low.endswith(e) for e in (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".3gp")
            ):
                bonus += 200_000.0  # foto/video chat, prioritas di bawah DCIM
            scored.append((mtime + bonus, path))

    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    uniq: list[str] = []
    for _, p in scored:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
        if limit > 0 and len(uniq) >= limit:
            break
    return uniq


async def acquire_android_adb(
    session_id: str,
    device_id: str,
    mode: AcquisitionMode,
    on_progress,
) -> tuple[Path, int, float, str]:
    t0 = time.perf_counter()
    staging = settings.staging_dir / session_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    transport = AsyncAdbTransport(
        settings.adb_path,
        timeout_seconds=settings.adb_command_timeout_s,
    )
    await transport.select_device(device_id)

    paths = settings.android_paths_quick if mode == AcquisitionMode.QUICK else settings.android_paths_full
    limit = settings.adb_max_files_quick if mode == AcquisitionMode.QUICK else settings.adb_max_files_full

    await on_progress(SessionStatus.ACQUIRING, 8, f"Listing file via ADB ({device_id})…", acquisition_method="adb")
    from app.acquisition.time_scope import build_time_scope

    not_before = build_time_scope(mode).not_before.timestamp()
    remote_files = await _adb_list_files(
        device_id,
        paths,
        limit,
        transport,
        not_before_epoch_s=not_before,
    )
    listed = len(remote_files)
    if listed == 0:
        raise RuntimeError(
            f"ADB tidak menemukan file pada path selektif untuk {device_id}. "
            "Pastikan USB debugging aktif & penyimpanan dapat diakses."
        )

    pulled = 0
    for idx, remote in enumerate(remote_files, start=1):
        source = _classify_source(remote)
        local_dir = staging / source
        local_dir.mkdir(parents=True, exist_ok=True)
        name = Path(remote).name or f"file_{idx}"
        local_path = local_dir / name
        # avoid overwrite collisions
        if local_path.exists():
            local_path = local_dir / f"{idx}_{name}"

        result = await transport.run(
            device_id,
            ["pull", remote, str(local_path)],
            operation="legacy_file_pull",
            timeout=float(settings.adb_pull_timeout_s),
            check=False,
        )
        if result.returncode == 0 and local_path.exists():
            # skip oversized
            if local_path.stat().st_size > settings.max_file_size_mb * 1024 * 1024:
                local_path.unlink(missing_ok=True)
            else:
                pulled += 1

        if idx % 5 == 0 or idx == listed:
            pct = 10 + (idx / listed) * 35
            await on_progress(
                SessionStatus.ACQUIRING,
                pct,
                f"ADB pull {idx}/{listed} (ok={pulled})",
                files_listed=listed,
                files_pulled=pulled,
                acquisition_method="adb",
            )

    if pulled == 0:
        raise RuntimeError(f"ADB pull gagal untuk semua kandidat file ({listed} kandidat).")

    return staging, pulled, (time.perf_counter() - t0) * 1000, "adb"


async def acquire_ios_libimobiledevice(
    session_id: str,
    device_id: str,
    mode: AcquisitionMode,
    on_progress,
) -> tuple[Path, int, float, str]:
    """Best-effort iOS acquisition via idevicebackup2; falls back with clear error."""
    t0 = time.perf_counter()
    staging = settings.staging_dir / session_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    tools = await toolchain_status()
    if not tools.get("idevicebackup2"):
        raise RuntimeError(
            "idevicebackup2 tidak tersedia. Install libimobiledevice atau gunakan simulator untuk PoC."
        )

    await on_progress(
        SessionStatus.ACQUIRING,
        10,
        f"iOS backup via idevicebackup2 ({device_id[:8]}…) — mode {mode.value}",
        acquisition_method="idevicebackup2",
    )
    backup_dir = staging / "_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    code, _out, _err = await _run(
        ["idevicebackup2", "-u", device_id, "backup", str(backup_dir)],
        timeout=900,
    )
    if code != 0:
        raise RuntimeError("idevicebackup2 gagal menjalankan backup perangkat.")

    # Copy interesting extensions out of backup tree into classified folders
    pulled = 0
    candidates = [
        p
        for p in backup_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in (IMG_EXT | VID_EXT | AUDIO_EXT | DOC_EXT | TEXT_EXT | {".heic"})
        and p.suffix.lower() not in {".db", ".sqlite"}
    ]
    from app.acquisition.time_scope import build_time_scope

    not_before = build_time_scope(mode).not_before.timestamp()
    candidates = [
        path
        for path in candidates
        if path.stat().st_mtime >= not_before or looks_favorite_path(str(path))
    ]

    total = max(len(candidates), 1)
    for idx, src in enumerate(candidates, start=1):
        source = _classify_source(str(src))
        dest_dir = staging / source
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{idx}_{src.name}"
        try:
            if src.stat().st_size <= settings.max_file_size_mb * 1024 * 1024:
                shutil.copy2(src, dest)
                pulled += 1
        except OSError:
            continue
        if idx % 10 == 0 or idx == total:
            await on_progress(
                SessionStatus.ACQUIRING,
                10 + (idx / total) * 35,
                f"Extract iOS backup {idx}/{total}",
                files_listed=total,
                files_pulled=pulled,
                acquisition_method="idevicebackup2",
            )

    if pulled == 0:
        # Unencrypted / modern iOS backups often have no gallery files with media
        # extensions in the backup tree. Keep staging so additive iOS social UI
        # (IG/X WDA) can still run; provider fails only if every source is empty.
        await on_progress(
            SessionStatus.ACQUIRING,
            40,
            "Backup iOS tanpa media terklasifikasi — lanjut sumber iOS lain bila diaktifkan",
            files_listed=0,
            files_pulled=0,
            acquisition_method="idevicebackup2",
        )
        return staging, 0, (time.perf_counter() - t0) * 1000, "idevicebackup2"

    return staging, pulled, (time.perf_counter() - t0) * 1000, "idevicebackup2"


async def acquire_from_zip(
    session_id: str,
    zip_bytes: bytes,
    *,
    on_progress,
    original_name: str = "upload.zip",
) -> tuple[Path, int, float, str]:
    """Ekstrak ZIP hasil ADB/manual ke staging — tanpa akuisisi USB."""
    import zipfile
    from io import BytesIO

    t0 = time.perf_counter()
    staging = settings.staging_dir / session_id
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    max_bytes = settings.zip_max_mb * 1024 * 1024
    if len(zip_bytes) > max_bytes:
        raise RuntimeError(f"ZIP terlalu besar (max {settings.zip_max_mb} MB)")

    await on_progress(
        SessionStatus.ACQUIRING,
        8,
        f"Membuka arsip {original_name}…",
        files_listed=0,
        files_pulled=0,
        acquisition_method="zip_upload",
    )

    def _extract() -> int:
        pulled = 0
        with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
            members = [m for m in zf.infolist() if not m.is_dir() and not _zip_skip(m.filename)]
            # Deteksi apakah ZIP sudah terstruktur (gallery/video/…)
            tops = {Path(m.filename).parts[0].lower() for m in members if Path(m.filename).parts}
            structured = bool(tops & {"gallery", "video", "documents", "dcim", "pictures", "download", "movies", "email", "gmail"})

            for i, member in enumerate(members):
                raw_name = member.filename.replace("\\", "/")
                if raw_name.endswith("/"):
                    continue
                # Cegah zip-slip
                target_name = Path(raw_name).name
                if ".." in Path(raw_name).parts:
                    continue

                if structured:
                    # Normalisasi DCIM/Pictures → gallery, Movies → video, Download → documents, Email → email
                    parts = list(Path(raw_name).parts)
                    top = parts[0].lower() if parts else "other"
                    if top in {"dcim", "pictures", "camera", "screenshot", "screenshots"}:
                        bucket = "gallery"
                        rel = Path(bucket, *parts[1:]) if len(parts) > 1 else Path(bucket, target_name)
                    elif top in {"movies", "video", "videos", "camera"}:
                        bucket = "video"
                        rel = Path(bucket, *parts[1:]) if len(parts) > 1 else Path(bucket, target_name)
                    elif top in {"download", "downloads", "documents", "docs"}:
                        bucket = "documents"
                        rel = Path(bucket, *parts[1:]) if len(parts) > 1 else Path(bucket, target_name)
                    elif top in {"email", "gmail", "mail", "emails"}:
                        bucket = "email"
                        rel = Path(bucket, *parts[1:]) if len(parts) > 1 else Path(bucket, target_name)
                    elif top in {"gallery", "video", "documents", "other", "whatsapp", "telegram", "email"}:
                        rel = Path(*parts)
                    else:
                        bucket = _bucket_for_file(raw_name)
                        rel = Path(bucket, target_name)
                else:
                    bucket = _bucket_for_file(raw_name)
                    rel = Path(bucket, target_name)

                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
                pulled += 1
                if pulled % 50 == 0:
                    # progress via shared state below
                    pass
        return pulled

    pulled = await asyncio.to_thread(_extract)
    await on_progress(
        SessionStatus.ACQUIRING,
        40,
        f"ZIP diekstrak · {pulled} file",
        files_listed=pulled,
        files_pulled=pulled,
        acquisition_method="zip_upload",
    )

    if pulled == 0:
        raise RuntimeError("ZIP tidak berisi file media yang bisa dianalisis")

    return staging, pulled, (time.perf_counter() - t0) * 1000, "zip_upload"


async def acquire_dispatch(
    *,
    session_id: str,
    device_id: str,
    device_type: DeviceType,
    simulated: bool,
    mode: AcquisitionMode,
    scenario: Scenario,
    file_count: int,
    on_progress,
    review_candidates: bool = False,
    analysis_plan=None,
) -> tuple[Path, int, float, str]:
    agent_runner = None
    if settings.android_agent_enabled:
        from app.acquisition.bootstrap import android_agent_runner

        agent_runner = android_agent_runner
    registry = AcquisitionProviderRegistry(
        android_agent_enabled=settings.android_agent_enabled,
        android_legacy_fallback=settings.android_legacy_fallback,
        agent_runner=agent_runner,
    )
    context = AcquisitionContext(
        session_id=session_id,
        device_id=device_id,
        device_type=device_type,
        mode=mode,
        scenario=scenario,
        file_count=file_count,
        on_progress=on_progress,
        simulated=simulated,
        request_id=current_request_id(),
        review_candidates=review_candidates,
        analysis_plan=analysis_plan or default_analysis_plan(),
    )
    result = await registry.acquire(context)
    if (
        settings.android_recovery_enabled
        and device_type == DeviceType.ANDROID
        and not simulated
        and context.analysis_plan.includes_recovery
    ):
        from app.acquisition.android_recovery import AndroidRecoveryService
        from app.acquisition.android_recovery.service import cleanup_recovery_staging

        try:
            recovery = await AndroidRecoveryService().recover(
                session_id=session_id,
                serial=device_id,
                mode=mode,
                staging=result.staging,
                on_progress=on_progress,
                request_id=context.request_id,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                asyncio.to_thread(cleanup_recovery_staging, result.staging)
            )
            raise
        except (AcquisitionError, OSError) as exc:
            await asyncio.to_thread(cleanup_recovery_staging, result.staging)
            error_category = (
                exc.category
                if isinstance(exc, AcquisitionError)
                else ErrorCategory.STORAGE_UNAVAILABLE
            )
            logger.warning(
                "android_recovery_unavailable",
                extra={
                    "request_id": context.request_id,
                    "session_id": session_id,
                    "phase": mode.value,
                    "error_category": error_category.value,
                    "retryable": (
                        exc.retryable if isinstance(exc, AcquisitionError) else False
                    ),
                    "dependency_exit_code": (
                        exc.dependency_exit_code
                        if isinstance(exc, AcquisitionError)
                        else None
                    ),
                },
            )
            await on_progress(
                SessionStatus.ACQUIRING,
                60.0,
                "Recovery sampah Android dilewati; akuisisi utama tetap dilanjutkan",
                recovery_state="unavailable",
                recovery_error_category=error_category.value,
            )
        else:
            result = AcquisitionResult(
                staging=result.staging,
                item_count=result.item_count + recovery.item_count,
                duration_ms=result.duration_ms + recovery.duration_ms,
                method=(
                    f"{result.method}+android_recovery_{mode.value}_{recovery.manifest.status}"
                ),
                provider=result.provider,
            )

    if (
        settings.gmail_acquisition_enabled
        and (device_type == DeviceType.ANDROID or simulated)
        and context.analysis_plan.scope.value != "social"
        and not ((result.staging / "email").is_dir() and any((result.staging / "email").iterdir()))
    ):
        from app.acquisition.gmail_service import GmailAcquisitionService

        try:
            token = None
            account_name = None
            if not simulated and agent_runner is not None:
                from app.acquisition.runtime import agent_runtime_registry

                runtime = await agent_runtime_registry.get(session_id)
                account_name = runtime.google_account
                token = runtime.google_token
                if not token:
                    from app.acquisition.agent_client import AgentClient, AgentClientConfig

                    runtime_client = AgentClient(
                        runtime.forward_host_port,
                        runtime.token,
                        config=AgentClientConfig(
                            timeout_seconds=settings.android_agent_request_timeout_s,
                            max_attempts=settings.android_agent_request_attempts,
                            max_response_bytes=(
                                settings.android_agent_max_response_mb * 1024 * 1024
                            ),
                        ),
                    )
                    if not account_name:
                        accounts = await runtime_client.list_google_accounts(session_id)
                        if accounts:
                            account_name = accounts[0].name
                    if account_name:
                        token = await runtime_client.get_google_auth_token(
                            session_id,
                            account_name,
                            scope=settings.resolved_gmail_scope,
                        )
                        if not token and settings.resolved_gmail_scope != settings.gmail_scope:
                            token = await runtime_client.get_google_auth_token(
                                session_id,
                                account_name,
                                scope=settings.gmail_scope,
                            )
            gmail_svc = GmailAcquisitionService()
            gmail_count, _ = await gmail_svc.acquire(
                session_id=session_id,
                staging=result.staging,
                mode=mode,
                token=token,
                account_name=account_name,
                simulated=simulated,
                on_progress=on_progress,
                request_id=context.request_id,
            )
            if gmail_count > 0:
                result = AcquisitionResult(
                    staging=result.staging,
                    item_count=result.item_count + gmail_count,
                    duration_ms=result.duration_ms,
                    method=f"{result.method}+gmail_api",
                    provider=result.provider,
                )
        except Exception as exc:
            logger.warning("gmail_acquisition_dispatch_skipped", extra={"error": str(exc)})

    return result.as_legacy_tuple()


async def acquire_zip_dispatch(
    *,
    session_id: str,
    zip_bytes: bytes,
    mode: AcquisitionMode,
    original_name: str,
    on_progress,
) -> tuple[Path, int, float, str]:
    registry = AcquisitionProviderRegistry()
    result = await registry.acquire(
        AcquisitionContext(
            session_id=session_id,
            device_id=f"zip:{original_name[:40]}",
            device_type=DeviceType.ANDROID,
            mode=mode,
            scenario=Scenario.LULUS,
            file_count=0,
            on_progress=on_progress,
            archive=UploadedArchive(content=zip_bytes, original_name=original_name),
            request_id=current_request_id(),
        )
    )
    return result.as_legacy_tuple()


