from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
import time
import uuid
from pathlib import Path

from app.core.config import settings
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
DOC_EXT = {".pdf", ".doc", ".docx", ".rtf", ".odt"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".imgmeta"}
VID_EXT = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".vidmeta"}
CHAT_HINTS = ("whatsapp", "telegram", "wa-", "msgstore", "chat")

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
_MEDIA_EXT = IMG_EXT | VID_EXT | TEXT_EXT | DOC_EXT


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
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="ignore"), err.decode(errors="ignore")
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except asyncio.TimeoutError:
        return 124, "", "timeout"


async def toolchain_status() -> dict:
    adb_code, _, _ = await _run(["adb", "version"], timeout=3)
    idevice_code, _, _ = await _run(["idevice_id", "-l"], timeout=3)
    backup_code, _, _ = await _run(["idevicebackup2", "-h"], timeout=3)
    return {
        "adb": adb_code == 0,
        "idevice_id": idevice_code == 0,
        "idevicebackup2": backup_code in (0, 1),  # help often exits 1
    }


async def detect_devices(*, include_simulators: bool = True) -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []

    code, out, _ = await _run(["adb", "devices", "-l"], timeout=5)
    if code == 0:
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                model = "Android"
                for p in parts[2:]:
                    if p.startswith("model:"):
                        model = p.split(":", 1)[1].replace("_", " ")
                ver_code, ver_out, _ = await _run(
                    ["adb", "-s", serial, "shell", "getprop", "ro.build.version.release"],
                    timeout=5,
                )
                os_version = ver_out.strip() if ver_code == 0 and ver_out.strip() else "unknown"
                devices.append(
                    DeviceInfo(
                        device_id=serial,
                        device_type=DeviceType.ANDROID,
                        label=f"{model} ({serial[:8]})",
                        os_version=os_version,
                        connected=True,
                        simulated=False,
                    )
                )

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
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    ext = path.suffix.lower()
    if ext in IMG_EXT:
        return "image/jpeg"
    if ext in VID_EXT:
        return "video/mp4"
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


async def _adb_list_files(device_id: str, remote_dirs: list[str], limit: int) -> list[str]:
    """List files via ADB, newest first, prefer relevant extensions."""
    scored: list[tuple[float, str]] = []
    prefer = tuple(settings.android_prefer_ext)

    for remote in remote_dirs:
        # Newest first using epoch mtime when toybox/find supports -printf; fallback plain find
        code, out, _ = await _run(
            [
                "adb",
                "-s",
                device_id,
                "shell",
                (
                    f'test -d "{remote}" && '
                    f'(find "{remote}" -type f -printf "%T@ %p\\n" 2>/dev/null '
                    f'|| find "{remote}" -type f -exec stat -c "%Y %n" {{}} + 2>/dev/null '
                    f'|| find "{remote}" -type f 2>/dev/null) | head -n {max(limit * 3, 100)}'
                ),
            ],
            timeout=90,
        )
        if code != 0 or not out.strip():
            continue
        for line in out.splitlines():
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
        if len(uniq) >= limit:
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

    paths = settings.android_paths_quick if mode == AcquisitionMode.QUICK else settings.android_paths_full
    limit = settings.adb_max_files_quick if mode == AcquisitionMode.QUICK else settings.adb_max_files_full

    await on_progress(SessionStatus.ACQUIRING, 8, f"Listing file via ADB ({device_id})…", acquisition_method="adb")
    remote_files = await _adb_list_files(device_id, paths, limit)
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

        code, _, err = await _run(
            ["adb", "-s", device_id, "pull", remote, str(local_path)],
            timeout=float(settings.adb_pull_timeout_s),
        )
        if code == 0 and local_path.exists():
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
        raise RuntimeError(f"ADB pull gagal untuk semua kandidat file ({listed} listed). Detail: {err[-200:] if err else 'n/a'}")

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

    code, out, err = await _run(
        ["idevicebackup2", "-u", device_id, "backup", str(backup_dir)],
        timeout=900,
    )
    if code != 0:
        raise RuntimeError(f"idevicebackup2 gagal: {(err or out)[:400]}")

    # Copy interesting extensions out of backup tree into classified folders
    pulled = 0
    candidates = [
        p
        for p in backup_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in (IMG_EXT | VID_EXT | {".heic"})
        and p.suffix.lower() not in {".db", ".sqlite"}
    ]
    if mode == AcquisitionMode.QUICK:
        candidates = candidates[: settings.adb_max_files_quick]

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
        raise RuntimeError("Backup iOS berhasil tetapi tidak ada file terklasifikasi untuk dianalisis.")

    return staging, pulled, (time.perf_counter() - t0) * 1000, "idevicebackup2"


def _zip_skip(name: str) -> bool:
    low = name.replace("\\", "/").lower()
    if "__macosx" in low.split("/"):
        return True
    return _is_junk_media_path(name)


def _bucket_for_file(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in VID_EXT:
        return "video"
    if ext in IMG_EXT:
        return "gallery"
    if ext in TEXT_EXT | DOC_EXT:
        return "documents"
    # path hints
    source = _classify_source(name)
    if source in {"gallery", "video", "documents", "whatsapp", "telegram"}:
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
            structured = bool(tops & {"gallery", "video", "documents", "dcim", "pictures", "download", "movies"})

            for i, member in enumerate(members):
                raw_name = member.filename.replace("\\", "/")
                if raw_name.endswith("/"):
                    continue
                # Cegah zip-slip
                target_name = Path(raw_name).name
                if ".." in Path(raw_name).parts:
                    continue

                if structured:
                    # Normalisasi DCIM/Pictures → gallery, Movies → video, Download → documents
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
                    elif top in {"gallery", "video", "documents", "other", "whatsapp", "telegram"}:
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
) -> tuple[Path, int, float, str]:
    if simulated or device_id.startswith("sim-"):
        return await acquire_simulated(session_id, device_id, mode, scenario, file_count, on_progress)

    if device_type == DeviceType.ANDROID:
        return await acquire_android_adb(session_id, device_id, mode, on_progress)

    if device_type == DeviceType.IOS:
        return await acquire_ios_libimobiledevice(session_id, device_id, mode, on_progress)

    # fallback
    return await acquire_simulated(session_id, device_id, mode, scenario, file_count, on_progress)


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
    paths = [
        p
        for p in staging.rglob("*")
        if p.is_file()
        and not p.name.endswith(".risk")
        and "_backup" not in p.parts
        and not _is_junk_media_path(str(p))
    ]
    total = len(paths)
    sem = asyncio.Semaphore(settings.worker_concurrency)

    async def one(p: Path) -> tuple:
        async with sem:
            from app.services.media_dates import capture_meta

            rel = str(p.relative_to(staging))
            source = Path(rel).parts[0] if Path(rel).parts else "other"
            digest = await hash_file(p)
            meta = {"ext": p.suffix.lower(), **capture_meta(p)}
            return (
                str(uuid.uuid4()),
                session_id,
                source,
                rel,
                guess_mime(p),
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
            """,
            files,
        )

    return indexed, (time.perf_counter() - t0) * 1000


def empty_progress(phase: SessionStatus = SessionStatus.PENDING) -> dict:
    return SessionProgress(phase=phase, percent=0, message="Menunggu").model_dump()


def empty_timing() -> dict:
    return TimingBreakdown().model_dump()
