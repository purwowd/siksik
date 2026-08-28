from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

from app.acquisition.adb import AsyncAdbTransport
from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.time_scope import build_time_scope
from app.core.config import settings
from app.models.schemas import AcquisitionMode, SessionStatus

logger = logging.getLogger("siksik.acquisition.whatsapp")

WHATSAPP_PACKAGE = "com.whatsapp"
WHATSAPP_COMPONENT = "com.whatsapp/.Main"
WHATSAPP_MESSAGE_MIME = "application/vnd.satria.whatsapp-message+json"
WHATSAPP_UI_ATTEMPTS = 4
WHATSAPP_NAVIGATION_STEPS = 12
WHATSAPP_UI_DUMP = "/sdcard/window_dump.xml"
WHATSAPP_BACKUP_FIND = (
    "find /sdcard/ -name 'msgstore*.db.crypt15' -exec ls -ld {} + 2>/dev/null"
)
WHATSAPP_BACKUP_FIND_FALLBACK = (
    "find /sdcard/Android/media/ -name 'msgstore.db.crypt15' 2>/dev/null"
)
MAX_CRYPT15_BYTES = 2 * 1024 * 1024 * 1024
MAX_MESSAGE_TEXT_CHARS = 131_072
MAX_PREVIEW_CHARS = 2_000
MAX_PARTICIPANTS = 5_000
HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
REMOTE_CRYPT15_RE = re.compile(
    r"^/sdcard/(?:[^\x00\r\n/]+/)*msgstore[^/]*\.db\.crypt15$",
    re.IGNORECASE,
)


class WhatsAppUiAutomationError(RuntimeError):
    """One complete UI-automation attempt did not reach a usable backup."""


class WhatsAppNotSignedInError(Exception):
    """WhatsApp is installed but no phone number is registered yet."""


class WhatsAppParseError(RuntimeError):
    """The acquired backup could not be converted into canonical records."""


UNSIGNED_IN_RESOURCE_MARKERS = (
    "registration_phone",
    "registration_cc",
    "registration_country",
    "registration_submit",
    "registration_code",
    "eula_accept",
    "verify_sms",
    "register_phone",
    "register_name",
)
UNSIGNED_IN_TEXT_MARKERS = (
    "welcome to whatsapp",
    "selamat datang di whatsapp",
    "agree and continue",
    "setuju dan lanjutkan",
    "enter your phone number",
    "masukkan nomor telepon",
    "verify your phone number",
    "verifikasi nomor telepon",
    "waiting to automatically detect",
    "menunggu deteksi sms",
    "didn't receive a code",
    "didn't receive code",
    "didn’t receive a code",
    "didn’t receive code",
    "kode tidak diterima",
)
WHATSAPP_REGISTRATION_ACTIVITY_RE = re.compile(
    r"com\.whatsapp(?:\.w4b)?[/.].*registration(?:\.|/)",
    re.IGNORECASE,
)


def hierarchy_shows_unsigned_in(elements: list[UIElement]) -> bool:
    for element in elements:
        resource = element.resource_id.casefold()
        if any(marker in resource for marker in UNSIGNED_IN_RESOURCE_MARKERS):
            return True
        blob = f"{element.text} {element.content_desc}".casefold()
        if any(marker in blob for marker in UNSIGNED_IN_TEXT_MARKERS):
            return True
    return False


def activity_shows_unsigned_in(dump: str) -> bool:
    return bool(WHATSAPP_REGISTRATION_ACTIVITY_RE.search(dump or ""))


@dataclass(frozen=True, slots=True)
class UIElement:
    resource_id: str
    text: str
    content_desc: str
    bounds: tuple[int, int, int, int]
    clickable: bool

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)


@dataclass(frozen=True, slots=True)
class WhatsAppBackupArtifact:
    crypt15_path: Path
    hex_key: str
    ui_attempts: int


@dataclass(frozen=True, slots=True)
class WhatsAppParseSummary:
    conversation_count: int
    message_count: int
    skipped_messages: int


@dataclass(frozen=True, slots=True)
class WhatsAppAcquisitionResult:
    item_count: int
    conversation_count: int
    skipped_messages: int
    ui_attempts: int
    duration_ms: float
    state: str


Sleep = Callable[[float], Awaitable[None]]


def _clean_text(value: Any, limit: int = MAX_MESSAGE_TEXT_CHARS) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).replace("\x00", " ").split()).strip()
    return cleaned[:limit] or None


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _timestamp_iso(value: Any) -> str | None:
    timestamp = _safe_int(value)
    if timestamp <= 0:
        return None
    seconds = timestamp / 1000.0 if timestamp > 10_000_000_000 else float(timestamp)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _opaque_id(namespace: str, *values: Any, length: int = 40) -> str:
    material = "\x1f".join(str(value or "") for value in values)
    return hashlib.sha256(f"{namespace}:v1:{material}".encode("utf-8")).hexdigest()[:length]


def _secure_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_hex_key(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="ascii").strip().casefold()
    except (OSError, UnicodeError):
        return None
    return value if HEX_KEY_RE.fullmatch(value) else None


def whatsapp_key_path(serial: str) -> Path:
    device_ref = _opaque_id("whatsapp-device-key", serial, length=48)
    return settings.data_dir / "_whatsapp_keys" / f"{device_ref}.key"


