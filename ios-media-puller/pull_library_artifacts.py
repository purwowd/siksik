#!/usr/bin/env python3
"""Bounded iOS Photos recovery collector used by the SIKSIK acquisition flow."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

PHOTO_DB_FILES = ("Photos.sqlite", "Photos.sqlite-wal", "Photos.sqlite-shm")
THUMBNAIL_ROOTS = (
    "/PhotoData/Thumbnails/V2",
    "/PhotoData/Thumbnails/VideoKeyFrames",
)
ITHMB_ROOT = "/PhotoData/Thumbnails"
APPLE_EPOCH_OFFSET_S = 978_307_200.0
MAX_IOS_EPOCH_S = 4_102_444_800.0
JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"
MEDIA_EXTENSIONS = {
    ".3gp": "video/3gpp",
    ".avi": "video/x-msvideo",
    ".dng": "image/x-adobe-dng",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".jxl": "image/jxl",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}
WAL_ASSET_RE = re.compile(
    rb"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})"
    rb"(DCIM/\d+APPLE|PhotoData/CPLAssets/group\d{1,3})"
    rb"((?:IMG_[0-9A-Za-z_]+|[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12})\."
    rb"(?:HEIC|heic|HEIF|heif|MOV|mov|MP4|mp4|M4V|m4v|JPG|jpg|JPEG|jpeg|"
    rb"PNG|png|JXL|jxl|GIF|gif|DNG|dng|TIF|tif|TIFF|tiff|WEBP|webp))"
)
UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


@dataclass(frozen=True, slots=True)
class PhotoAsset:
    uuid: str | None
    directory: str
    filename: str
    captured_epoch_s: float | None
    hidden: bool
    trashed: bool

    @property
    def remote_path(self) -> str:
        directory = self.directory.strip("/").replace("\\", "/")
        candidate = PurePosixPath("/", directory, self.filename)
        if ".." in candidate.parts:
            raise ValueError("unsafe Photos asset path")
        value = candidate.as_posix()
        if PurePosixPath(self.filename).name != self.filename or len(self.filename) > 255:
            raise ValueError("unsafe Photos asset filename")
        if not value.startswith(("/DCIM/", "/PhotoData/CPLAssets/")):
            raise ValueError("Photos asset path is outside public AFC roots")
        return value


@dataclass(frozen=True, slots=True)
class Artifact:
    relative_path: str
    source: str
    classification: str
    capture_method: str
    mime_type: str
    size_bytes: int
    sha256: str
    source_uuid: str | None = None
    original_filename: str | None = None
    captured_epoch_s: float | None = None


@dataclass(slots=True)
class CaptureBudget:
    max_items: int
    max_bytes: int
    max_file_bytes: int
    bytes_captured: int = 0
    hashes: set[str] = field(default_factory=set)

    def remaining_bytes(self) -> int:
        return max(0, self.max_bytes - self.bytes_captured)

    def allows(self, artifacts: list[Artifact], size: int) -> bool:
        return (
            len(artifacts) < self.max_items
            and 0 < size <= self.max_file_bytes
            and size <= self.remaining_bytes()
        )


@dataclass(slots=True)
class PurgeEvidence:
    uuid: str
    filename: str | None = None
    directory: str | None = None
    deleted_epoch_s: float | None = None
    reason: str | None = None
    sources: set[str] = field(default_factory=set)

    def merge(self, other: "PurgeEvidence") -> None:
        self.filename = self.filename or other.filename
        self.directory = self.directory or other.directory
        self.deleted_epoch_s = self.deleted_epoch_s or other.deleted_epoch_s
        self.reason = self.reason or other.reason
        self.sources.update(other.sources)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _asset_table(connection: sqlite3.Connection) -> str:
    names = _table_names(connection)
    for candidate in ("ZASSET", "ZGENERICASSET"):
        if candidate in names:
            return candidate
    raise RuntimeError("Photos asset table unavailable")


def _date_expression(columns: set[str], *, deleted: bool) -> str | None:
    candidates = (
        ("ZTRASHEDDATE", "ZDATECREATED", "ZADDEDDATE", "ZMODIFICATIONDATE")
        if deleted
        else ("ZDATECREATED", "ZADDEDDATE", "ZMODIFICATIONDATE")
    )
    available = [f"z.{name}" for name in candidates if name in columns]
    if not available:
        return None
    return available[0] if len(available) == 1 else f"COALESCE({', '.join(available)})"


def _apple_to_unix(value: object) -> float | None:
    try:
        raw = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if raw <= 0:
        return None
    converted = raw + APPLE_EPOCH_OFFSET_S
    return converted if converted <= MAX_IOS_EPOCH_S else None


def query_album_assets(
    database: Path,
    *,
    album: str,
    limit: int,
    not_before_epoch_s: float,
) -> tuple[list[PhotoAsset], int]:
    """Read Hidden or Recently Deleted assets from a copied Photos database."""
    if album not in {"hidden", "recently_deleted"}:
        raise ValueError("unsupported Photos album")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        table = _asset_table(connection)
        columns = _table_columns(connection, table)
        required = {"ZDIRECTORY", "ZFILENAME"}
        if not required.issubset(columns):
            raise RuntimeError("Photos asset path columns unavailable")
        flag = "ZHIDDEN" if album == "hidden" else "ZTRASHEDSTATE"
        if flag not in columns:
            raise RuntimeError(f"Photos {album} state unavailable")
        uuid_sql = "z.ZUUID" if "ZUUID" in columns else "NULL"
        hidden_sql = "IFNULL(z.ZHIDDEN, 0)" if "ZHIDDEN" in columns else "0"
        trashed_sql = (
            "IFNULL(z.ZTRASHEDSTATE, 0)" if "ZTRASHEDSTATE" in columns else "0"
        )
        date_sql = _date_expression(columns, deleted=album == "recently_deleted")
        date_select = date_sql or "NULL"
        conditions = [
            "z.ZDIRECTORY IS NOT NULL",
            "z.ZFILENAME IS NOT NULL",
            f"IFNULL(z.{flag}, 0) != 0",
        ]
        if album == "hidden" and "ZTRASHEDSTATE" in columns:
            conditions.append(f"{trashed_sql} = 0")
        parameters: list[Any] = []
        if date_sql is not None:
            conditions.append(f"({date_sql} IS NULL OR {date_sql} >= ?)")
            parameters.append(not_before_epoch_s - APPLE_EPOCH_OFFSET_S)
        parameters.append(limit)
        rows = connection.execute(
            f"""
            SELECT {uuid_sql} AS uuid, z.ZDIRECTORY AS directory,
                   z.ZFILENAME AS filename, {date_select} AS asset_date,
                   {hidden_sql} AS hidden, {trashed_sql} AS trashed
            FROM {table} z
            WHERE {' AND '.join(conditions)}
            ORDER BY IFNULL({date_select}, 0) DESC, z.Z_PK DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        assets: list[PhotoAsset] = []
        unknown_dates = 0
        for row in rows:
            filename = str(row["filename"])
            if Path(filename).suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            raw_uuid = str(row["uuid"]) if row["uuid"] else ""
            uuid = raw_uuid.upper() if UUID_RE.fullmatch(raw_uuid) else None
            captured = _apple_to_unix(row["asset_date"])
            if captured is None:
                unknown_dates += 1
            assets.append(
                PhotoAsset(
                    uuid=uuid,
                    directory=str(row["directory"]),
                    filename=filename,
                    captured_epoch_s=captured,
                    hidden=bool(row["hidden"]),
                    trashed=bool(row["trashed"]),
                )
            )
        return assets, unknown_dates
    finally:
        connection.close()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _candidate_id(source: str, identity: str) -> str:
    return hashlib.sha256(f"{source}\0{identity}".encode("utf-8")).hexdigest()[:32]


