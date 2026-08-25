from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.contracts import (
    AcquisitionContext,
    AcquisitionResult,
    UploadedArchive,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.file_identity import stable_file_id
from app.acquisition.process import run_process
from app.acquisition.providers import AcquisitionProviderRegistry
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

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log", ".vcard", ".vcf"}
DOC_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".xls",
    ".xlsx",
    ".ods",
    ".ppt",
    ".pptx",
    ".pages",
    ".numbers",
    ".key",
}
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".imgmeta"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".vidmeta"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".amr"}
CHAT_HINTS = ("whatsapp", "telegram", "wa-", "msgstore", "chat")
logger = logging.getLogger("siksik.services.acquisition")

# Junk Android / OS clutter — jangan di-pull / di-index
_JUNK_BASENAMES = frozenset(
    {
        ".nomedia",
        ".database_uuid",
        ".ds_store",
        "thumbs.db",
        "desktop.ini",
        ".thumbnails",
    }
)
_MEDIA_EXT = IMG_EXT | VID_EXT | AUDIO_EXT | TEXT_EXT | DOC_EXT


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def looks_favorite_path(path_str: str) -> bool:
    value = path_str.casefold()
    return any(token in value for token in ("favorite", "favourite", "favorit"))


def _is_junk_media_path(path_str: str) -> bool:
    """Skip hidden/junk yang sering ikut saat find pada Movies/Download."""
    name = Path(path_str).name
    low = name.lower()
    if low in _JUNK_BASENAMES:
        return True
    if name.startswith("."):
        return True
    ext = Path(path_str).suffix.lower()
    # PoC gallery-first: hanya media/dokumen relevan
    if not ext or ext not in _MEDIA_EXT:
        return True
    return False


async def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        result = await run_process(
            cmd,
            timeout=timeout,
            check=False,
            output_limit_bytes=1024 * 1024,
            operation="acquisition_dependency",
        )
        return result.returncode, result.stdout, result.stderr
    except AcquisitionError as exc:
        code = 124 if exc.category == ErrorCategory.ADB_TIMEOUT else 127
        return code, "", exc.public_message


async def toolchain_status() -> dict:
    try:
        adb_result = await AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=3,
        ).run(
            None,
            ["version"],
            operation="adb_version_probe",
            check=False,
        )
        adb_ready = adb_result.returncode == 0
    except AcquisitionError:
        adb_ready = False
    idevice_code, _, _ = await _run(["idevice_id", "-l"], timeout=3)
    backup_code, _, _ = await _run(["idevicebackup2", "-h"], timeout=3)
    return {
        "adb": adb_ready,
        "idevice_id": idevice_code == 0,
        "idevicebackup2": backup_code in (0, 1),  # help often exits 1
    }