class WhatsAppUiAutomator:
    """Native asynchronous equivalent of the reference UI automator."""

    def __init__(
        self,
        *,
        serial: str,
        work_dir: Path,
        transport: AsyncAdbTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.serial = serial
        self.work_dir = work_dir
        self.transport = transport or AsyncAdbTransport(
            settings.adb_path,
            timeout_seconds=settings.adb_command_timeout_s,
            output_limit_bytes=4 * 1024 * 1024,
        )
        self.sleep = sleep
        self.session_key_path = self.work_dir / "wa_64digit.key"
        self.device_key_path = whatsapp_key_path(serial)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    async def package_installed(self) -> bool:
        result = await self.transport.run(
            self.serial,
            ["shell", "pm", "path", WHATSAPP_PACKAGE],
            operation="whatsapp_package_probe",
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode == 0:
            return any(line.startswith("package:") for line in output.splitlines())
        if "unknown package" in output.casefold() or "not found" in output.casefold():
            return False
        raise acquisition_error(
            ErrorCategory.ADB_COMMAND_FAILED,
            "Pemeriksaan instalasi WhatsApp melalui ADB gagal.",
            retryable=True,
            dependency_exit_code=result.returncode,
        )

    async def tap(self, x: int, y: int, delay: float = 1.5) -> None:
        if not 0 <= x <= 20_000 or not 0 <= y <= 20_000:
            raise WhatsAppUiAutomationError("Koordinat elemen WhatsApp tidak valid")
        await self.transport.run(
            self.serial,
            ["shell", "input", "tap", str(x), str(y)],
            operation="whatsapp_ui_tap",
        )
        await self.sleep(delay)

    async def tap_element(self, element: UIElement, delay: float = 1.5) -> None:
        await self.tap(*element.center, delay=delay)

    async def dump_hierarchy(self) -> list[UIElement]:
        await self.transport.run(
            self.serial,
            ["shell", "uiautomator", "dump", "--compressed", WHATSAPP_UI_DUMP],
            operation="whatsapp_ui_dump",
            timeout=30.0,
        )
        result = await self.transport.run(
            self.serial,
            ["shell", "cat", WHATSAPP_UI_DUMP],
            operation="whatsapp_ui_read",
            check=False,
        )
        if not result.stdout or "<hierarchy" not in result.stdout:
            await self.sleep(1.0)
            result = await self.transport.run(
                self.serial,
                ["shell", "cat", WHATSAPP_UI_DUMP],
                operation="whatsapp_ui_read_retry",
                check=False,
            )
        if not result.stdout or "<hierarchy" not in result.stdout:
            raise WhatsAppUiAutomationError("Hierarchy UI WhatsApp tidak tersedia")

        try:
            root = ET.fromstring(result.stdout)
        except ET.ParseError as exc:
            raise WhatsAppUiAutomationError("Hierarchy UI WhatsApp tidak valid") from exc

        elements: list[UIElement] = []
        for node in root.iter("node"):
            raw_bounds = node.attrib.get("bounds", "[0,0][0,0]")
            matches = re.findall(r"\[(\d+),(\d+)\]", raw_bounds)
            bounds = (
                (
                    int(matches[0][0]),
                    int(matches[0][1]),
                    int(matches[1][0]),
                    int(matches[1][1]),
                )
                if len(matches) == 2
                else (0, 0, 0, 0)
            )
            elements.append(
                UIElement(
                    resource_id=node.attrib.get("resource-id", "")[:512],
                    text=node.attrib.get("text", "")[:4096],
                    content_desc=node.attrib.get("content-desc", "")[:4096],
                    bounds=bounds,
                    clickable=node.attrib.get("clickable", "false").casefold() == "true",
                )
            )
        return elements

    async def launch_whatsapp(self) -> None:
        for args, operation in (
            (["shell", "input", "keyevent", "224"], "whatsapp_device_wake"),
            (
                ["shell", "input", "swipe", "500", "1500", "500", "500", "200"],
                "whatsapp_device_swipe",
            ),
            (["shell", "input", "keyevent", "82"], "whatsapp_device_unlock"),
        ):
            await self.transport.run(
                self.serial,
                args,
                operation=operation,
                check=False,
            )
        await self.transport.run(
            self.serial,
            [
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
                "-n",
                WHATSAPP_COMPONENT,
            ],
            operation="whatsapp_launch",
        )
        await self.sleep(2.0)

    async def _activity_top_dump(self) -> str:
        try:
            result = await self.transport.run(
                self.serial,
                ["shell", "dumpsys", "activity", "top"],
                operation="whatsapp_activity_probe",
                timeout=20.0,
                check=False,
            )
        except AcquisitionError:
            return ""
        return f"{result.stdout}\n{result.stderr}"

    async def navigate_to_chat_backup(self) -> bool:
        await self.launch_whatsapp()
        if activity_shows_unsigned_in(await self._activity_top_dump()):
            raise WhatsAppNotSignedInError
        for _ in range(WHATSAPP_NAVIGATION_STEPS):
            elements = await self.dump_hierarchy()
            if hierarchy_shows_unsigned_in(elements):
                raise WhatsAppNotSignedInError
            if any(
                "google_drive_backup_now_btn" in element.resource_id
                or element.text in {"BACK UP", "CADANGKAN"}
                or "Backup settings" in element.text
                for element in elements
            ):
                return True

            backup_item = next(
                (
                    element
                    for element in elements
                    if "chat_backup_preference" in element.resource_id
                    or element.text in {"Chat backup", "Cadangan chat"}
                    or element.content_desc in {"Chat backup", "Cadangan chat"}
                ),
                None,
            )
            if backup_item and any(
                "Chat settings" in element.text
                or "Setelan chat" in element.text
                or "Enter is send" in element.text
                for element in elements
            ):
                await self.tap_element(backup_item)
                continue

            chats_item = next(
                (
                    element
                    for element in elements
                    if "settings_chat" in element.resource_id
                    or element.content_desc.startswith("Chats,")
                    or element.content_desc.startswith("Chat,")
                    or (
                        element.text in {"Chats", "Chat"}
                        and element.center[1] > 1700
                    )
                ),
                None,
            )
            if chats_item and any(
                "Account" in (element.text or element.content_desc)
                or "Akun" in (element.text or element.content_desc)
                or "Privacy" in (element.text or element.content_desc)
                for element in elements
            ):
                await self.tap_element(chats_item)
                continue

            settings_item = next(
                (
                    element
                    for element in elements
                    if (
                        element.text.startswith("Settings")
                        or element.text.startswith("Setelan")
                        or element.content_desc in {"Settings", "Setelan"}
                    )
                    and element.center[1] > 800
                ),
                None,
            )
            if settings_item and any(
                "New group" in element.text
                or "Grup baru" in element.text
                or "Linked devices" in element.text
                for element in elements
            ):
                await self.tap_element(settings_item)
                continue

            overflow = next(
                (
                    element
                    for element in elements
                    if "menuitem_overflow" in element.resource_id
                    or element.content_desc in {"More options", "Opsi lainnya"}
                ),
                None,
            )
            if overflow:
                await self.tap_element(overflow)
                continue

            await self.transport.run(
                self.serial,
                ["shell", "input", "keyevent", "4"],
                operation="whatsapp_ui_back",
                check=False,
            )
            await self.sleep(1.0)
        return False

    async def setup_or_extract_64digit_key(self) -> str | None:
        elements = await self.dump_hierarchy()
        create_button = next(
            (
                element
                for element in elements
                if "enable_done_create_button" in element.resource_id
                or element.text in {"CREATE", "BUAT"}
            ),
            None,
        )
        if create_button:
            await self.tap_element(create_button, delay=3.0)
            elements = await self.dump_hierarchy()

        encrypted_backup = next(
            (
                element
                for element in elements
                if "settings_gdrive_e2e_encryption" in element.resource_id
                or "End-to-end encrypted backup" in element.content_desc
                or "Cadangan terenkripsi end-to-end" in element.content_desc
            ),
            None,
        )
        if encrypted_backup:
            await self.tap_element(encrypted_backup, delay=2.0)
            elements = await self.dump_hierarchy()

        more_options = next(
            (
                element
                for element in elements
                if "enable_info_more_options_button" in element.resource_id
                or element.text in {"MORE OPTIONS", "OPSI LAINNYA"}
            ),
            None,
        )
        if more_options:
            await self.tap_element(more_options)
            elements = await self.dump_hierarchy()

        key_option = next(
            (
                element
                for element in elements
                if "enc_backup_more_options_encryption_key" in element.resource_id
                or element.text
                in {"64-digit encryption key", "Kunci enkripsi 64 digit"}
                or element.content_desc
                in {"64-digit encryption key", "Kunci enkripsi 64 digit"}
            ),
            None,
        )
        if key_option:
            await self.tap_element(key_option)
            elements = await self.dump_hierarchy()

        generate_button = next(
            (
                element
                for element in elements
                if element.text
                in {
                    "GENERATE YOUR 64-DIGIT KEY",
                    "Generate your 64-digit key",
                    "BUAT KUNCI 64 DIGIT ANDA",
                    "TURN ON",
                    "NYALAKAN",
                }
            ),
            None,
        )
        if generate_button:
            await self.tap_element(generate_button, delay=2.0)
            elements = await self.dump_hierarchy()

        chunks = [
            element.text.strip().casefold()
            for element in elements
            if re.fullmatch(r"[0-9a-fA-F]{4}", element.text.strip())
        ]
        if len(chunks) < 16:
            return None
        hex_key = "".join(chunks[:16])
        if not HEX_KEY_RE.fullmatch(hex_key):
            return None

        continue_button = next(
            (
                element
                for element in elements
                if "encryption_key_info_bottom_button" in element.resource_id
                or element.text in {"CONTINUE", "LANJUT"}
            ),
            None,
        )
        if continue_button:
            await self.tap_element(continue_button)
            elements = await self.dump_hierarchy()

        saved_button = next(
            (
                element
                for element in elements
                if "encryption_key_confirm_button_confirm" in element.resource_id
                or element.text
                in {
                    "I SAVED MY 64-DIGIT KEY",
                    "SAYA MENYIMPAN KUNCI 64 DIGIT SAYA",
                }
            ),
            None,
        )
        if saved_button:
            await self.tap_element(saved_button)
            elements = await self.dump_hierarchy()

        create_button = next(
            (
                element
                for element in elements
                if "enable_done_create_button" in element.resource_id
                or element.text in {"CREATE", "BUAT"}
            ),
            None,
        )
        if create_button:
            await self.tap_element(create_button, delay=4.0)
        return hex_key

    async def trigger_backup(self) -> bool:
        elements = await self.dump_hierarchy()
        backup_button = next(
            (
                element
                for element in elements
                if "google_drive_backup_now_btn" in element.resource_id
                or element.text in {"BACK UP", "CADANGKAN"}
            ),
            None,
        )
        if backup_button is None:
            return False
        await self.tap_element(backup_button, delay=4.0)
        return True

    @staticmethod
    def _remote_backup_paths(output: str) -> list[str]:
        paths: list[str] = []
        for line in output.splitlines():
            cleaned = line.strip()
            if not cleaned or "msgstore" not in cleaned.casefold():
                continue
            candidate = cleaned.split()[-1]
            if not REMOTE_CRYPT15_RE.fullmatch(candidate):
                continue
            pure = PurePosixPath(candidate)
            if ".." in pure.parts or len(candidate) > 2048:
                continue
            paths.append(candidate)
        return list(dict.fromkeys(paths))

    async def pull_newest_crypt15(self) -> Path | None:
        result = await self.transport.run(
            self.serial,
            ["shell", WHATSAPP_BACKUP_FIND],
            operation="whatsapp_backup_find",
            timeout=120.0,
            check=False,
        )
        paths = self._remote_backup_paths(result.stdout)
        if not paths:
            result = await self.transport.run(
                self.serial,
                ["shell", WHATSAPP_BACKUP_FIND_FALLBACK],
                operation="whatsapp_backup_find_fallback",
                timeout=120.0,
                check=False,
            )
            paths = self._remote_backup_paths(result.stdout)
        if not paths:
            return None

        target = paths[0]
        for candidate in paths:
            if "accounts" in candidate and candidate.endswith("/msgstore.db.crypt15"):
                target = candidate
                break
            if candidate.endswith("/msgstore.db.crypt15"):
                target = candidate

        destination = self.work_dir / "msgstore.db.crypt15"
        await self.transport.run(
            self.serial,
            ["pull", target, str(destination)],
            operation="whatsapp_backup_pull",
            timeout=900.0,
        )
        try:
            size = destination.stat().st_size
        except OSError:
            return None
        if not 0 < size <= MAX_CRYPT15_BYTES:
            return None
        return destination

    def _store_key(self, hex_key: str) -> None:
        if not HEX_KEY_RE.fullmatch(hex_key):
            raise WhatsAppUiAutomationError("Kunci Crypt15 tidak valid")
        _secure_write_text(self.session_key_path, hex_key)
        _secure_write_text(self.device_key_path, hex_key)

    def _stored_key(self) -> str | None:
        return _read_hex_key(self.session_key_path) or _read_hex_key(self.device_key_path)

    async def _single_attempt(self) -> WhatsAppBackupArtifact:
        if not await self.navigate_to_chat_backup():
            raise WhatsAppUiAutomationError("Navigasi ke Cadangan chat gagal")

        generated_key = await self.setup_or_extract_64digit_key()
        if generated_key:
            self._store_key(generated_key)
            hex_key = generated_key
        else:
            hex_key = self._stored_key()
            if hex_key is None:
                raise WhatsAppUiAutomationError("Kunci enkripsi 64 digit tidak tersedia")
            # The reference flow treats BACK UP as optional here: an existing
            # encrypted-backup settings screen may not expose the button, while
            # a current local Crypt15 backup is still valid and pullable.
            await self.trigger_backup()

        await self.sleep(3.0)
        backup = await self.pull_newest_crypt15()
        if backup is None:
            raise WhatsAppUiAutomationError("msgstore.db.crypt15 tidak ditemukan")
        return WhatsAppBackupArtifact(backup, hex_key, 1)

    async def acquire_backup(
        self,
        *,
        on_progress: Callable[..., Awaitable[None]],
    ) -> WhatsAppBackupArtifact:
        last_error: BaseException | None = None
        for attempt in range(1, WHATSAPP_UI_ATTEMPTS + 1):
            await on_progress(
                SessionStatus.ACQUIRING,
                38.0,
                f"WhatsApp UI automator · percobaan {attempt}/{WHATSAPP_UI_ATTEMPTS}",
                whatsapp_state="ui_automation",
                whatsapp_ui_attempt=attempt,
                whatsapp_ui_attempts=WHATSAPP_UI_ATTEMPTS,
            )
            try:
                artifact = await self._single_attempt()
            except asyncio.CancelledError:
                raise
            except WhatsAppNotSignedInError:
                raise
            except (AcquisitionError, OSError, ValueError, WhatsAppUiAutomationError) as exc:
                last_error = exc
                logger.warning(
                    "whatsapp_ui_attempt_failed",
                    extra={
                        "attempt": attempt,
                        "attempts": WHATSAPP_UI_ATTEMPTS,
                        "error_category": (
                            exc.category.value
                            if isinstance(exc, AcquisitionError)
                            else "ui_automation"
                        ),
                    },
                )
                if attempt < WHATSAPP_UI_ATTEMPTS:
                    await self.sleep(1.0)
                continue
            return WhatsAppBackupArtifact(
                crypt15_path=artifact.crypt15_path,
                hex_key=artifact.hex_key,
                ui_attempts=attempt,
            )

        dependency_exit_code = (
            last_error.dependency_exit_code
            if isinstance(last_error, AcquisitionError)
            else None
        )
        raise acquisition_error(
            ErrorCategory.ACCESS_TIMEOUT,
            "UI automator WhatsApp gagal setelah 4 percobaan penuh.",
            retryable=False,
            dependency_exit_code=dependency_exit_code,
        )


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if offset >= len(data):
            raise WhatsAppParseError("Protobuf Crypt15 terpotong")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise WhatsAppParseError("Varint protobuf Crypt15 tidak valid")


def _protobuf_length_fields(data: bytes) -> dict[int, list[bytes]]:
    fields: dict[int, list[bytes]] = {}
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number <= 0:
            raise WhatsAppParseError("Nomor field protobuf Crypt15 tidak valid")
        if wire_type == 0:
            _, offset = _read_varint(data, offset)
        elif wire_type == 1:
            offset += 8
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise WhatsAppParseError("Field protobuf Crypt15 terpotong")
            fields.setdefault(field_number, []).append(data[offset:end])
            offset = end
        elif wire_type == 5:
            offset += 4
        else:
            raise WhatsAppParseError("Wire type protobuf Crypt15 tidak didukung")
        if offset > len(data):
            raise WhatsAppParseError("Protobuf Crypt15 terpotong")
    return fields


def _crypt15_header(data: bytes) -> tuple[bytes, bytes, bytes]:
    if len(data) < 36:
        raise WhatsAppParseError("File Crypt15 terlalu kecil")
    protobuf_size = data[0]
    backup_type = data[1]
    header_end = 2 + protobuf_size
    if backup_type != 1 or header_end >= len(data):
        raise WhatsAppParseError("Header Crypt15 tidak didukung")
    protobuf = data[2:header_end]
    prefix_fields = _protobuf_length_fields(protobuf)
    c15_values = prefix_fields.get(3) or []
    if not c15_values:
        raise WhatsAppParseError("IV Crypt15 tidak ditemukan")
    iv_values = _protobuf_length_fields(c15_values[0]).get(1) or []
    if not iv_values or len(iv_values[0]) != 16:
        raise WhatsAppParseError("IV Crypt15 tidak valid")
    return data[:header_end], iv_values[0], data[header_end:]


def decrypt_crypt15(hex_key: str, source: Path, destination: Path) -> None:
    if not HEX_KEY_RE.fullmatch(hex_key):
        raise WhatsAppParseError("Kunci Crypt15 harus berupa 64 karakter heksadesimal")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise WhatsAppParseError("File Crypt15 tidak dapat dibaca") from exc
    if not 0 < size <= MAX_CRYPT15_BYTES:
        raise WhatsAppParseError("Ukuran file Crypt15 tidak valid")
    try:
        encrypted = source.read_bytes()
    except (OSError, MemoryError) as exc:
        raise WhatsAppParseError("File Crypt15 tidak dapat dimuat") from exc

    header, iv, body = _crypt15_header(encrypted)
    if len(body) >= 36 and re.fullmatch(rb"[-0-9]{4}", body[-4:]):
        # Some single-file backups append the final phone-number characters.
        body = body[:-4]
    if len(body) < 32:
        raise WhatsAppParseError("Payload Crypt15 terpotong")
    raw_key = bytes.fromhex(hex_key)
    extracted = hmac.new(b"\x00" * 32, raw_key, hashlib.sha256).digest()
    derived_key = hmac.new(
        extracted,
        b"backup encryption\x01",
        hashlib.sha256,
    ).digest()

    try:
        from Cryptodome.Cipher import AES
    except ImportError as exc:
        raise WhatsAppParseError("Dependensi AES Crypt15 tidak tersedia") from exc

    ciphertext = body[:-32]
    authentication_tag = body[-32:-16]
    checksum = body[-16:]
    normal_checksum = hashlib.md5(
        header + ciphertext + authentication_tag,
        usedforsecurity=False,
    ).digest()
    try:
        if hmac.compare_digest(normal_checksum, checksum):
            compressed = AES.new(derived_key, AES.MODE_GCM, iv).decrypt_and_verify(
                ciphertext,
                authentication_tag,
            )
        else:
            compressed = AES.new(derived_key, AES.MODE_GCM, iv).decrypt_and_verify(
                body[:-16],
                body[-16:],
            )
    except (ValueError, KeyError) as exc:
        raise WhatsAppParseError("Autentikasi Crypt15 gagal") from exc

    if compressed.startswith(b"PK\x03\x04"):
        raise WhatsAppParseError("Backup Crypt15 multi-file ZIP belum berisi SQLite langsung")
    try:
        plaintext = zlib.decompress(compressed)
    except zlib.error:
        plaintext = compressed
    if not plaintext.startswith(b"SQLite format 3\x00"):
        raise WhatsAppParseError("Hasil dekripsi bukan database SQLite WhatsApp")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(plaintext)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(destination)
    except OSError as exc:
        raise WhatsAppParseError("Database WhatsApp hasil dekripsi tidak dapat disimpan") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


MESSAGE_TYPE_NAMES = {
    0: "text",
    1: "image",
    2: "audio",
    3: "video",
    5: "location",
    7: "system",
    9: "file",
    20: "sticker",
}


class WhatsAppDatabaseParser:
    """Schema-tolerant parser; optional WhatsApp tables never invalidate good rows."""

    def __init__(self, database: Path, output_root: Path, cutoff_epoch_ms: int) -> None:
        self.database = database
        self.output_root = output_root
        self.cutoff_epoch_ms = cutoff_epoch_ms

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        if not re.fullmatch(r"[a-z0-9_]{1,128}", table):
            return set()
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _self_phone_user(connection: sqlite3.Connection, tables: set[str]) -> str | None:
        if "props" not in tables:
            return None
        try:
            row = connection.execute(
                "SELECT value FROM props "
                "WHERE key = 'status_ranking_frequent_group_participants'"
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row or not row[0]:
            return None
        first = str(row[0]).split(",", 1)[0]
        return first.split("@", 1)[0] if "@" in first else None

    def _chats(
        self,
        connection: sqlite3.Connection,
        tables: set[str],
    ) -> list[dict[str, Any]]:
        if not {"chat", "jid"}.issubset(tables):
            raise WhatsAppParseError("Tabel chat/jid WhatsApp tidak tersedia")
        chat_columns = self._columns(connection, "chat")
        jid_columns = self._columns(connection, "jid")
        if not {"_id", "user", "server"}.issubset(jid_columns):
            raise WhatsAppParseError("Skema JID WhatsApp tidak kompatibel")
        jid_map_columns = self._columns(connection, "jid_map") if "jid_map" in tables else set()
        jid_map = {"lid_row_id", "jid_row_id"}.issubset(jid_map_columns)
        mapped_select = "phone_jid.user" if jid_map else "NULL"
        join_map = (
            "LEFT JOIN jid_map ON chat.jid_row_id = jid_map.lid_row_id "
            "LEFT JOIN jid AS phone_jid ON jid_map.jid_row_id = phone_jid._id"
            if jid_map
            else ""
        )
        where = "WHERE COALESCE(chat.hidden, 0) = 0" if "hidden" in chat_columns else ""
        order = "ORDER BY chat.sort_timestamp DESC" if "sort_timestamp" in chat_columns else ""
        subject = "chat.subject" if "subject" in chat_columns else "NULL"
        raw_string = "main_jid.raw_string" if "raw_string" in jid_columns else "NULL"
        query = f"""
            SELECT chat._id, {subject} AS subject,
                   main_jid.user AS main_user, main_jid.server AS main_server,
                   {raw_string} AS main_raw,
                   {mapped_select} AS mapped_phone_user
            FROM chat
            LEFT JOIN jid AS main_jid ON chat.jid_row_id = main_jid._id
            {join_map}
            {where}
            {order}
        """
        self_user = self._self_phone_user(connection, tables)
        chats: list[dict[str, Any]] = []
        for row in connection.execute(query):
            server = _clean_text(row[3], 128) or ""
            main_user = _clean_text(row[2], 512) or ""
            mapped_user = _clean_text(row[5], 512)
            raw = _clean_text(row[4], 1024)
            is_group = server == "g.us"
            if is_group:
                address = raw or (f"{main_user}@{server}" if main_user else "Group Chat")
                display_name = _clean_text(row[1], 512) or "Unknown Group"
            else:
                number = mapped_user or (self_user if server == "lid" and self_user else main_user)
                address = f"+{number}" if number else (raw or "Unknown")
                display_name = "(You)" if server == "lid" and self_user else address
            chat_id = _safe_int(row[0], -1)
            if chat_id < 0:
                continue
            chats.append(
                {
                    "database_id": chat_id,
                    "id": _opaque_id("whatsapp-conversation", server, main_user, chat_id),
                    "name": display_name,
                    "address": address,
                    "type": "group" if is_group else "chat",
                    "is_group": is_group,
                }
            )
        return chats

    def _participants(
        self,
        connection: sqlite3.Connection,
        tables: set[str],
        chat_id: int,
    ) -> tuple[list[str], list[str]]:
        result: list[list[str]] = [[], []]
        for index, table in enumerate(
            ("group_participant_user", "group_past_participant_user")
        ):
            if table not in tables:
                continue
            try:
                rows = connection.execute(
                    f"""
                    SELECT jid.user
                    FROM {table} participant
                    JOIN jid ON participant.user_jid_row_id = jid._id
                    JOIN chat ON participant.group_jid_row_id = chat.jid_row_id
                    WHERE chat._id = ?
                    LIMIT ?
                    """,
                    (chat_id, MAX_PARTICIPANTS),
                ).fetchall()
            except sqlite3.Error:
                continue
            result[index] = [
                value
                for row in rows
                if (value := _clean_text(row[0], 512)) is not None
            ]
        return result[0], result[1]

    @staticmethod
    def _column(
        alias: str,
        columns: set[str],
        name: str,
        output: str | None = None,
    ) -> str:
        label = output or name
        return f"{alias}.{name} AS {label}" if name in columns else f"NULL AS {label}"

    def _message_query(
        self,
        connection: sqlite3.Connection,
        tables: set[str],
    ) -> tuple[str, list[str]]:
        source = "available_message_view" if "available_message_view" in tables else "message"
        if source not in tables:
            raise WhatsAppParseError("Tabel pesan WhatsApp tidak tersedia")
        message_columns = self._columns(connection, source)
        required = {"_id", "chat_row_id", "from_me", "timestamp", "message_type"}
        if not required.issubset(message_columns):
            raise WhatsAppParseError("Skema tabel pesan WhatsApp tidak kompatibel")

        selections = [
            self._column("msg", message_columns, "_id"),
            self._column("msg", message_columns, "key_id"),
            self._column("msg", message_columns, "text_data"),
            self._column("msg", message_columns, "from_me"),
            self._column("msg", message_columns, "timestamp"),
            self._column("msg", message_columns, "message_type"),
            self._column("msg", message_columns, "sender_jid_row_id"),
            self._column("msg", message_columns, "starred"),
        ]
        joins: list[str] = []
        field_names = [
            "_id",
            "key_id",
            "text_data",
            "from_me",
            "timestamp",
            "message_type",
            "sender_jid_row_id",
            "starred",
        ]

        jid_columns = self._columns(connection, "jid") if "jid" in tables else set()
        has_sender_join = (
            "sender_jid_row_id" in message_columns
            and {"_id", "user"}.issubset(jid_columns)
        )
        if has_sender_join:
            joins.append("LEFT JOIN jid AS sender_jid ON msg.sender_jid_row_id = sender_jid._id")
            selections.extend(
                [
                    "sender_jid.user AS sender_user",
                    self._column("sender_jid", jid_columns, "server", "sender_server"),
                    self._column("sender_jid", jid_columns, "raw_string", "sender_raw"),
                ]
            )
        else:
            selections.extend(
                [
                    "NULL AS sender_user",
                    "NULL AS sender_server",
                    "NULL AS sender_raw",
                ]
            )
        field_names.extend(["sender_user", "sender_server", "sender_raw"])

        jid_map_columns = self._columns(connection, "jid_map") if "jid_map" in tables else set()
        if (
            has_sender_join
            and {"lid_row_id", "jid_row_id"}.issubset(jid_map_columns)
        ):
            joins.extend(
                [
                    "LEFT JOIN jid_map ON sender_jid._id = jid_map.lid_row_id",
                    "LEFT JOIN jid AS sender_phone_jid "
                    "ON jid_map.jid_row_id = sender_phone_jid._id",
                ]
            )
            selections.append("sender_phone_jid.user AS sender_phone_user")
        else:
            selections.append("NULL AS sender_phone_user")
        field_names.append("sender_phone_user")

        optional_tables: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
            (
                "message_media",
                "media",
                (
                    ("file_size", "file_size"),
                    ("media_name", "media_name"),
                    ("media_caption", "media_caption"),
                    ("mime_type", "mime_type"),
                    ("file_path", "file_path"),
                    ("media_duration", "media_duration"),
                ),
            ),
            (
                "message_location",
                "location",
                (
                    ("latitude", "latitude"),
                    ("longitude", "longitude"),
                    ("place_name", "place_name"),
                    ("place_address", "place_address"),
                    ("url", "url"),
                ),
            ),
            (
                "message_revoked",
                "revoked",
                (
                    ("revoked_key_id", "revoked_key_id"),
                    ("revoke_timestamp", "revoke_timestamp"),
                    ("admin_jid_row_id", "revoked_admin_jid_row_id"),
                ),
            ),
            ("message_forwarded", "forwarded", (("forward_score", "forward_score"),)),
            (
                "message_edit_info",
                "edit_info",
                (("edited_timestamp", "edited_timestamp"),),
            ),
            (
                "message_quoted",
                "quoted",
                (
                    ("text_data", "quoted_text"),
                    ("sender_jid_row_id", "quoted_sender_jid_row_id"),
                ),
            ),
        )
        joined_optional: dict[str, set[str]] = {}
        for table, alias, columns in optional_tables:
            table_columns = self._columns(connection, table) if table in tables else set()
            if "message_row_id" in table_columns:
                joins.append(
                    f"LEFT JOIN {table} AS {alias} ON msg._id = {alias}.message_row_id"
                )
                joined_optional[table] = table_columns
                for column, output in columns:
                    selections.append(self._column(alias, table_columns, column, output))
                    field_names.append(output)
            else:
                for _column_name, output in columns:
                    selections.append(f"NULL AS {output}")
                    field_names.append(output)

        quoted_columns = joined_optional.get("message_quoted", set())
        if "sender_jid_row_id" in quoted_columns and {"_id", "user"}.issubset(jid_columns):
            joins.append("LEFT JOIN jid AS quoted_jid ON quoted.sender_jid_row_id = quoted_jid._id")
            selections.append("quoted_jid.user AS quoted_user")
        else:
            selections.append("NULL AS quoted_user")
        field_names.append("quoted_user")

        revoked_columns = joined_optional.get("message_revoked", set())
        if "admin_jid_row_id" in revoked_columns and {"_id", "user"}.issubset(jid_columns):
            joins.append(
                "LEFT JOIN jid AS revoked_admin_jid "
                "ON revoked.admin_jid_row_id = revoked_admin_jid._id"
            )
            selections.append("revoked_admin_jid.user AS revoked_admin")
        else:
            selections.append("NULL AS revoked_admin")
        field_names.append("revoked_admin")

        thumbnail_columns = (
            self._columns(connection, "message_thumbnail")
            if "message_thumbnail" in tables
            else set()
        )
        if {"message_row_id", "thumbnail"}.issubset(thumbnail_columns):
            joins.append(
                "LEFT JOIN message_thumbnail AS thumbnail "
                "ON msg._id = thumbnail.message_row_id"
            )
            selections.append(
                "CASE WHEN thumbnail.thumbnail IS NULL THEN 0 ELSE 1 END "
                "AS thumbnail_available"
            )
        else:
            selections.append("0 AS thumbnail_available")
        field_names.append("thumbnail_available")

        call_link_columns = (
            self._columns(connection, "message_call_log")
            if "message_call_log" in tables
            else set()
        )
        call_columns = self._columns(connection, "call_log") if "call_log" in tables else set()
        if (
            {"message_row_id", "call_log_row_id"}.issubset(call_link_columns)
            and "_id" in call_columns
        ):
            joins.extend(
                [
                    "LEFT JOIN message_call_log ON msg._id = message_call_log.message_row_id",
                    "LEFT JOIN call_log ON message_call_log.call_log_row_id = call_log._id",
                ]
            )
            selections.append(self._column("call_log", call_columns, "duration", "call_duration"))
        else:
            selections.append("NULL AS call_duration")
        field_names.append("call_duration")

        order_column = "sort_id" if "sort_id" in message_columns else "_id"
        query = f"""
            SELECT {', '.join(selections)}
            FROM {source} AS msg
            {' '.join(joins)}
            WHERE msg.chat_row_id = ? AND msg.timestamp >= ?
            ORDER BY msg.{order_column} ASC
        """
        return query, field_names

    @staticmethod
    def _message_record(
        chat: dict[str, Any],
        participants: list[str],
        past_participants: list[str],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        type_code = _safe_int(row.get("message_type"))
        message_type = MESSAGE_TYPE_NAMES.get(type_code, f"type_{type_code}")
        direction = "OUT" if _safe_int(row.get("from_me")) == 1 else "IN"
        sender_user = _clean_text(row.get("sender_phone_user"), 512) or _clean_text(
            row.get("sender_user"), 512
        )
        sender = f"+{sender_user}" if sender_user else _clean_text(row.get("sender_raw"), 1024)
        if direction == "OUT":
            sender = "Anda"

        text = _clean_text(row.get("text_data"))
        caption = _clean_text(row.get("media_caption"))
        location = None
        latitude = row.get("latitude")
        longitude = row.get("longitude")
        place_name = _clean_text(row.get("place_name"), 1024)
        place_address = _clean_text(row.get("place_address"), 2048)
        if latitude is not None or longitude is not None or place_name or place_address:
            location = {
                "latitude": latitude,
                "longitude": longitude,
                "place_name": place_name,
                "address": place_address,
                "url": _clean_text(row.get("url"), 2048),
            }

        media = None
        if type_code in {1, 2, 3, 9, 20} or any(
            row.get(key) not in {None, ""}
            for key in ("file_path", "mime_type", "media_name", "file_size")
        ):
            file_path = _clean_text(row.get("file_path"), 4096)
            media = {
                "filename": (
                    PurePosixPath(file_path).name
                    if file_path
                    else _clean_text(row.get("media_name"), 1024)
                ),
                "file_size": max(0, _safe_int(row.get("file_size"))),
                "mime_type": _clean_text(row.get("mime_type"), 255),
                "caption": caption,
                "source_path": file_path,
                "duration": max(0, _safe_int(row.get("media_duration"))),
            }

        quoted_text = _clean_text(row.get("quoted_text"))
        quote = (
            {
                "sender": _clean_text(row.get("quoted_user"), 512),
                "text": quoted_text,
            }
            if quoted_text or row.get("quoted_user")
            else None
        )
        revoked = bool(row.get("revoked_key_id"))
        timestamp = _timestamp_iso(row.get("timestamp"))
        key_id = _clean_text(row.get("key_id"), 512)
        message_id = _opaque_id(
            "whatsapp-message",
            chat["id"],
            key_id or row.get("_id"),
            row.get("timestamp"),
        )

        primary_text = text or caption
        if message_type == "location" and not primary_text:
            primary_text = "Lokasi dibagikan"
        elif message_type == "system" and not primary_text:
            primary_text = "Pesan sistem"
        elif revoked and not primary_text:
            primary_text = "Pesan dicabut"
        elif not primary_text:
            primary_text = f"[{message_type}]"

        location_text = " ".join(
            value
            for value in (
                place_name,
                place_address,
                str(latitude) if latitude is not None else None,
                str(longitude) if longitude is not None else None,
            )
            if value
        )
        normalized_text = "\n".join(
            dict.fromkeys(
                value
                for value in (
                    chat["name"],
                    chat["address"],
                    sender,
                    text,
                    caption,
                    quoted_text,
                    location_text or None,
                )
                if value
            )
        )[:MAX_MESSAGE_TEXT_CHARS]
        preview = _clean_text(primary_text, MAX_PREVIEW_CHARS) or f"[{message_type}]"
        return {
            "schema_version": 1,
            "kind": "whatsapp_message",
            "source": "whatsapp",
            "record_id": message_id,
            "album": "WhatsApp",
            "display_name": chat["name"],
            "preview_text": preview,
            "normalized_text": normalized_text,
            "captured_at": timestamp,
            "source_created_at": timestamp,
            "conversation": {
                "id": chat["id"],
                "name": chat["name"],
                "address": chat["address"],
                "type": chat["type"],
                "participants": participants,
                "past_participants": past_participants,
            },
            "message": {
                "id": message_id,
                "key_id": key_id,
                "direction": direction,
                "sender": sender,
                "type": message_type,
                "type_code": type_code,
                "text": text,
                "timestamp": timestamp,
                "starred": bool(_safe_int(row.get("starred"))),
                "revoked": revoked,
                "revoked_at": _timestamp_iso(row.get("revoke_timestamp")),
                "revoked_by": _clean_text(row.get("revoked_admin"), 512),
                "forward_score": max(0, _safe_int(row.get("forward_score"))),
                "edited_at": _timestamp_iso(row.get("edited_timestamp")),
                "call_duration": max(0, _safe_int(row.get("call_duration"))),
                "thumbnail_available": bool(
                    _safe_int(row.get("thumbnail_available"))
                ),
                "quote": quote,
                "location": location,
                "media": media,
            },
        }

    def export(self) -> WhatsAppParseSummary:
        uri = f"file:{self.database.resolve().as_posix()}?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise WhatsAppParseError("Database WhatsApp tidak dapat dibuka") from exc
        connection.row_factory = sqlite3.Row
        skipped = 0
        written = 0
        try:
            connection.execute("PRAGMA query_only = ON")
            tables = self._table_names(connection)
            chats = self._chats(connection, tables)
            query, field_names = self._message_query(connection, tables)
            self.output_root.mkdir(parents=True, exist_ok=True)
            for chat in chats:
                participants, past_participants = (
                    self._participants(connection, tables, chat["database_id"])
                    if chat["is_group"]
                    else ([], [])
                )
                try:
                    rows = connection.execute(
                        query,
                        (chat["database_id"], self.cutoff_epoch_ms),
                    )
                except sqlite3.Error:
                    skipped += 1
                    continue
                room = self.output_root / chat["id"]
                for sqlite_row in rows:
                    try:
                        values = {
                            field_names[index]: sqlite_row[index]
                            for index in range(min(len(field_names), len(sqlite_row)))
                        }
                        record = self._message_record(
                            chat,
                            participants,
                            past_participants,
                            values,
                        )
                        room.mkdir(parents=True, exist_ok=True)
                        record_path = room / f"{record['record_id']}.whatsapp-message.json"
                        record_path.write_text(
                            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                            encoding="utf-8",
                        )
                        written += 1
                    except (OSError, TypeError, ValueError, OverflowError):
                        skipped += 1
                        continue
        except sqlite3.Error as exc:
            raise WhatsAppParseError("Skema database WhatsApp tidak dapat dibaca") from exc
        finally:
            connection.close()
        return WhatsAppParseSummary(
            conversation_count=len(chats),
            message_count=written,
            skipped_messages=skipped,
        )


class WhatsAppBackupAcquisitionService:
    def __init__(self, transport: AsyncAdbTransport | None = None) -> None:
        self.transport = transport

    async def acquire(
        self,
        *,
        serial: str,
        staging: Path,
        mode: AcquisitionMode,
        on_progress: Callable[..., Awaitable[None]],
        reference: datetime | None = None,
    ) -> WhatsAppAcquisitionResult | None:
        started = time.perf_counter()
        internal = staging / "_whatsapp"
        automator = WhatsAppUiAutomator(
            serial=serial,
            work_dir=internal,
            transport=self.transport,
        )
        if not await automator.package_installed():
            await on_progress(
                SessionStatus.ACQUIRING,
                38.0,
                "WhatsApp tidak terpasang; akuisisi chat tidak diperlukan",
                whatsapp_state="not_installed",
                whatsapp_ui_attempt=0,
                whatsapp_ui_attempts=WHATSAPP_UI_ATTEMPTS,
            )
            return None

        try:
            artifact = await automator.acquire_backup(on_progress=on_progress)
        except WhatsAppNotSignedInError:
            logger.warning(
                "whatsapp_not_signed_in",
                extra={"error_category": "not_signed_in", "mode": mode.value},
            )
            await on_progress(
                SessionStatus.ACQUIRING,
                38.0,
                "WhatsApp belum login nomor; akuisisi chat dilewati",
                whatsapp_state="not_signed_in",
                whatsapp_ui_attempt=1,
                whatsapp_ui_attempts=WHATSAPP_UI_ATTEMPTS,
            )
            return None
        await on_progress(
            SessionStatus.ACQUIRING,
            41.0,
            "Backup WhatsApp diperoleh · decrypt dan parsing pesan",
            whatsapp_state="parsing",
            whatsapp_ui_attempt=artifact.ui_attempts,
            whatsapp_ui_attempts=WHATSAPP_UI_ATTEMPTS,
        )
        decrypted = internal / "msgstore_decrypted.db"
        output = staging / "whatsapp"
        scope = build_time_scope(mode, reference=reference)
        try:
            await asyncio.to_thread(
                decrypt_crypt15,
                artifact.hex_key,
                artifact.crypt15_path,
                decrypted,
            )
            summary = await asyncio.to_thread(
                WhatsAppDatabaseParser(
                    decrypted,
                    output,
                    scope.not_before_epoch_ms,
                ).export
            )
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, WhatsAppParseError) as exc:
            logger.warning(
                "whatsapp_parse_unavailable",
                extra={"error_category": "parse", "mode": mode.value},
            )
            await on_progress(
                SessionStatus.ACQUIRING,
                43.0,
                "Backup WhatsApp diperoleh, tetapi parser tidak dapat membaca format database",
                whatsapp_state="parse_unavailable",
                whatsapp_ui_attempt=artifact.ui_attempts,
                whatsapp_ui_attempts=WHATSAPP_UI_ATTEMPTS,
                whatsapp_messages=0,
                whatsapp_conversations=0,
                whatsapp_parse_skipped=0,
            )
            return WhatsAppAcquisitionResult(
                item_count=0,
                conversation_count=0,
                skipped_messages=0,
                ui_attempts=artifact.ui_attempts,
                duration_ms=(time.perf_counter() - started) * 1000,
                state="parse_unavailable",
            )

        await on_progress(
            SessionStatus.ACQUIRING,
            43.0,
            (
                f"WhatsApp · {summary.message_count} pesan dari "
                f"{summary.conversation_count} percakapan"
            ),
            whatsapp_state="complete",
            whatsapp_ui_attempt=artifact.ui_attempts,
            whatsapp_ui_attempts=WHATSAPP_UI_ATTEMPTS,
            whatsapp_messages=summary.message_count,
            whatsapp_conversations=summary.conversation_count,
            whatsapp_parse_skipped=summary.skipped_messages,
        )
        return WhatsAppAcquisitionResult(
            item_count=summary.message_count,
            conversation_count=summary.conversation_count,
            skipped_messages=summary.skipped_messages,
            ui_attempts=artifact.ui_attempts,
            duration_ms=(time.perf_counter() - started) * 1000,
            state="complete",
        )