async def _download_asset(
    afc: Any,
    output: Path,
    source: str,
    classification: str,
    asset: PhotoAsset,
    budget: CaptureBudget,
    artifacts: list[Artifact],
    warnings: set[str],
) -> None:
    try:
        remote = asset.remote_path
        if not await afc.exists(remote):
            warnings.add(f"{source}_original_unavailable")
            return
        info = await afc.stat(remote)
        reported = int(info.get("st_size") or 0)
    except Exception:
        warnings.add(f"{source}_source_probe_failed")
        return
    if not budget.allows(artifacts, reported):
        warnings.add("ios_library_budget_truncated")
        return
    extension = Path(asset.filename).suffix.lower()
    identity = asset.uuid or remote
    relative = PurePosixPath(source, f"{_candidate_id(source, identity)}{extension}")
    destination = output / relative.as_posix()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    try:
        await afc.pull(remote, str(partial), progress_bar=False)
        digest, actual = _sha256_file(partial)
        if not budget.allows(artifacts, actual):
            warnings.add("ios_library_budget_truncated")
            return
        if digest in budget.hashes:
            warnings.add("ios_library_duplicate_payload")
            return
        os.replace(partial, destination)
        budget.hashes.add(digest)
        budget.bytes_captured += actual
        artifacts.append(
            Artifact(
                relative_path=relative.as_posix(),
                source=source,
                classification=classification,
                capture_method="afc_pull",
                mime_type=MEDIA_EXTENSIONS[extension],
                size_bytes=actual,
                sha256=digest,
                source_uuid=asset.uuid,
                original_filename=asset.filename,
                captured_epoch_s=asset.captured_epoch_s,
            )
        )
    except Exception:
        warnings.add(f"{source}_pull_failed")
    finally:
        partial.unlink(missing_ok=True)