async def detect_devices(*, include_simulators: bool = True) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []

    try:
        from app.acquisition.install_policy import oem_install_guidance

        transport = AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=min(settings.adb_command_timeout_s, 5.0),
        )
        android_devices = await transport.list_devices()
        for item in android_devices:
            if item.state != "device":
                continue
            model = (item.model or "Android").replace("_", " ")
            os_version = "unknown"
            manufacturer: str | None = None
            api_level: int | None = None
            unlocked: bool | None = None
            install_hint: str | None = None
            try:
                os_version = await transport.getprop(item.serial, "ro.build.version.release") or "unknown"
            except AcquisitionError:
                pass
            try:
                manufacturer = await transport.getprop(item.serial, "ro.product.manufacturer")
            except AcquisitionError:
                pass
            try:
                brand = await transport.getprop(item.serial, "ro.product.brand")
            except AcquisitionError:
                brand = None
            try:
                sdk = await transport.getprop(item.serial, "ro.build.version.sdk")
                if sdk and sdk.isdigit():
                    api_level = int(sdk)
            except AcquisitionError:
                pass
            try:
                readiness = await transport.device_readiness(item.serial)
                unlocked = readiness.unlocked
            except AcquisitionError:
                pass
            install_hint = oem_install_guidance(manufacturer=manufacturer, brand=brand)
            agent_state: str | None = None
            agent_version: str | None = None
            agent_error_category: str | None = None
            automation_state: str | None = None
            if settings.android_agent_enabled:
                try:
                    package = await transport.inspect_package(
                        item.serial,
                        settings.android_agent_package,
                    )
                    agent_state = "installed" if package.installed else "not_installed"
                    agent_version = package.version_name
                except AcquisitionError as exc:
                    agent_state = "unknown"
                    agent_error_category = exc.category.value
                try:
                    automation = await transport.inspect_package(
                        item.serial,
                        settings.android_agent_automation_package,
                    )
                    automation_state = (
                        "installed" if automation.installed else "not_installed"
                    )
                except AcquisitionError:
                    automation_state = "unknown"
            lock_note = ""
            if unlocked is False:
                lock_note = " · terkunci"
            devices.append(
                DeviceInfo(
                    device_id=item.serial,
                    device_type=DeviceType.ANDROID,
                    label=f"{model} · Android {os_version}{lock_note} ({item.serial[:8]})",
                    os_version=os_version or "unknown",
                    connected=True,
                    simulated=False,
                    agent_state=agent_state,
                    agent_version=agent_version,
                    agent_error_category=agent_error_category,
                    manufacturer=manufacturer,
                    api_level=api_level,
                    unlocked=unlocked,
                    install_hint=install_hint,
                    automation_state=automation_state,
                )
            )
    except AcquisitionError:
        pass

    code, out, _ = await _run(["idevice_id", "-l"], timeout=5)
    if code == 0:
        for udid in out.strip().splitlines():
            udid = udid.strip()
            if not udid:
                continue
            name_code, name_out, _ = await _run(["idevicename", "-u", udid], timeout=5)
            label = name_out.strip() if name_code == 0 and name_out.strip() else f"iPhone ({udid[:8]})"
            devices.append(
                DeviceInfo(
                    device_id=udid,
                    device_type=DeviceType.IOS,
                    label=label,
                    os_version="iOS",
                    connected=True,
                    simulated=False,
                )
            )

    if include_simulators:
        devices.extend(
            [
                DeviceInfo(
                    device_id="sim-android-01",
                    device_type=DeviceType.ANDROID,
                    label="Android Simulator (PoC)",
                    os_version="14",
                    connected=True,
                    simulated=True,
                ),
                DeviceInfo(
                    device_id="sim-iphone-01",
                    device_type=DeviceType.IOS,
                    label="iPhone Simulator (PoC)",
                    os_version="17",
                    connected=True,
                    simulated=True,
                ),
            ]
        )
    return devices


def _classify_source(path_str: str) -> str:
    low = path_str.lower().replace("\\", "/")
    ext = Path(path_str).suffix.lower()
    # Prefer media type from extension so Download/*.mp4 tetap dianalisis sebagai video
    if ext in VID_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMG_EXT:
        if any(x in low for x in ("whatsapp", "/wa/")):
            return "whatsapp"
        if "telegram" in low:
            return "telegram"
        return "gallery"
    if "whatsapp" in low or "/wa/" in low:
        return "whatsapp"
    if "telegram" in low:
        return "telegram"
    if any(x in low for x in ("dcim", "camera", "picture", "gallery", "img_")):
        return "gallery"
    if any(x in low for x in ("document", "download", "pdf", "doc")):
        return "documents"
    if any(x in low for x in ("movie", "video")):
        return "video"
    return "other"


def guess_mime(path: Path) -> str:
    if path.name.endswith(".siksik-record.json"):
        return "application/vnd.siksik.crawl-record+json"
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    ext = path.suffix.lower()
    if ext in IMG_EXT:
        return "image/jpeg"
    if ext in VID_EXT:
        return "video/mp4"
    if ext in AUDIO_EXT:
        return "audio/mpeg"
    if ext in TEXT_EXT or ext in DOC_EXT:
        return "text/plain"
    return "application/octet-stream"


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


