from __future__ import annotations

import re
import struct
import zlib
from pathlib import PurePosixPath
from typing import Any, Iterable

from app.acquisition.android_recovery.contracts import (
    CacheImageRecord,
    ImageSpan,
    MediaStoreRow,
)
from app.acquisition.android_recovery.paths import canonical_shared_path
from app.acquisition.errors import ErrorCategory, acquisition_error

MEDIA_COLUMNS = (
    "_id",
    "_data",
    "_display_name",
    "date_expires",
    "is_trashed",
    "mime_type",
    "_size",
)
ROW_PREFIX = re.compile(r"^Row:\s+\d+\s+")
MEDIA_FIELD = re.compile(
    r"(?:^|,\s)(" + "|".join(re.escape(value) for value in MEDIA_COLUMNS) + r")="
)
TRASH_NAME = re.compile(r"^\.trashed-(\d+)-(.+)$", re.DOTALL)
CONTROL_FILE = re.compile(
    r"(?:^\.nomedia$|^(?:trash_bin\.db|tran_bin_db)(?:-(?:wal|shm|journal))?$|^\.DS_Store$)",
    re.IGNORECASE,
)
TRASH_DIRECTORY_NAMES = frozenset(
    {
        ".trash",
        "trash",
        ".recyclebin",
        "recyclebin",
        "recycle bin",
        ".trashbin",
        ".trashbin_file",
        ".filesbygoogletrash",
        ".filemanagerrecycler",
        "recently deleted",
        "recentlydeleted",
        ".recentlydeleted",
    }
)
GALLERY_INDEX_MAGIC = 0xB3273030
GALLERY_DATA_MAGIC = 0xBD248510
GALLERY_INDEX_HEADER_SIZE = 32
GALLERY_BLOB_HEADER_SIZE = 20
MAX_CACHE_RECORD_BYTES = 128 * 1024 * 1024
THUMBDATA_SLOT_SIZE = 10_000
CLASSIC_THUMBNAIL = re.compile(
    r"^(?:thumb[-_.]?)?(\d{1,19})\.(?:jpe?g|png|webp)$",
    re.IGNORECASE,
)


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def parse_media_store_rows(text: str) -> list[MediaStoreRow]:
    rows: list[MediaStoreRow] = []
    for line in text.splitlines():
        payload = ROW_PREFIX.sub("", line, count=1)
        if payload == line:
            continue
        matches = list(MEDIA_FIELD.finditer(payload))
        fields: dict[str, str] = {}
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
            fields[match.group(1)] = payload[start:end]
        media_id = fields.get("_id", "")
        if not media_id.isdigit():
            continue
        raw_path = fields.get("_data", "")
        path = "" if raw_path in {"", "null", "NULL"} else canonical_shared_path(raw_path)
        mime = fields.get("mime_type")
        rows.append(
            MediaStoreRow(
                media_id=media_id,
                path=path,
                display_name=fields.get("_display_name", ""),
                mime_type=None if mime in {None, "", "null", "NULL"} else mime,
                size_bytes=_optional_int(fields.get("_size")),
                expires_epoch_s=_optional_int(fields.get("date_expires")),
                is_trashed=fields.get("is_trashed") == "1",
            )
        )
    return rows


def is_control_file(path: str) -> bool:
    return bool(CONTROL_FILE.fullmatch(PurePosixPath(path).name))


def is_trash_path(path: str) -> bool:
    pure = PurePosixPath(path)
    if TRASH_NAME.fullmatch(pure.name):
        return True
    for part in pure.parts[:-1]:
        lowered = part.casefold()
        if lowered in TRASH_DIRECTORY_NAMES:
            return True
        compact = re.sub(r"[\s._-]+", "", lowered)
        if compact in {
            "trash",
            "trashbin",
            "recycle",
            "recyclebin",
            "recentlydeleted",
            "filemanagerrecycler",
        }:
            return True
    return False


def trash_original_name(path: str) -> str:
    name = PurePosixPath(path).name
    match = TRASH_NAME.fullmatch(name)
    return match.group(2) if match else name