async def _safe_listdir(
    afc: Any,
    remote: str,
    warnings: set[str],
    warning: str,
) -> list[str]:
    try:
        if not await afc.exists(remote):
            return []
        return [str(item) for item in await afc.listdir(remote) if item not in {".", ".."}]
    except Exception:
        warnings.add(warning)
        return []


async def _cache_candidates(
    afc: Any,
    *,
    item_limit: int,
    entry_limit: int,
    warnings: set[str],
) -> tuple[list[str], bool]:
    found: list[str] = []
    visited = 0
    truncated = False
    for root in THUMBNAIL_ROOTS:
        queue: list[tuple[str, int]] = [(root, 0)]
        while queue and len(found) < item_limit and visited < entry_limit:
            current, depth = queue.pop(0)
            for name in await _safe_listdir(
                afc,
                current,
                warnings,
                "ios_cache_discovery_failed",
            ):
                visited += 1
                remote = f"{current.rstrip('/')}/{name}"
                low = name.lower()
                if low in {"5005.jpg", "localvideokeyframe.jpg"}:
                    found.append(remote)
                    if len(found) >= item_limit:
                        break
                    continue
                if depth >= 5 or low.startswith("."):
                    continue
                try:
                    if await afc.isdir(remote):
                        queue.append((remote, depth + 1))
                except Exception:
                    warnings.add("ios_cache_discovery_failed")
                    continue
                if visited >= entry_limit:
                    break
        if queue or visited >= entry_limit:
            truncated = True
        if len(found) >= item_limit:
            truncated = True
            break
    return found, truncated


def _is_jpeg(path: Path) -> bool:
    try:
        if path.stat().st_size < len(JPEG_SOI) + len(JPEG_EOI):
            return False
        with path.open("rb") as handle:
            if handle.read(len(JPEG_SOI)) != JPEG_SOI:
                return False
            handle.seek(-len(JPEG_EOI), os.SEEK_END)
            return handle.read(len(JPEG_EOI)) == JPEG_EOI
    except OSError:
        return False


async def _download_cache_file(
    afc: Any,
    remote: str,
    output: Path,
    budget: CaptureBudget,
    artifacts: list[Artifact],
    warnings: set[str],
) -> None:
    try:
        info = await afc.stat(remote)
        reported = int(info.get("st_size") or 0)
    except Exception:
        warnings.add("ios_cache_probe_failed")
        return
    if not budget.allows(artifacts, reported):
        warnings.add("ios_library_budget_truncated")
        return
    identity = remote
    relative = PurePosixPath(
        "ios_recovered_cache",
        f"{_candidate_id('ios_recovered_cache', identity)}.jpg",
    )
    destination = output / relative.as_posix()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    try:
        await afc.pull(remote, str(partial), progress_bar=False)
        digest, actual = _sha256_file(partial)
        if not _is_jpeg(partial):
            warnings.add("ios_cache_payload_invalid")
            return
        if not budget.allows(artifacts, actual):
            warnings.add("ios_library_budget_truncated")
            return
        if digest in budget.hashes:
            return
        os.replace(partial, destination)
        budget.hashes.add(digest)
        budget.bytes_captured += actual
        artifacts.append(
            Artifact(
                relative_path=relative.as_posix(),
                source="ios_recovered_cache",
                classification="photos_thumbnail_cache",
                capture_method="afc_pull",
                mime_type="image/jpeg",
                size_bytes=actual,
                sha256=digest,
            )
        )
    except Exception:
        warnings.add("ios_cache_pull_failed")
    finally:
        partial.unlink(missing_ok=True)