def _zip_skip(name: str) -> bool:
    low = name.replace("\\", "/").lower()
    if "__macosx" in low.split("/"):
        return True
    return _is_junk_media_path(name)


def _bucket_for_file(name: str) -> str:
    ext = Path(name).suffix.lower()
    low = name.lower()
    if low.endswith(".whatsapp-message.json"):
        return "whatsapp"
    if ext in {".eml", ".msg"} or "email" in low or "gmail" in low:
        return "email"
    if ext in VID_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMG_EXT:
        return "gallery"
    if ext in TEXT_EXT | DOC_EXT:
        return "documents"
    # path hints
    source = _classify_source(name)
    if source in {"gallery", "video", "audio", "documents", "whatsapp", "telegram", "email"}:
        return "gallery" if source in {"whatsapp", "telegram"} else source
    return "other"


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
            structured = bool(
                tops
                & {
                    "gallery",
                    "video",
                    "documents",
                    "dcim",
                    "pictures",
                    "download",
                    "movies",
                    "email",
                    "gmail",
                    "whatsapp",
                }
            )

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
    )
    result = await registry.acquire(context)
    if (
        settings.android_recovery_enabled
        and device_type == DeviceType.ANDROID
        and not simulated
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

    if device_type == DeviceType.ANDROID and not simulated:
        from app.acquisition.whatsapp_backup import WhatsAppBackupAcquisitionService

        whatsapp = await WhatsAppBackupAcquisitionService().acquire(
            serial=device_id,
            staging=result.staging,
            mode=mode,
            on_progress=on_progress,
        )
        if whatsapp is not None:
            method_suffix = (
                "whatsapp_crypt15"
                if whatsapp.state == "complete"
                else "whatsapp_crypt15_parse_unavailable"
            )
            result = AcquisitionResult(
                staging=result.staging,
                item_count=result.item_count + whatsapp.item_count,
                duration_ms=result.duration_ms + whatsapp.duration_ms,
                method=f"{result.method}+{method_suffix}",
                provider=result.provider,
            )

    if (
        settings.gmail_acquisition_enabled
        and (device_type == DeviceType.ANDROID or simulated)
        and not ((result.staging / "email").is_dir() and any((result.staging / "email").iterdir()))
    ):
        from app.acquisition.gmail_oauth import (
            ensure_gmail_oauth,
            session_acquisition_reference,
        )
        from app.acquisition.gmail_service import GmailAcquisitionService
        from app.acquisition.runtime import AgentRuntimeSecrets, agent_runtime_registry

        token = None
        account_name = None
        if not simulated and settings.android_agent_enabled:
            from app.acquisition.runtime import agent_runtime_registry as registry

            try:
                runtime = await registry.get(session_id)
            except AcquisitionError as exc:
                if exc.category != ErrorCategory.NOT_FOUND:
                    raise
            else:
                account_name = runtime.google_account
                token = runtime.google_token
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
                account_name, token = await ensure_gmail_oauth(
                    client=runtime_client,
                    session_id=session_id,
                    serial=device_id,
                    adb=None,
                    on_progress=on_progress,
                    request_id=context.request_id,
                    existing_account=account_name,
                    existing_token=token,
                )
                await agent_runtime_registry.bind(
                    AgentRuntimeSecrets(
                        session_id=runtime.session_id,
                        serial=runtime.serial,
                        token=runtime.token,
                        forward_host_port=runtime.forward_host_port,
                        token_expires_at=runtime.token_expires_at,
                        google_token=token,
                        google_account=account_name,
                    )
                )
        if simulated or token:
            reference = await session_acquisition_reference(session_id)
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
                reference=reference,
            )
            if gmail_count > 0:
                result = AcquisitionResult(
                    staging=result.staging,
                    item_count=result.item_count + gmail_count,
                    duration_ms=result.duration_ms,
                    method=f"{result.method}+gmail_api",
                    provider=result.provider,
                )

    if settings.browser_history_enabled and device_type == DeviceType.ANDROID:
        from app.acquisition.browser_history import BrowserHistoryAcquisitionService
        from app.acquisition.gmail_oauth import session_acquisition_reference

        reference = await session_acquisition_reference(session_id)
        browser_count = await BrowserHistoryAcquisitionService().acquire(
            session_id=session_id,
            serial=device_id,
            staging=result.staging,
            mode=mode,
            simulated=simulated,
            on_progress=on_progress,
            request_id=context.request_id,
            reference=reference,
        )
        if browser_count > 0:
            result = AcquisitionResult(
                staging=result.staging,
                item_count=result.item_count + browser_count,
                duration_ms=result.duration_ms,
                method=f"{result.method}+chrome_cdp",
                provider=result.provider,
            )

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