def trash_expires(path: str) -> int | None:
    match = TRASH_NAME.fullmatch(PurePosixPath(path).name)
    return int(match.group(1)) if match else None


JPEG_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _find_pngs(data: Any) -> list[ImageSpan]:
    signature = b"\x89PNG\r\n\x1a\n"
    images: list[ImageSpan] = []
    cursor = 0
    while True:
        start = data.find(signature, cursor)
        if start < 0:
            break
        cursor = start + 1
        position = start + len(signature)
        width = height = None
        chunks = 0
        while position + 12 <= len(data):
            length = struct.unpack_from(">I", data, position)[0]
            kind = bytes(data[position + 4 : position + 8])
            chunk_end = position + 12 + length
            if length > MAX_CACHE_RECORD_BYTES or chunk_end > len(data):
                break
            payload = data[position + 8 : position + 8 + length]
            expected = struct.unpack_from(">I", data, position + 8 + length)[0]
            if zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF != expected:
                break
            if chunks == 0:
                if kind != b"IHDR" or length != 13:
                    break
                width, height = struct.unpack_from(">II", payload, 0)
                if not width or not height:
                    break
            chunks += 1
            position = chunk_end
            if kind == b"IEND" and length == 0 and width and height:
                images.append(
                    ImageSpan("png", ".png", start, chunk_end, width, height, "png_chunks_crc")
                )
                cursor = chunk_end
                break
    return images


def _find_jpegs(data: Any) -> list[ImageSpan]:
    images: list[ImageSpan] = []
    cursor = 0
    while True:
        start = data.find(b"\xff\xd8\xff", cursor)
        if start < 0:
            break
        cursor = start + 1
        position = start + 2
        width = height = None
        valid = True
        while position < len(data):
            if data[position] != 0xFF:
                valid = False
                break
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                valid = False
                break
            marker = data[position]
            position += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 0x00 or 0xD0 <= marker <= 0xD8 or marker == 0x01:
                continue
            if position + 2 > len(data):
                valid = False
                break
            segment_length = struct.unpack_from(">H", data, position)[0]
            if segment_length < 2 or position + segment_length > len(data):
                valid = False
                break
            if marker in JPEG_SOF and segment_length >= 7:
                height = struct.unpack_from(">H", data, position + 3)[0]
                width = struct.unpack_from(">H", data, position + 5)[0]
            position += segment_length
        end_marker = data.find(b"\xff\xd9", max(position, start + 3))
        if valid and end_marker >= 0 and width and height:
            end = end_marker + 2
            images.append(ImageSpan("jpeg", ".jpg", start, end, width, height, "jpeg_sof_eoi"))
            cursor = end
    return images


def _unsigned_24_le(data: Any, offset: int) -> int:
    return data[offset] | data[offset + 1] << 8 | data[offset + 2] << 16


def _find_webps(data: Any) -> list[ImageSpan]:
    images: list[ImageSpan] = []
    cursor = 0
    while True:
        start = data.find(b"RIFF", cursor)
        if start < 0:
            break
        cursor = start + 1
        if start + 20 > len(data) or bytes(data[start + 8 : start + 12]) != b"WEBP":
            continue
        end = start + 8 + struct.unpack_from("<I", data, start + 4)[0]
        if end > len(data) or end <= start + 20:
            continue
        kind = bytes(data[start + 12 : start + 16])
        payload = start + 20
        width = height = None
        if kind == b"VP8X" and payload + 10 <= end:
            width = _unsigned_24_le(data, payload + 4) + 1
            height = _unsigned_24_le(data, payload + 7) + 1
        elif kind == b"VP8L" and payload + 5 <= end and data[payload] == 0x2F:
            one, two, three, four = data[payload + 1 : payload + 5]
            width = 1 + one + ((two & 0x3F) << 8)
            height = 1 + (two >> 6) + (three << 2) + ((four & 0x0F) << 10)
        elif kind == b"VP8 " and payload + 10 <= end and bytes(data[payload + 3 : payload + 6]) == b"\x9d\x01\x2a":
            width = struct.unpack_from("<H", data, payload + 6)[0] & 0x3FFF
            height = struct.unpack_from("<H", data, payload + 8)[0] & 0x3FFF
        if width and height:
            images.append(ImageSpan("webp", ".webp", start, end, width, height, "webp_riff_dimensions"))
            cursor = end
    return images