def _jpeg_spans(data: bytes, limit: int) -> list[bytes]:
    values: list[bytes] = []
    cursor = 0
    while len(values) < limit:
        start = data.find(JPEG_SOI, cursor)
        if start < 0:
            break
        end = data.find(JPEG_EOI, start + len(JPEG_SOI))
        if end < 0:
            break
        end += len(JPEG_EOI)
        value = data[start:end]
        if 2_048 <= len(value) <= 32 * 1024 * 1024:
            values.append(value)
        cursor = end
    return values


def _store_cache_bytes(
    output: Path,
    identity: str,
    payload: bytes,
    budget: CaptureBudget,
    artifacts: list[Artifact],
    warnings: set[str],
) -> None:
    if not budget.allows(artifacts, len(payload)):
        warnings.add("ios_library_budget_truncated")
        return
    digest = hashlib.sha256(payload).hexdigest()
    if digest in budget.hashes:
        return
    relative = PurePosixPath(
        "ios_recovered_cache",
        f"{_candidate_id('ios_ithmb', identity)}.jpg",
    )
    destination = output / relative.as_posix()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    try:
        partial.write_bytes(payload)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    budget.hashes.add(digest)
    budget.bytes_captured += len(payload)
    artifacts.append(
        Artifact(
            relative_path=relative.as_posix(),
            source="ios_recovered_cache",
            classification="ithmb_jpeg_carve",
            capture_method="cache_carve",
            mime_type="image/jpeg",
            size_bytes=len(payload),
            sha256=digest,
        )
    )


async def _carve_ithmb(
    afc: Any,
    output: Path,
    control: Path,
    *,
    source_limit: int,
    source_bytes: int,
    candidate_limit: int,
    budget: CaptureBudget,
    artifacts: list[Artifact],
    warnings: set[str],
) -> None:
    names = [
        name
        for name in await _safe_listdir(
            afc,
            ITHMB_ROOT,
            warnings,
            "ios_ithmb_discovery_failed",
        )
        if name.lower().endswith(".ithmb")
    ][:source_limit]
    for source_index, name in enumerate(names):
        if len(artifacts) >= budget.max_items:
            warnings.add("ios_library_budget_truncated")
            return
        remote = f"{ITHMB_ROOT}/{name}"
        try:
            info = await afc.stat(remote)
            size = int(info.get("st_size") or 0)
            if size < 1 or size > source_bytes:
                warnings.add("ios_ithmb_source_oversized")
                continue
            local = control / f"ithmb_{source_index}.bin"
            await afc.pull(remote, str(local), progress_bar=False)
            data = local.read_bytes()
            for index, payload in enumerate(_jpeg_spans(data, candidate_limit)):
                _store_cache_bytes(
                    output,
                    f"{remote}:{index}",
                    payload,
                    budget,
                    artifacts,
                    warnings,
                )
        except Exception:
            warnings.add("ios_ithmb_parse_failed")


def _merge_evidence(target: dict[str, PurgeEvidence], item: PurgeEvidence) -> None:
    existing = target.get(item.uuid)
    if existing is None:
        target[item.uuid] = item
    else:
        existing.merge(item)


def extract_purge_evidence(
    database: Path,
    *,
    max_wal_bytes: int = 256 * 1024 * 1024,
) -> list[PurgeEvidence]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        table = _asset_table(connection)
        columns = _table_columns(connection, table)
        live: set[str] = set()
        if "ZUUID" in columns:
            live = {
                str(row[0]).upper()
                for row in connection.execute(
                    f"SELECT ZUUID FROM {table} WHERE ZUUID IS NOT NULL"
                )
            }
        evidence: dict[str, PurgeEvidence] = {}
        if "ACHANGE" in _table_names(connection):
            tombstones = _table_columns(connection, "ACHANGE")
            selected = [
                name
                for name in (
                    "ZTOMBSTONE0",
                    "ZTOMBSTONE2",
                    "ZTOMBSTONE3",
                    "ZTOMBSTONE5",
                )
                if name in tombstones
            ]
            if "ZTOMBSTONE0" in selected:
                for row in connection.execute(
                    f"SELECT {', '.join(selected)} FROM ACHANGE "
                    "WHERE ZTOMBSTONE0 IS NOT NULL"
                ):
                    values = dict(zip(selected, row))
                    raw_uuid = values.get("ZTOMBSTONE0")
                    if not isinstance(raw_uuid, str) or not UUID_RE.fullmatch(raw_uuid):
                        continue
                    uuid = raw_uuid.upper()
                    if uuid in live:
                        continue
                    _merge_evidence(
                        evidence,
                        PurgeEvidence(
                            uuid=uuid,
                            deleted_epoch_s=_apple_to_unix(values.get("ZTOMBSTONE3")),
                            reason=(
                                str(values["ZTOMBSTONE2"])[:256]
                                if values.get("ZTOMBSTONE2") is not None
                                else None
                            ),
                            sources={"photos_achange_tombstone"},
                        ),
                    )
        wal = database.with_name("Photos.sqlite-wal")
        if wal.is_file() and wal.stat().st_size <= max_wal_bytes:
            data = wal.read_bytes()
            for match in WAL_ASSET_RE.finditer(data):
                uuid = match.group(1).decode("ascii").upper()
                if uuid in live:
                    continue
                directory = match.group(2).decode("ascii", "ignore")
                filename = match.group(3).decode("ascii", "ignore")
                _merge_evidence(
                    evidence,
                    PurgeEvidence(
                        uuid=uuid,
                        filename=filename,
                        directory=directory,
                        sources={"photos_sqlite_wal"},
                    ),
                )
        return sorted(
            evidence.values(),
            key=lambda item: (-(item.deleted_epoch_s or 0), item.filename or "", item.uuid),
        )
    finally:
        connection.close()