async def hash_file(path: Path) -> str:
    def _hash() -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(settings.hash_chunk_bytes)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    return await asyncio.to_thread(_hash)


async def index_staging(session_id: str, staging: Path, on_progress) -> tuple[int, float]:
    t0 = time.perf_counter()
    files: list[tuple] = []
    from app.acquisition.android_recovery.paths import is_recovery_namespace_path
    from app.acquisition.android_recovery.service import (
        detect_recovery_mime_type,
        recovery_metadata,
    )
    from app.acquisition.ios_afc import is_ios_library_path, ios_library_metadata

    recovered_artifacts = await asyncio.to_thread(recovery_metadata, staging)
    ios_library_artifacts = await asyncio.to_thread(ios_library_metadata, staging)

    def indexable_path(path: Path) -> bool:
        if (
            not path.is_file()
            or path.name.endswith(".risk")
            or "_backup" in path.parts
            or any(part.startswith("_") for part in path.parts)
        ):
            return False
        relative = path.relative_to(staging).as_posix()
        is_verified_recovery = relative in recovered_artifacts
        is_verified_ios = relative in ios_library_artifacts
        if is_recovery_namespace_path(relative) and not is_verified_recovery:
            return False
        if is_ios_library_path(relative) and not is_verified_ios:
            return False
        # A validated recovery manifest is authoritative for opaque payload
        # names such as trash/*.bin. Extension-based junk filtering is only for
        # ordinary files discovered by walking staging.
        return is_verified_recovery or is_verified_ios or not _is_junk_media_path(str(path))

    sem = asyncio.Semaphore(settings.worker_concurrency)
    crawl_artifact_rows = await db.fetchall(
        "SELECT a.record_id, a.source_kind, a.role, a.mime_type, a.relative_path, "
        "a.sha256, "
        "r.social_scope, r.source_app, r.canonical_json FROM crawl_artifacts a "
        "JOIN crawl_records r ON r.crawl_id = a.crawl_id AND r.record_id = a.record_id "
        "WHERE a.session_id = ? AND a.verified = 1",
        (session_id,),
    )
    crawl_artifacts = {row["relative_path"]: row for row in crawl_artifact_rows}
    binary_record_ids = {
        str(row["record_id"])
        for row in crawl_artifact_rows
        if str(row["role"] or "") == "source_binary"
    }

    def is_duplicate_canonical_companion(path: Path) -> bool:
        relative = path.relative_to(staging).as_posix()
        artifact = crawl_artifacts.get(relative)
        return bool(
            artifact is not None
            and str(artifact["role"] or "") == "canonical_record"
            and str(artifact["record_id"]) in binary_record_ids
        )

    paths = [
        path
        for path in staging.rglob("*")
        if indexable_path(path) and not is_duplicate_canonical_companion(path)
    ]
    total = len(paths)
    existing_file_rows = await db.fetchall(
        "SELECT id, path FROM files WHERE session_id = ?",
        (session_id,),
    )
    duplicate_canonical_paths = {
        str(row["relative_path"])
        for row in crawl_artifact_rows
        if str(row["role"] or "") == "canonical_record"
        and str(row["record_id"]) in binary_record_ids
    }
    # Older/live ingestion could already have indexed and analyzed the
    # canonical JSON before its source binary arrived. It is a transfer
    # companion, not another acquired file. Remove that technical row and its
    # duplicate findings atomically; the binary below is then analyzed once
    # with canonical metadata merged into its analysis context.
    duplicate_file_rows = [
        row for row in existing_file_rows if str(row["path"]) in duplicate_canonical_paths
    ]
    if duplicate_file_rows:
        async with db.transaction() as conn:
            await conn.executemany(
                "DELETE FROM findings WHERE session_id = ? AND file_id = ?",
                [(session_id, str(row["id"])) for row in duplicate_file_rows],
            )
            await conn.executemany(
                "DELETE FROM files WHERE session_id = ? AND id = ?",
                [(session_id, str(row["id"])) for row in duplicate_file_rows],
            )
        duplicate_ids = {str(row["id"]) for row in duplicate_file_rows}
        existing_file_rows = [
            row for row in existing_file_rows if str(row["id"]) not in duplicate_ids
        ]
    existing_file_ids = {str(row["path"]): str(row["id"]) for row in existing_file_rows}
    crawl_capture_meta: dict[str, dict[str, object]] = {}
    for row in crawl_artifact_rows:
        record_id = str(row["record_id"])
        if record_id in crawl_capture_meta:
            continue
        try:
            payload = json.loads(row["canonical_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("canonical crawl metadata is invalid") from exc
        captured_at = (
            payload.get("source_created_at")
            or payload.get("source_modified_at")
            or payload.get("observed_at")
        )
        captured_year = None
        if isinstance(captured_at, str) and len(captured_at) >= 4:
            try:
                year = int(captured_at[:4])
                captured_year = year if 1970 <= year <= 9999 else None
            except ValueError:
                captured_year = None
        from app.services.gallery import gallery_meta_from_canonical

        gallery_meta = gallery_meta_from_canonical(payload)
        crawl_capture_meta[record_id] = {
            "captured_at": captured_at if isinstance(captured_at, str) else None,
            "captured_year": captured_year,
            "date_source": "android_agent_canonical",
            "canonical_normalized_text": (
                payload.get("normalized_text")
                if isinstance(payload.get("normalized_text"), str)
                else None
            ),
            **gallery_meta,
        }

    async def one(p: Path) -> tuple:
        async with sem:
            rel = str(p.relative_to(staging))
            artifact = crawl_artifacts.get(rel)
            recovered = recovered_artifacts.get(rel)
            ios_artifact = ios_library_artifacts.get(rel)
            source = (
                artifact["source_kind"]
                if artifact is not None
                else ios_artifact.source
                if ios_artifact is not None
                else Path(rel).parts[0] if Path(rel).parts else "other"
            )
            digest = (
                artifact["sha256"]
                if artifact is not None
                else ios_artifact.sha256
                if ios_artifact is not None
                else recovered.sha256 if recovered is not None else await hash_file(p)
            )
            mime = (
                artifact["mime_type"]
                if artifact is not None
                else ios_artifact.mime_type
                if ios_artifact is not None
                else (
                    detect_recovery_mime_type(p, recovered.mime_type)
                    if recovered is not None
                    else guess_mime(p)
                )
            )
            if recovered is not None and p.stat().st_size != recovered.size_bytes:
                raise RuntimeError("artifact recovery Android gagal verifikasi")
            if artifact is not None:
                capture = crawl_capture_meta[str(artifact["record_id"])]
            elif ios_artifact is not None and ios_artifact.captured_epoch_s is not None:
                captured = datetime.fromtimestamp(
                    ios_artifact.captured_epoch_s,
                    tz=timezone.utc,
                ).isoformat()
                capture = {
                    "captured_at": captured,
                    "captured_year": int(captured[:4]),
                    "date_source": "ios_photos_database",
                }
            else:
                from app.services.media_dates import capture_meta

                capture = capture_meta(p)
            meta = {"ext": p.suffix.lower(), **capture}
            if str(source).casefold() == "whatsapp" and p.suffix.lower() == ".json":
                try:
                    whatsapp_payload = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    whatsapp_payload = {}
                if (
                    isinstance(whatsapp_payload, dict)
                    and whatsapp_payload.get("kind") == "whatsapp_message"
                ):
                    from app.acquisition.whatsapp_backup import WHATSAPP_MESSAGE_MIME

                    mime = WHATSAPP_MESSAGE_MIME
                    conversation = whatsapp_payload.get("conversation")
                    message = whatsapp_payload.get("message")
                    conversation = conversation if isinstance(conversation, dict) else {}
                    message = message if isinstance(message, dict) else {}
                    quote = message.get("quote")
                    quote = quote if isinstance(quote, dict) else {}
                    media = message.get("media")
                    media = media if isinstance(media, dict) else {}
                    message_text = message.get("text")
                    if not isinstance(message_text, str) or not message_text.strip():
                        message_text = media.get("caption")
                    if not isinstance(message_text, str) or not message_text.strip():
                        message_text = whatsapp_payload.get("preview_text")
                    if isinstance(message_text, str):
                        message_text = message_text[:131_072]
                    else:
                        message_text = None
                    for key in (
                        "album",
                        "display_name",
                        "captured_at",
                        "source_created_at",
                        "preview_text",
                        "normalized_text",
                    ):
                        value = whatsapp_payload.get(key)
                        if value not in {None, ""}:
                            meta[key] = value
                    meta.update(
                        {
                            "acquisition_method": "whatsapp_crypt15",
                            "artifact_role": "canonical_message",
                            "conversation_id": conversation.get("id"),
                            "conversation_name": conversation.get("name"),
                            "conversation_address": conversation.get("address"),
                            "conversation_type": conversation.get("type"),
                            "message_id": message.get("id"),
                            "message_direction": message.get("direction"),
                            "message_sender": message.get("sender"),
                            "message_type": message.get("type"),
                            "message_text": message_text,
                            "message_timestamp": message.get("timestamp"),
                            "message_starred": bool(message.get("starred")),
                            "message_revoked": bool(message.get("revoked")),
                            "message_forward_score": _nonnegative_int(
                                message.get("forward_score")
                            ),
                            "message_edited_at": message.get("edited_at"),
                            "quoted_text": quote.get("text"),
                        }
                    )
                    captured_at = meta.get("captured_at")
                    if isinstance(captured_at, str) and len(captured_at) >= 4:
                        try:
                            captured_year = int(captured_at[:4])
                        except ValueError:
                            captured_year = 0
                        if 1970 <= captured_year <= 9999:
                            meta["captured_year"] = captured_year
                    meta["date_source"] = "whatsapp_database"
            if str(source).startswith("browser_history") and p.suffix.lower() == ".json":
                try:
                    browser_payload = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    browser_payload = {}
                if isinstance(browser_payload, dict):
                    for key in (
                        "album",
                        "display_name",
                        "captured_at",
                        "access_count",
                        "preview_text",
                        "observed_at",
                    ):
                        value = browser_payload.get(key)
                        if value not in {None, ""}:
                            meta[key] = value
                    if meta.get("observed_at") and not meta.get("captured_at"):
                        meta["captured_at"] = meta["observed_at"]
                    meta["acquisition_method"] = "chrome_cdp"
            if artifact is not None:
                capture_extra = crawl_capture_meta.get(str(artifact["record_id"]), {})
                meta.update(
                    {
                        "acquisition_method": "android_agent_direct_manifest",
                        "crawl_record_id": artifact["record_id"],
                        "crawl_artifact_role": artifact["role"],
                        "social_scope": artifact["social_scope"],
                        "source_app": artifact["source_app"],
                        "directory_hint": capture_extra.get("directory_hint"),
                        "display_name": capture_extra.get("display_name"),
                        "is_favorite": bool(capture_extra.get("is_favorite")),
                        "date_added": capture_extra.get("date_added"),
                        "date_modified": capture_extra.get("date_modified"),
                        "date_taken": capture_extra.get("date_taken"),
                        "album": capture_extra.get("album"),
                    }
                )
                if str(artifact["role"] or "") == "source_binary":
                    meta["canonical_normalized_text"] = capture_extra.get(
                        "canonical_normalized_text"
                    )
            if recovered is not None:
                meta.update(
                    {
                        "acquisition_method": "android_recovery_v1",
                        "recovery_candidate_id": recovered.candidate_id,
                        "recovery_source": recovered.source,
                        "recovery_classification": recovered.classification,
                        "recovery_confidence": recovered.confidence,
                        "recovery_expires_epoch_s": recovered.expires_epoch_s,
                    }
                )
            if ios_artifact is not None:
                meta.update(
                    {
                        "acquisition_method": "ios_photo_library_recovery_v1",
                        "ios_library_classification": ios_artifact.classification,
                        "ios_library_capture_method": ios_artifact.capture_method,
                        "ios_source_uuid": ios_artifact.source_uuid,
                        "ios_original_filename": ios_artifact.original_filename,
                    }
                )
            from app.services.gallery import album_leaf, looks_favorite

            if not meta.get("album"):
                hint = meta.get("directory_hint")
                meta["album"] = album_leaf(
                    hint if isinstance(hint, str) else None,
                    rel,
                    str(source),
                )
            meta["is_favorite"] = bool(meta.get("is_favorite")) or looks_favorite(
                rel,
                str(meta.get("album") or ""),
                str(meta.get("display_name") or ""),
                str(meta.get("directory_hint") or ""),
            )
            if mime == "application/vnd.siksik.crawl-record+json":
                from app.acquisition.agent_client import InventoryRecordV1

                try:
                    record = InventoryRecordV1.model_validate_json(p.read_bytes())
                except (OSError, ValueError) as exc:
                    raise RuntimeError("canonical crawl record is invalid") from exc
                meta.update(
                    {
                        "crawl_id": record.crawl_id,
                        "record_id": record.record_id,
                        "source_kind": record.source_kind,
                        "source_app": record.source_app,
                        "observed_at": record.observed_at,
                        "source_created_at": record.source_created_at,
                        "source_modified_at": record.source_modified_at,
                        "captured_at": record.source_created_at or record.observed_at,
                        "captured_year": int(
                            (record.source_created_at or record.observed_at)[:4]
                        ),
                        "provenance": record.provenance.model_dump(mode="json"),
                        "social_scope": (
                            record.metadata.social_scope
                            if record.source_kind == "visible_ui"
                            else None
                        ),
                    }
                )
            file_id = (
                existing_file_ids.get(rel)
                or (
                    stable_file_id(session_id, rel)
                    if (
                        artifact is not None
                        or recovered is not None
                        or ios_artifact is not None
                        or str(source).casefold() == "whatsapp"
                    )
                    else str(uuid.uuid4())
                )
            )
            return (
                file_id,
                session_id,
                source,
                rel,
                mime,
                p.stat().st_size,
                digest,
                "pulled",
                0,
                json.dumps(meta),
            )

    wave = 64
    indexed = 0
    for start in range(0, total, wave):
        batch = paths[start : start + wave]
        rows = await asyncio.gather(*(one(p) for p in batch))
        files.extend(rows)
        indexed += len(rows)
        pct = 45 + (indexed / max(total, 1)) * 15
        await on_progress(
            SessionStatus.INDEXING,
            pct,
            f"Indexing & hashing ({indexed}/{total})",
            files_listed=total,
            files_pulled=total,
            files_indexed=indexed,
        )

    if files:
        await db.executemany(
            """
            INSERT INTO files (id, session_id, source, path, mime, size_bytes, sha256, pull_status, analyzed, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source,
                path = excluded.path,
                mime = excluded.mime,
                size_bytes = excluded.size_bytes,
                sha256 = excluded.sha256,
                pull_status = excluded.pull_status,
                meta_json = excluded.meta_json
            """,
            files,
        )

    return indexed, (time.perf_counter() - t0) * 1000


def empty_progress(phase: SessionStatus = SessionStatus.PENDING) -> dict:
    return SessionProgress(phase=phase, percent=0, message="Menunggu").model_dump()


def empty_timing() -> dict:
    return TimingBreakdown().model_dump()