def find_images(data: Any) -> list[ImageSpan]:
    return sorted(
        [*_find_pngs(data), *_find_jpegs(data), *_find_webps(data)],
        key=lambda item: (item.offset, item.end, item.format),
    )


def parse_gallery_index(data: bytes) -> tuple[dict[str, int], dict[int, list[int]]]:
    if len(data) < GALLERY_INDEX_HEADER_SIZE:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Index cache galeri tidak lengkap.")
    fields = struct.unpack_from("<8I", data, 0)
    if fields[0] != GALLERY_INDEX_MAGIC or fields[7] != zlib.adler32(data[:28]) & 0xFFFFFFFF:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Index cache galeri tidak valid.")
    if fields[3] not in (0, 1):
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Region cache galeri tidak valid.")
    payload_size = len(data) - GALLERY_INDEX_HEADER_SIZE
    if payload_size <= 0 or payload_size % 24:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Slot cache galeri tidak valid.")
    entries = payload_size // 24
    if entries > 10_000_000:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Slot cache galeri melewati batas.")
    offsets: dict[int, list[int]] = {}
    for region in (0, 1):
        start = GALLERY_INDEX_HEADER_SIZE + region * entries * 12
        offsets[region] = sorted(
            {
                offset
                for slot in range(entries)
                for _, offset in [struct.unpack_from("<QI", data, start + slot * 12)]
                if offset >= 4
            }
        )
    return {
        "active_region": fields[3],
        "active_bytes": fields[5],
    }, offsets


def _cache_reference(prefix: bytes) -> tuple[str, str]:
    if len(prefix) % 2:
        return "", ""
    try:
        text = prefix.decode("utf-16le").rstrip("\x00")
    except UnicodeDecodeError:
        return "", ""
    head = text.rsplit("+", 2)[0]
    if "+" in head:
        model_key, source_path = head.split("+", 1)
    else:
        model_key, source_path = head, ""
    media = re.search(r"/(?:image|video)/item/(\d+)$", model_key)
    return media.group(1) if media else "", canonical_shared_path(source_path) if source_path.startswith("/") else ""


def gallery_cache_records(
    data: Any,
    *,
    expected_bytes: int | None,
    salvage_offsets: Iterable[int],
) -> list[CacheImageRecord]:
    if len(data) < 4 or struct.unpack_from("<I", data, 0)[0] != GALLERY_DATA_MAGIC:
        raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Data cache galeri tidak valid.")
    positions: list[int] = []
    if expected_bytes is None:
        positions = sorted(set(salvage_offsets))
    else:
        if expected_bytes < 4 or expected_bytes > len(data):
            raise acquisition_error(ErrorCategory.VALIDATION_ERROR, "Ukuran cache galeri tidak valid.")
        position = 4
        while position + GALLERY_BLOB_HEADER_SIZE <= expected_bytes:
            _, _, stored, length = struct.unpack_from("<QIII", data, position)
            if stored != position or length < 1 or length > MAX_CACHE_RECORD_BYTES:
                break
            end = position + GALLERY_BLOB_HEADER_SIZE + length
            if end > expected_bytes:
                break
            positions.append(position)
            position = end
    records: list[CacheImageRecord] = []
    for position in positions:
        if position < 4 or position + GALLERY_BLOB_HEADER_SIZE > len(data):
            continue
        _, checksum, stored, length = struct.unpack_from("<QIII", data, position)
        start = position + GALLERY_BLOB_HEADER_SIZE
        end = start + length
        if stored != position or length < 1 or length > MAX_CACHE_RECORD_BYTES or end > len(data):
            continue
        payload = data[start:end]
        if zlib.adler32(payload) & 0xFFFFFFFF != checksum:
            continue
        for image in find_images(payload):
            media_id, original_path = _cache_reference(bytes(payload[: image.offset]))
            records.append(CacheImageRecord(position, media_id, original_path, image))
    return records