def _store_purge_metadata(
    output: Path,
    evidence: PurgeEvidence,
    budget: CaptureBudget,
    artifacts: list[Artifact],
    warnings: set[str],
) -> None:
    relative = PurePosixPath(
        "ios_deleted_metadata",
        f"{_candidate_id('ios_deleted_metadata', evidence.uuid)}.json",
    )
    payload = {
        "schema_version": 1,
        "record_type": "ios_photos_purge_evidence",
        "source_uuid": evidence.uuid,
        "original_filename": evidence.filename,
        "original_directory": evidence.directory,
        "deleted_at": (
            datetime.fromtimestamp(evidence.deleted_epoch_s, tz=timezone.utc).isoformat()
            if evidence.deleted_epoch_s is not None
            else None
        ),
        "expunge_reason": evidence.reason,
        "evidence_sources": sorted(evidence.sources),
        "media_bytes_recovered": False,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    if not budget.allows(artifacts, len(encoded)):
        warnings.add("ios_library_budget_truncated")
        return
    digest = hashlib.sha256(encoded).hexdigest()
    if digest in budget.hashes:
        return
    destination = output / relative.as_posix()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    budget.hashes.add(digest)
    budget.bytes_captured += len(encoded)
    artifacts.append(
        Artifact(
            relative_path=relative.as_posix(),
            source="ios_deleted_metadata",
            classification="purged_metadata_only",
            capture_method="photos_database_parse",
            mime_type="application/json",
            size_bytes=len(encoded),
            sha256=digest,
            source_uuid=evidence.uuid,
            original_filename=evidence.filename,
            captured_epoch_s=evidence.deleted_epoch_s,
        )
    )


async def _pull_photos_database(afc: Any, control: Path, max_file_bytes: int) -> Path:
    control.mkdir(parents=True, exist_ok=True)
    for name in PHOTO_DB_FILES:
        remote = f"/PhotoData/{name}"
        if not await afc.exists(remote):
            if name == "Photos.sqlite":
                raise FileNotFoundError("Photos.sqlite unavailable")
            continue
        info = await afc.stat(remote)
        size = int(info.get("st_size") or 0)
        if size < 1 or size > max_file_bytes:
            raise RuntimeError("Photos database source is outside the byte limit")
        await afc.pull(remote, str(control / name), progress_bar=False)
    return control / "Photos.sqlite"


async def collect(args: argparse.Namespace) -> int:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.afc import AfcService

    output = args.output.expanduser().resolve()
    if output.exists():
        raise RuntimeError("output directory already exists")
    output.mkdir(parents=True)
    control = output / "_control"
    artifacts: list[Artifact] = []
    warnings: set[str] = set()
    budget = CaptureBudget(args.max_items, args.max_bytes, args.max_file_bytes)
    lockdown = await create_using_usbmux(serial=args.udid)
    try:
        async with AfcService(lockdown) as afc:
            database = await _pull_photos_database(
                afc,
                control,
                args.max_cache_source_bytes,
            )
            for album, source, classification, limit in (
                ("hidden", "ios_hidden", "hidden_album", args.hidden_limit),
                (
                    "recently_deleted",
                    "ios_recently_deleted",
                    "recently_deleted_album",
                    args.deleted_limit,
                ),
            ):
                try:
                    assets, unknown_dates = query_album_assets(
                        database,
                        album=album,
                        limit=limit,
                        not_before_epoch_s=args.not_before_epoch_s,
                    )
                except (RuntimeError, sqlite3.DatabaseError):
                    warnings.add(f"{source}_query_unavailable")
                    continue
                if unknown_dates:
                    warnings.add(f"{source}_date_unknown")
                for asset in assets:
                    await _download_asset(
                        afc,
                        output,
                        source,
                        classification,
                        asset,
                        budget,
                        artifacts,
                        warnings,
                    )

            candidates, cache_truncated = await _cache_candidates(
                afc,
                item_limit=args.cache_limit,
                entry_limit=args.cache_entry_limit,
                warnings=warnings,
            )
            if cache_truncated:
                warnings.add("ios_cache_discovery_truncated")
            for remote in candidates:
                await _download_cache_file(
                    afc,
                    remote,
                    output,
                    budget,
                    artifacts,
                    warnings,
                )
            cache_captured = sum(
                item.source == "ios_recovered_cache" for item in artifacts
            )
            remaining_cache = max(0, args.cache_limit - cache_captured)
            if remaining_cache:
                await _carve_ithmb(
                    afc,
                    output,
                    control,
                    source_limit=args.ithmb_source_limit,
                    source_bytes=args.max_cache_source_bytes,
                    candidate_limit=remaining_cache,
                    budget=budget,
                    artifacts=artifacts,
                    warnings=warnings,
                )
            try:
                purge = extract_purge_evidence(
                    database,
                    max_wal_bytes=args.max_cache_source_bytes,
                )
            except (RuntimeError, ValueError, sqlite3.DatabaseError, OSError):
                warnings.add("ios_purge_metadata_unavailable")
                purge = []
            retained = [
                item
                for item in purge
                if item.deleted_epoch_s is None
                or item.deleted_epoch_s >= args.not_before_epoch_s
            ][: args.metadata_limit]
            if len(purge) > len(retained):
                warnings.add("ios_purge_metadata_truncated")
            for item in retained:
                _store_purge_metadata(output, item, budget, artifacts, warnings)
    finally:
        try:
            await lockdown.close()
        except Exception:
            warnings.add("ios_lockdown_close_failed")
        await asyncio.sleep(0.1)

    by_source: dict[str, int] = {}
    for artifact in artifacts:
        by_source[artifact.source] = by_source.get(artifact.source, 0) + 1
    manifest = {
        "schema_version": 1,
        "status": "partial" if warnings else "complete",
        "not_before_epoch_s": args.not_before_epoch_s,
        "artifacts": [asdict(item) for item in artifacts],
        "stats": {
            "captured": len(artifacts),
            "bytes_captured": budget.bytes_captured,
            "by_source": by_source,
        },
        "warnings": sorted(warnings),
    }
    manifest_path = output / "manifest-v1.json"
    partial = output / ".manifest-v1.json.partial"
    partial.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(partial, manifest_path)
    shutil.rmtree(control, ignore_errors=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--udid", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--not-before-epoch-s", type=float, required=True)
    parser.add_argument("--hidden-limit", type=int, required=True)
    parser.add_argument("--deleted-limit", type=int, required=True)
    parser.add_argument("--cache-limit", type=int, required=True)
    parser.add_argument("--metadata-limit", type=int, required=True)
    parser.add_argument("--max-items", type=int, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--max-file-bytes", type=int, required=True)
    parser.add_argument("--max-cache-source-bytes", type=int, required=True)
    parser.add_argument("--cache-entry-limit", type=int, required=True)
    parser.add_argument("--ithmb-source-limit", type=int, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", args.udid):
        parser.error("invalid UDID")
    for name in (
        "hidden_limit",
        "deleted_limit",
        "cache_limit",
        "metadata_limit",
        "max_items",
        "max_bytes",
        "max_file_bytes",
        "max_cache_source_bytes",
        "cache_entry_limit",
        "ithmb_source_limit",
    ):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if not 946_684_800 <= args.not_before_epoch_s <= MAX_IOS_EPOCH_S:
        parser.error("invalid acquisition cutoff")
    return args


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(collect(args))
    except (FileNotFoundError, RuntimeError, OSError, sqlite3.DatabaseError) as exc:
        print(f"ios_library_error={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
