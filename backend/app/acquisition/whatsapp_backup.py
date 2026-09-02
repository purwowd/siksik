from __future__ import annotations

import asyncio
import base64
import binascii
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
from enum import Enum
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
WHATSAPP_NAVIGATION_STEPS = 16
WHATSAPP_NAVIGATION_RELAUNCH_STEPS = frozenset({6, 11})
WHATSAPP_UI_DUMP_ATTEMPTS = 2
WHATSAPP_UI_DUMP_TIMEOUT_S = 20.0
WHATSAPP_UI_READ_TIMEOUT_S = 10.0
WHATSAPP_BACKUP_IDLE_POLLS = 8
WHATSAPP_BACKUP_FIND_POLLS = 10
WHATSAPP_BACKUP_FIND_POLL_S = 2.0
WHATSAPP_UI_DUMP = "/sdcard/window_dump.xml"
WHATSAPP_BACKUP_FIND = (
    "find /sdcard/Android/media/com.whatsapp/ /sdcard/WhatsApp/ "
    "/sdcard/Android/media/com.whatsapp.w4b/ -name 'msgstore*.db.crypt15' "
    "-exec ls -lt {} + 2>/dev/null"
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


class WhatsAppLayout(str, Enum):
    UNKNOWN = "unknown"
    PROFILE_TAB = "profile_tab"
    OVERFLOW_MENU = "overflow_menu"


class WhatsAppE2eState(str, Enum):
    UNKNOWN = "unknown"
    DISABLED = "disabled"
    ENABLED = "enabled"


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


def _normalized_ui_value(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _ui_labels(element: UIElement) -> tuple[str, str]:
    return (
        _normalized_ui_value(element.text),
        _normalized_ui_value(element.content_desc),
    )


def _ui_blob(element: UIElement) -> str:
    return " ".join(value for value in _ui_labels(element) if value)


def _resource_contains(element: UIElement, *markers: str) -> bool:
    resource_id = element.resource_id.casefold()
    return any(marker.casefold() in resource_id for marker in markers)


def _label_is(element: UIElement, *labels: str) -> bool:
    expected = {_normalized_ui_value(label) for label in labels}
    return any(value in expected for value in _ui_labels(element) if value)


def _label_contains(element: UIElement, *phrases: str) -> bool:
    expected = tuple(_normalized_ui_value(phrase) for phrase in phrases)
    return any(
        phrase in value
        for value in _ui_labels(element)
        for phrase in expected
        if phrase
    )


def _hierarchy_dimensions(elements: list[UIElement]) -> tuple[int, int]:
    width = max((element.bounds[2] for element in elements), default=0)
    height = max((element.bounds[3] for element in elements), default=0)
    return (width if width > 0 else 1080, height if height > 0 else 2400)


def _visible_in_viewport(
    element: UIElement,
    elements: list[UIElement],
    *,
    bottom_margin: int = 100,
) -> bool:
    width, height = _hierarchy_dimensions(elements)
    x, y = element.center
    return 0 < x < width and 0 < y < max(height - bottom_margin, 1)


def _find_profile_tab(elements: list[UIElement]) -> UIElement | None:
    width, height = _hierarchy_dimensions(elements)
    excluded = (
        "calls",
        "panggilan",
        "communities",
        "komunitas",
        "updates",
        "pembaruan",
    )
    profile_labels = ("anda", "you", "profil", "profile", "me")
    for element in elements:
        x, y = element.center
        if y <= height * 0.80 or x <= width * 0.75:
            continue
        labels = tuple(value for value in _ui_labels(element) if value)
        if not labels or any(marker in value for value in labels for marker in excluded):
            continue
        if any(
            value == label or value.startswith(f"{label},")
            for value in labels
            for label in profile_labels
        ):
            return element
    return None


def _find_overflow_button(elements: list[UIElement]) -> UIElement | None:
    return next(
        (
            element
            for element in elements
            if _resource_contains(element, "menuitem_overflow")
            or _label_is(element, "More options", "Opsi lainnya")
        ),
        None,
    )


def detect_whatsapp_layout(elements: list[UIElement]) -> WhatsAppLayout:
    if _find_profile_tab(elements) is not None:
        return WhatsAppLayout.PROFILE_TAB
    if _find_overflow_button(elements) is not None:
        return WhatsAppLayout.OVERFLOW_MENU
    return WhatsAppLayout.UNKNOWN


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
        self.layout_mode = WhatsAppLayout.UNKNOWN
        self._last_stage = "idle"
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
        last_transport_error: AcquisitionError | None = None
        last_parse_error: ET.ParseError | None = None
        for local_attempt in range(1, WHATSAPP_UI_DUMP_ATTEMPTS + 1):
            try:
                dumped = await self.transport.run(
                    self.serial,
                    [
                        "shell",
                        "uiautomator",
                        "dump",
                        "--compressed",
                        WHATSAPP_UI_DUMP,
                    ],
                    operation="whatsapp_ui_dump",
                    timeout=WHATSAPP_UI_DUMP_TIMEOUT_S,
                    check=False,
                )
                if dumped.returncode != 0:
                    if local_attempt < WHATSAPP_UI_DUMP_ATTEMPTS:
                        await self.sleep(0.5)
                        continue
                    break
                result = await self.transport.run(
                    self.serial,
                    ["shell", "cat", WHATSAPP_UI_DUMP],
                    operation="whatsapp_ui_read",
                    timeout=WHATSAPP_UI_READ_TIMEOUT_S,
                    check=False,
                )
            except asyncio.CancelledError:
                raise
            except AcquisitionError as exc:
                last_transport_error = exc
                if local_attempt < WHATSAPP_UI_DUMP_ATTEMPTS:
                    await self.sleep(0.5)
                    continue
                break

            if result.returncode != 0 or "<hierarchy" not in result.stdout:
                if local_attempt < WHATSAPP_UI_DUMP_ATTEMPTS:
                    await self.sleep(0.5)
                    continue
                break
            try:
                root = ET.fromstring(result.stdout)
            except ET.ParseError as exc:
                last_parse_error = exc
                if local_attempt < WHATSAPP_UI_DUMP_ATTEMPTS:
                    await self.sleep(0.5)
                    continue
                break

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
                        clickable=(
                            node.attrib.get("clickable", "false").casefold() == "true"
                        ),
                    )
                )
            return elements

        if last_transport_error is not None:
            raise last_transport_error
        if last_parse_error is not None:
            raise WhatsAppUiAutomationError(
                "Hierarchy UI WhatsApp tidak valid"
            ) from last_parse_error
        raise WhatsAppUiAutomationError("Hierarchy UI WhatsApp tidak tersedia")

    async def press_back(self, *, delay: float = 1.0) -> None:
        await self.transport.run(
            self.serial,
            ["shell", "input", "keyevent", "4"],
            operation="whatsapp_ui_back",
            check=False,
        )
        await self.sleep(delay)

    async def swipe_up(self, elements: list[UIElement]) -> None:
        width, height = _hierarchy_dimensions(elements)
        x = width // 2
        await self.transport.run(
            self.serial,
            [
                "shell",
                "input",
                "swipe",
                str(x),
                str(int(height * 0.72)),
                str(x),
                str(int(height * 0.32)),
                "350",
            ],
            operation="whatsapp_ui_scroll",
        )
        await self.sleep(1.2)

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

    @staticmethod
    def _is_chat_backup_screen(elements: list[UIElement]) -> bool:
        return any(
            _resource_contains(
                element,
                "backup_settings_header_view",
                "google_drive_backup_now_btn",
                "settings_gdrive_e2e_encryption",
            )
            or _label_is(
                element,
                "Backup settings",
                "Pengaturan Cadangan",
                "BACK UP",
                "CADANGKAN",
                "Back up",
                "Cadangkan",
            )
            for element in elements
        )

    @staticmethod
    def _find_chat_backup_item(elements: list[UIElement]) -> UIElement | None:
        return next(
            (
                element
                for element in elements
                if _resource_contains(element, "chat_backup_preference")
                or _label_contains(
                    element,
                    "Cadangan obrolan",
                    "Cadangan chat",
                    "Chat backup",
                    "Chat-backup",
                )
            ),
            None,
        )

    @staticmethod
    def _is_chats_settings_screen(elements: list[UIElement]) -> bool:
        return any(
            _resource_contains(
                element,
                "enter_key_preference",
                "media_visibility_preference",
                "chat_backup_preference",
            )
            or _label_contains(
                element,
                "Pengaturan obrolan",
                "Chat settings",
                "Setelan chat",
                "Enter untuk mengirim",
                "Enter is send",
                "Visibilitas media",
                "Media visibility",
                "Arsip obrolan",
                "Archived chats",
            )
            for element in elements
        )

    @staticmethod
    def _is_main_chat_screen(elements: list[UIElement]) -> bool:
        return any(
            _resource_contains(
                element,
                "conversations_row_contact_name",
                "conversations_row_date",
            )
            or element.resource_id
            in {
                "com.whatsapp:id/fab",
                "com.whatsapp:id/extended_mini_fab",
            }
            for element in elements
        )

    @staticmethod
    def _is_settings_or_profile_screen(
        elements: list[UIElement],
        *,
        is_main_screen: bool,
    ) -> bool:
        if is_main_screen:
            return False
        return any(
            _resource_contains(
                element,
                "me_tab_root_layout",
                "me_tab_profile_info",
                "settings_nested_scroll_view",
                "settings_account_info",
                "privacy_preference",
                "settings_chat",
            )
            for element in elements
        )

    @staticmethod
    def _find_chats_item(elements: list[UIElement]) -> UIElement | None:
        _width, height = _hierarchy_dimensions(elements)
        return next(
            (
                element
                for element in elements
                if _resource_contains(element, "settings_chat")
                or (
                    element.resource_id == "com.whatsapp:id/row_text"
                    and _label_is(element, "Chats", "Chat", "Obrolan")
                )
                or (
                    element.center[1] < height * 0.88
                    and not _resource_contains(element, "navigation_bar")
                    and any(
                        label.startswith(prefix)
                        for label in _ui_labels(element)
                        if label
                        for prefix in ("chats,", "chat,", "obrolan,")
                    )
                )
            ),
            None,
        )

    @staticmethod
    def _is_overflow_open(elements: list[UIElement]) -> bool:
        return any(
            _label_contains(
                element,
                "Grup baru",
                "New group",
                "Komunitas baru",
                "New community",
                "Daftar siaran",
                "Broadcast lists",
                "New broadcast",
            )
            for element in elements
        )

    @staticmethod
    def _find_settings_item(elements: list[UIElement]) -> UIElement | None:
        return next(
            (
                element
                for element in elements
                if _label_is(element, "Settings", "Setelan")
                and element.center[1] > 200
            ),
            None,
        )

    async def _dismiss_blocking_dialog(self, elements: list[UIElement]) -> bool:
        has_dialog = any(
            _resource_contains(element, "alerttitle")
            or element.resource_id == "android:id/message"
            for element in elements
        )
        if not has_dialog:
            return False

        dialog_text = " ".join(_ui_blob(element) for element in elements)
        cancellation_warning = any(
            marker in dialog_text
            for marker in ("akan dihapus", "dihapus", "will be deleted")
        )
        if cancellation_warning:
            negative = next(
                (
                    element
                    for element in elements
                    if element.resource_id == "android:id/button2"
                    or _label_is(element, "Kembali", "Back", "Batal", "Cancel")
                ),
                None,
            )
            if negative is not None:
                await self.tap_element(negative)
            else:
                await self.press_back()
            return True

        wait_dialog = any(
            marker in dialog_text
            for marker in (
                "tunggu sampai",
                "selesai sebelum",
                "wait until",
                "finish before",
            )
        )
        positive = next(
            (
                element
                for element in elements
                if _label_is(element, "OKE", "OK", "Ok")
                or (wait_dialog and element.resource_id == "android:id/button1")
            ),
            None,
        )
        if positive is not None:
            await self.tap_element(positive)
        else:
            await self.press_back()
        return True

    @staticmethod
    def _backup_in_progress(elements: list[UIElement]) -> bool:
        return any(
            _resource_contains(element, "cancel_download", "google_drive_progress")
            or _label_contains(
                element,
                "Mempersiapkan",
                "Preparing",
                "Mencadangkan",
                "Backing up",
                "Pencadangan sedang",
                "Backup in progress",
            )
            for element in elements
        )

    @staticmethod
    def _find_encrypted_backup_item(
        elements: list[UIElement],
    ) -> UIElement | None:
        return next(
            (
                element
                for element in elements
                if _resource_contains(element, "settings_gdrive_e2e_encryption")
                or _label_contains(
                    element,
                    "End-to-end encrypted backup",
                    "Cadangan terenkripsi end-to-end",
                )
            ),
            None,
        )

    @staticmethod
    def _encrypted_backup_state(
        elements: list[UIElement],
        encrypted_backup: UIElement,
    ) -> WhatsAppE2eState:
        _width, height = _hierarchy_dimensions(elements)
        nearby_distance = max(80, int(height * 0.04))
        nearby = " ".join(
            _ui_blob(element)
            for element in elements
            if abs(element.center[1] - encrypted_backup.center[1]) <= nearby_distance
        )
        combined = f"{_ui_blob(encrypted_backup)} {nearby}"
        if any(marker in combined for marker in ("nonaktif", "disabled")):
            return WhatsAppE2eState.DISABLED
        if re.search(r"\boff\b", combined):
            return WhatsAppE2eState.DISABLED
        if any(marker in combined for marker in ("nyala", "enabled")):
            return WhatsAppE2eState.ENABLED
        if re.search(r"\b(on|aktif)\b", combined):
            return WhatsAppE2eState.ENABLED
        return WhatsAppE2eState.UNKNOWN

    @staticmethod
    def _find_button(
        elements: list[UIElement],
        *,
        resource_markers: tuple[str, ...],
        labels: tuple[str, ...],
    ) -> UIElement | None:
        return next(
            (
                element
                for element in elements
                if _resource_contains(element, *resource_markers)
                or _label_is(element, *labels)
            ),
            None,
        )

    async def navigate_to_chat_backup(self) -> bool:
        self._last_stage = "navigation_launch"
        self.layout_mode = WhatsAppLayout.UNKNOWN
        await self.launch_whatsapp()
        if activity_shows_unsigned_in(await self._activity_top_dump()):
            raise WhatsAppNotSignedInError

        for step in range(1, WHATSAPP_NAVIGATION_STEPS + 1):
            self._last_stage = "navigation_probe"
            elements = await self.dump_hierarchy()
            if hierarchy_shows_unsigned_in(elements):
                raise WhatsAppNotSignedInError

            if await self._dismiss_blocking_dialog(elements):
                continue

            if self._is_chat_backup_screen(elements):
                self._last_stage = "chat_backup_ready"
                return True

            backup_item = self._find_chat_backup_item(elements)
            if self._is_chats_settings_screen(elements) or backup_item is not None:
                if backup_item is not None and _visible_in_viewport(
                    backup_item,
                    elements,
                ):
                    self._last_stage = "open_chat_backup"
                    await self.tap_element(backup_item, delay=2.0)
                else:
                    self._last_stage = "scroll_chat_settings"
                    await self.swipe_up(elements)
                continue

            is_main_screen = self._is_main_chat_screen(elements)
            if self._is_settings_or_profile_screen(
                elements,
                is_main_screen=is_main_screen,
            ):
                chats_item = self._find_chats_item(elements)
                if chats_item is not None and _visible_in_viewport(
                    chats_item,
                    elements,
                    bottom_margin=120,
                ):
                    self._last_stage = "open_chat_settings"
                    await self.tap_element(chats_item, delay=2.0)
                else:
                    self._last_stage = "scroll_settings"
                    await self.swipe_up(elements)
                continue

            if self._is_overflow_open(elements):
                settings_item = self._find_settings_item(elements)
                if settings_item is not None:
                    self._last_stage = "open_settings"
                    await self.tap_element(settings_item, delay=2.0)
                else:
                    self._last_stage = "close_unusable_overflow"
                    await self.press_back()
                continue

            has_navigation_marker = any(
                _resource_contains(element, "bottom_nav", "menuitem_overflow")
                for element in elements
            )
            if is_main_screen or has_navigation_marker:
                layout = detect_whatsapp_layout(elements)
                if layout is WhatsAppLayout.PROFILE_TAB:
                    profile_tab = _find_profile_tab(elements)
                    if profile_tab is not None:
                        self.layout_mode = layout
                        self._last_stage = "open_profile_tab"
                        logger.info(
                            "whatsapp_ui_layout_detected",
                            extra={"layout": layout.value, "navigation_step": step},
                        )
                        await self.tap_element(profile_tab, delay=2.0)
                        continue

                overflow = _find_overflow_button(elements)
                if overflow is not None:
                    self.layout_mode = WhatsAppLayout.OVERFLOW_MENU
                    self._last_stage = "open_overflow_menu"
                    logger.info(
                        "whatsapp_ui_layout_detected",
                        extra={
                            "layout": WhatsAppLayout.OVERFLOW_MENU.value,
                            "navigation_step": step,
                        },
                    )
                    await self.tap_element(overflow)
                    continue

            self._last_stage = "navigation_recovery_back"
            await self.press_back(delay=1.2)
            if step in WHATSAPP_NAVIGATION_RELAUNCH_STEPS:
                self._last_stage = "navigation_relaunch"
                await self.launch_whatsapp()
        return False

    async def _wait_for_backup_idle(
        self,
        elements: list[UIElement],
    ) -> list[UIElement]:
        current = elements
        for poll in range(WHATSAPP_BACKUP_IDLE_POLLS):
            if await self._dismiss_blocking_dialog(current):
                current = await self.dump_hierarchy()
                continue
            if not self._backup_in_progress(current):
                return current
            if poll < WHATSAPP_BACKUP_IDLE_POLLS - 1:
                await self.sleep(1.0)
                current = await self.dump_hierarchy()
        return current

    async def disable_existing_e2e_encryption(
        self,
        encrypted_backup: UIElement | None = None,
        *,
        landing_open: bool = False,
    ) -> None:
        self._last_stage = "disable_e2e_open"
        if not landing_open:
            if encrypted_backup is None:
                elements = await self.dump_hierarchy()
                encrypted_backup = self._find_encrypted_backup_item(elements)
            if encrypted_backup is None:
                raise WhatsAppUiAutomationError(
                    "Pengaturan cadangan terenkripsi tidak ditemukan"
                )
            await self.tap_element(encrypted_backup, delay=2.0)

        elements = await self.dump_hierarchy()
        if await self._dismiss_blocking_dialog(elements):
            raise WhatsAppUiAutomationError(
                "Cadangan WhatsApp masih berjalan saat enkripsi akan diubah"
            )

        self._last_stage = "disable_e2e_landing"
        disable_button = self._find_button(
            elements,
            resource_markers=("enc_backup_enabled_landing_disable_button",),
            labels=("MATIKAN", "TURN OFF", "DISABLE"),
        )
        if disable_button is None:
            raise WhatsAppUiAutomationError(
                "Tombol untuk mematikan enkripsi cadangan tidak ditemukan"
            )
        await self.tap_element(disable_button, delay=2.0)

        self._last_stage = "disable_e2e_key_recovery"
        elements = await self.dump_hierarchy()
        forgot_button = self._find_button(
            elements,
            resource_markers=("enc_backup_encryption_key_input_forgot",),
            labels=(
                "Saya kehilangan kunci enkripsi",
                "Kehilangan kunci enkripsi",
                "I lost my encryption key",
                "Lost my encryption key",
                "I forgot my encryption key",
                "Forgot my encryption key",
            ),
        )
        if forgot_button is None:
            raise WhatsAppUiAutomationError(
                "Opsi pemulihan kunci enkripsi tidak ditemukan"
            )
        await self.tap_element(forgot_button, delay=2.0)

        self._last_stage = "disable_e2e_confirm"
        elements = await self.dump_hierarchy()
        confirm_button = self._find_button(
            elements,
            resource_markers=("confirm_disable_disable_button",),
            labels=("MATIKAN", "TURN OFF", "DISABLE"),
        )
        if confirm_button is None:
            raise WhatsAppUiAutomationError(
                "Konfirmasi mematikan enkripsi cadangan tidak ditemukan"
            )
        await self.tap_element(confirm_button, delay=3.0)

        self._last_stage = "disable_e2e_finish"
        elements = await self.dump_hierarchy()
        done_button = self._find_button(
            elements,
            resource_markers=("disable_done_done_button",),
            labels=("SELESAI", "DONE"),
        )
        if done_button is not None:
            await self.tap_element(done_button, delay=2.0)

        self._last_stage = "disable_e2e_verify"
        for verification in range(5):
            elements = await self.dump_hierarchy()
            encrypted_backup = self._find_encrypted_backup_item(elements)
            if (
                encrypted_backup is not None
                and self._encrypted_backup_state(elements, encrypted_backup)
                is WhatsAppE2eState.DISABLED
            ):
                return
            if (
                self._is_chat_backup_screen(elements)
                and encrypted_backup is None
                and verification == 0
            ):
                await self.swipe_up(elements)
                continue
            if verification < 4:
                await self.sleep(1.0)
        raise WhatsAppUiAutomationError(
            "Status nonaktif enkripsi cadangan tidak dapat diverifikasi"
        )

    async def _complete_key_setup(self, elements: list[UIElement]) -> None:
        current = elements
        for transition in range(8):
            if await self._dismiss_blocking_dialog(current):
                current = await self.dump_hierarchy()
                continue
            if self._is_chat_backup_screen(current):
                return

            continue_button = self._find_button(
                current,
                resource_markers=("encryption_key_info_bottom_button",),
                labels=("CONTINUE", "LANJUT"),
            )
            if continue_button is not None:
                self._last_stage = "confirm_key_continue"
                await self.tap_element(continue_button)
                current = await self.dump_hierarchy()
                continue

            saved_button = self._find_button(
                current,
                resource_markers=("encryption_key_confirm_button_confirm",),
                labels=(
                    "I SAVED MY 64-DIGIT KEY",
                    "I HAVE SAVED MY 64-DIGIT ENCRYPTION KEY",
                    "SAYA MENYIMPAN KUNCI 64 DIGIT SAYA",
                    "SAYA SUDAH MENYIMPAN KUNCI ENKRIPSI 64 DIGIT",
                ),
            )
            if saved_button is not None:
                self._last_stage = "confirm_key_saved"
                await self.tap_element(saved_button)
                current = await self.dump_hierarchy()
                continue

            create_button = self._find_button(
                current,
                resource_markers=("enable_done_create_button",),
                labels=("CREATE", "BUAT"),
            )
            if create_button is not None:
                self._last_stage = "confirm_key_create"
                await self.tap_element(create_button, delay=4.0)
                current = await self.dump_hierarchy()
                continue

            if transition < 7:
                await self.sleep(0.5)
                current = await self.dump_hierarchy()
        raise WhatsAppUiAutomationError(
            "Konfirmasi pembuatan cadangan terenkripsi tidak selesai"
        )

    async def setup_or_extract_64digit_key(self) -> str | None:
        self._last_stage = "e2e_settings_probe"
        elements = await self.dump_hierarchy()
        elements = await self._wait_for_backup_idle(elements)
        if self._backup_in_progress(elements):
            raise WhatsAppUiAutomationError(
                "Cadangan WhatsApp masih berjalan setelah penantian terbatas"
            )

        encrypted_backup = self._find_encrypted_backup_item(elements)
        if encrypted_backup is None:
            self._last_stage = "e2e_settings_scroll"
            await self.swipe_up(elements)
            elements = await self.dump_hierarchy()
            encrypted_backup = self._find_encrypted_backup_item(elements)
        if encrypted_backup is None:
            raise WhatsAppUiAutomationError(
                "Pengaturan cadangan terenkripsi tidak ditemukan"
            )

        state = self._encrypted_backup_state(elements, encrypted_backup)
        if state is WhatsAppE2eState.ENABLED:
            await self.disable_existing_e2e_encryption(encrypted_backup)
            elements = await self.dump_hierarchy()
            encrypted_backup = self._find_encrypted_backup_item(elements)
            if encrypted_backup is None:
                await self.swipe_up(elements)
                elements = await self.dump_hierarchy()
                encrypted_backup = self._find_encrypted_backup_item(elements)
            if encrypted_backup is None:
                raise WhatsAppUiAutomationError(
                    "Pengaturan enkripsi tidak ditemukan setelah dinonaktifkan"
                )

        self._last_stage = "e2e_settings_open"
        await self.tap_element(encrypted_backup, delay=2.0)
        elements = await self.dump_hierarchy()
        if await self._dismiss_blocking_dialog(elements):
            raise WhatsAppUiAutomationError(
                "Pengaturan enkripsi tertahan oleh proses cadangan"
            )

        enabled_landing = self._find_button(
            elements,
            resource_markers=("enc_backup_enabled_landing_disable_button",),
            labels=("MATIKAN", "TURN OFF", "DISABLE"),
        )
        if enabled_landing is not None:
            await self.disable_existing_e2e_encryption(landing_open=True)
            elements = await self.dump_hierarchy()
            encrypted_backup = self._find_encrypted_backup_item(elements)
            if encrypted_backup is None:
                await self.swipe_up(elements)
                elements = await self.dump_hierarchy()
                encrypted_backup = self._find_encrypted_backup_item(elements)
            if encrypted_backup is None:
                raise WhatsAppUiAutomationError(
                    "Pengaturan enkripsi tidak ditemukan setelah recovery"
                )
            await self.tap_element(encrypted_backup, delay=2.0)
            elements = await self.dump_hierarchy()

        self._last_stage = "e2e_more_options"
        more_options = self._find_button(
            elements,
            resource_markers=("enable_info_more_options_button",),
            labels=("MORE OPTIONS", "OPSI LAINNYA"),
        )
        if more_options is not None:
            await self.tap_element(more_options)
            elements = await self.dump_hierarchy()
        else:
            turn_on = self._find_button(
                elements,
                resource_markers=("enable_info_turn_on_button",),
                labels=("TURN ON", "NYALAKAN"),
            )
            if turn_on is not None:
                await self.tap_element(turn_on)
                elements = await self.dump_hierarchy()

        self._last_stage = "e2e_select_key"
        key_option = self._find_button(
            elements,
            resource_markers=("enc_backup_more_options_encryption_key",),
            labels=("64-digit encryption key", "Kunci enkripsi 64 digit"),
        )
        if key_option is not None:
            await self.tap_element(key_option)
            elements = await self.dump_hierarchy()

        self._last_stage = "e2e_generate_key"
        generate_button = self._find_button(
            elements,
            resource_markers=(
                "encryption_key_info_middle_button",
                "encryption_key_info_bottom_button",
            ),
            labels=(
                "GENERATE YOUR 64-DIGIT KEY",
                "Generate your 64-digit key",
                "BUAT KUNCI 64 DIGIT ANDA",
                "TURN ON",
                "NYALAKAN",
            ),
        )
        chunks = [
            element.text.strip().casefold()
            for element in elements
            if re.fullmatch(r"[0-9a-fA-F]{4}", element.text.strip())
        ]
        if generate_button is not None and len(chunks) < 16:
            await self.tap_element(generate_button, delay=2.0)
            elements = await self.dump_hierarchy()
            chunks = [
                element.text.strip().casefold()
                for element in elements
                if re.fullmatch(r"[0-9a-fA-F]{4}", element.text.strip())
            ]
        if len(chunks) < 16:
            await self.sleep(1.0)
            elements = await self.dump_hierarchy()
            chunks = [
                element.text.strip().casefold()
                for element in elements
                if re.fullmatch(r"[0-9a-fA-F]{4}", element.text.strip())
            ]
        if len(chunks) < 16:
            raise WhatsAppUiAutomationError(
                "Kunci enkripsi 64 digit tidak tampil lengkap"
            )

        hex_key = "".join(chunks[:16])
        if not HEX_KEY_RE.fullmatch(hex_key):
            raise WhatsAppUiAutomationError("Kunci enkripsi 64 digit tidak valid")

        await self._complete_key_setup(elements)
        return hex_key

    async def trigger_backup(self) -> bool:
        self._last_stage = "trigger_backup"
        elements = await self.dump_hierarchy()
        if await self._dismiss_blocking_dialog(elements):
            elements = await self.dump_hierarchy()
        backup_button = self._find_button(
            elements,
            resource_markers=("google_drive_backup_now_btn",),
            labels=("BACK UP", "CADANGKAN", "Back up", "Cadangkan"),
        )
        if backup_button is None:
            return False
        await self.tap_element(backup_button, delay=3.0)

        for poll in range(WHATSAPP_BACKUP_IDLE_POLLS):
            elements = await self.dump_hierarchy()
            if await self._dismiss_blocking_dialog(elements):
                elements = await self.dump_hierarchy()
            if (
                not self._backup_in_progress(elements)
                and self._is_chat_backup_screen(elements)
            ):
                return True
            if poll < WHATSAPP_BACKUP_IDLE_POLLS - 1:
                await self.sleep(1.0)
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
        self._last_stage = "find_crypt15"
        paths: list[str] = []
        for poll in range(WHATSAPP_BACKUP_FIND_POLLS):
            result = await self.transport.run(
                self.serial,
                ["shell", WHATSAPP_BACKUP_FIND],
                operation="whatsapp_backup_find",
                timeout=30.0,
                check=False,
            )
            paths = self._remote_backup_paths(result.stdout)
            if paths:
                break
            if poll < WHATSAPP_BACKUP_FIND_POLLS - 1:
                await self.sleep(WHATSAPP_BACKUP_FIND_POLL_S)
        if not paths:
            result = await self.transport.run(
                self.serial,
                ["shell", WHATSAPP_BACKUP_FIND_FALLBACK],
                operation="whatsapp_backup_find_fallback",
                timeout=60.0,
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
        try:
            destination.unlink(missing_ok=True)
        except OSError as exc:
            raise WhatsAppUiAutomationError(
                "Artifact Crypt15 lama tidak dapat dibersihkan"
            ) from exc
        self._last_stage = "pull_crypt15"
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
        self._last_stage = "navigate_to_backup"
        if not await self.navigate_to_chat_backup():
            raise WhatsAppUiAutomationError("Navigasi ke Cadangan chat gagal")

        self._last_stage = "setup_encryption_key"
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
        self._last_stage = "acquire_crypt15"
        backup = await self.pull_newest_crypt15()
        if backup is None:
            raise WhatsAppUiAutomationError("msgstore.db.crypt15 tidak ditemukan")
        self._last_stage = "complete"
        return WhatsAppBackupArtifact(backup, hex_key, 1)

    async def acquire_backup(
        self,
        *,
        on_progress: Callable[..., Awaitable[None]],
    ) -> WhatsAppBackupArtifact:
        last_error: BaseException | None = None
        for attempt in range(1, WHATSAPP_UI_ATTEMPTS + 1):
            self.layout_mode = WhatsAppLayout.UNKNOWN
            self._last_stage = "attempt_start"
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
                        "failure_stage": self._last_stage,
                        "layout": self.layout_mode.value,
                    },
                )
                if attempt < WHATSAPP_UI_ATTEMPTS:
                    try:
                        await self.press_back(delay=0.5)
                    except asyncio.CancelledError:
                        raise
                    except AcquisitionError as recovery_error:
                        logger.warning(
                            "whatsapp_ui_retry_recovery_failed",
                            extra={
                                "attempt": attempt,
                                "error_category": recovery_error.category.value,
                            },
                        )
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
    55: "interactive",
    62: "interactive",
    90: "call",
}

SELF_JID_SERVERS = {
    "lid_me": "lid",
    "status_me": "s.whatsapp.net",
}
UI_TEXT_KEYS = {
    "body",
    "buttonText",
    "button_text",
    "description",
    "footer",
    "footer_text",
    "label",
    "sub_title",
    "subtitle",
    "text",
    "title",
}


@dataclass(frozen=True, slots=True)
class WhatsAppAccountIdentity:
    account_id: str | None
    self_jids: frozenset[str]


def _jid_value(user: Any, server: Any, raw: Any = None) -> str | None:
    raw_value = _clean_text(raw, 1024)
    if raw_value and "@" in raw_value:
        raw_user, raw_server = raw_value.rsplit("@", 1)
        normalized_raw_server = SELF_JID_SERVERS.get(
            raw_server.casefold(),
            raw_server.casefold(),
        )
        return f"{raw_user}@{normalized_raw_server}".casefold()
    user_value = _clean_text(user, 512)
    server_value = _clean_text(server, 128)
    if not user_value or not server_value:
        return None
    normalized_server = SELF_JID_SERVERS.get(server_value.casefold(), server_value.casefold())
    return f"{user_value}@{normalized_server}".casefold()


def _phone_label(user: Any, server: Any) -> str | None:
    user_value = _clean_text(user, 512)
    server_value = (_clean_text(server, 128) or "").casefold()
    if not user_value or server_value != "s.whatsapp.net":
        return None
    return user_value if user_value.startswith("+") else f"+{user_value}"


def _message_direction(value: Any) -> tuple[str, str]:
    try:
        from_me = int(value)
    except (TypeError, ValueError, OverflowError):
        return "UNKNOWN", "unavailable"
    if from_me == 1:
        return "OUT", "message.from_me"
    if from_me == 0:
        return "IN", "message.from_me"
    return "UNKNOWN", "message.from_me_invalid"


def _unique_text(values: list[str | None], limit: int = MAX_MESSAGE_TEXT_CHARS) -> str:
    return "\n".join(dict.fromkeys(value for value in values if value))[:limit]


def _media_sha256(value: Any) -> str | None:
    text = _clean_text(value, 256)
    if not text:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return text.casefold()
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError, binascii.Error):
        return None
    return decoded.hex() if len(decoded) == 32 else None


def _collect_ui_text(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    output: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "message_params_json" and isinstance(nested, str):
                try:
                    decoded = json.loads(nested)
                except json.JSONDecodeError:
                    continue
                output.extend(_collect_ui_text(decoded, depth=depth + 1))
            elif key in UI_TEXT_KEYS:
                if isinstance(nested, str):
                    text = _clean_text(nested, 16_384)
                    if text:
                        output.append(text)
                elif isinstance(nested, (dict, list)):
                    output.extend(_collect_ui_text(nested, depth=depth + 1))
            elif isinstance(nested, (dict, list)):
                output.extend(_collect_ui_text(nested, depth=depth + 1))
            if len(output) >= 64:
                break
    elif isinstance(value, list):
        for nested in value[:64]:
            output.extend(_collect_ui_text(nested, depth=depth + 1))
            if len(output) >= 64:
                break
    return list(dict.fromkeys(output))[:64]


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

    def _account_identity(
        self,
        connection: sqlite3.Connection,
        tables: set[str],
    ) -> WhatsAppAccountIdentity:
        if "jid" not in tables:
            return WhatsAppAccountIdentity(None, frozenset())
        columns = self._columns(connection, "jid")
        if not {"user", "server"}.issubset(columns):
            return WhatsAppAccountIdentity(None, frozenset())
        raw_select = "raw_string" if "raw_string" in columns else "NULL"
        try:
            rows = connection.execute(
                f"SELECT user, server, {raw_select} AS raw_string FROM jid "
                "WHERE server IN ('lid_me', 'status_me')"
            ).fetchall()
        except sqlite3.Error:
            return WhatsAppAccountIdentity(None, frozenset())
        identities = frozenset(
            identity
            for row in rows
            if (identity := _jid_value(row[0], row[1], row[2])) is not None
        )
        account_id = (
            _opaque_id("whatsapp-account", *sorted(identities)) if identities else None
        )
        return WhatsAppAccountIdentity(account_id, identities)

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
        mapped_server = "phone_jid.server" if jid_map else "NULL"
        mapped_raw = (
            "phone_jid.raw_string"
            if jid_map and "raw_string" in jid_columns
            else "NULL"
        )
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
                   {mapped_select} AS mapped_phone_user,
                   {mapped_server} AS mapped_phone_server,
                   {mapped_raw} AS mapped_phone_raw
            FROM chat
            LEFT JOIN jid AS main_jid ON chat.jid_row_id = main_jid._id
            {join_map}
            {where}
            {order}
        """
        account = self._account_identity(connection, tables)
        chats: list[dict[str, Any]] = []
        for row in connection.execute(query):
            server = _clean_text(row[3], 128) or ""
            main_user = _clean_text(row[2], 512) or ""
            mapped_user = _clean_text(row[5], 512)
            mapped_server_value = _clean_text(row[6], 128)
            raw = _clean_text(row[4], 1024)
            peer_jid = _jid_value(main_user, server, raw)
            peer_phone_jid = _jid_value(mapped_user, mapped_server_value, row[7])
            is_self_chat = bool(
                account.self_jids
                and ({peer_jid, peer_phone_jid} - {None}) & account.self_jids
            )
            is_group = server == "g.us"
            if is_group:
                address = peer_jid or "Group Chat"
                display_name = _clean_text(row[1], 512) or "Unknown Group"
            else:
                phone_label = _phone_label(mapped_user, mapped_server_value) or _phone_label(
                    main_user,
                    server,
                )
                is_service_chat = server == "s.whatsapp.net" and main_user == "0"
                address = peer_jid if is_service_chat else phone_label or peer_jid or "Unknown"
                if is_service_chat:
                    display_name = "WhatsApp"
                elif is_self_chat:
                    display_name = "(You)"
                elif phone_label:
                    display_name = phone_label
                elif server == "lid" and peer_jid:
                    display_name = (
                        "Kontak WhatsApp · "
                        f"{_opaque_id('whatsapp-peer-label', peer_jid, length=8)}"
                    )
                else:
                    display_name = address
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
                    "account_id": account.account_id,
                    "account_slot": 0,
                    "peer_jid": peer_jid,
                    "peer_phone_jid": peer_phone_jid,
                    "is_self_chat": is_self_chat,
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
                jid_columns = self._columns(connection, "jid")
                server_select = "jid.server" if "server" in jid_columns else "NULL"
                raw_select = "jid.raw_string" if "raw_string" in jid_columns else "NULL"
                rows = connection.execute(
                    f"""
                    SELECT jid.user, {server_select}, {raw_select}
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
                if (value := _jid_value(row[0], row[1], row[2])) is not None
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
            selections.extend(
                [
                    "sender_phone_jid.user AS sender_phone_user",
                    self._column(
                        "sender_phone_jid",
                        jid_columns,
                        "server",
                        "sender_phone_server",
                    ),
                    self._column(
                        "sender_phone_jid",
                        jid_columns,
                        "raw_string",
                        "sender_phone_raw",
                    ),
                ]
            )
        else:
            selections.extend(
                [
                    "NULL AS sender_phone_user",
                    "NULL AS sender_phone_server",
                    "NULL AS sender_phone_raw",
                ]
            )
        field_names.extend(
            ["sender_phone_user", "sender_phone_server", "sender_phone_raw"]
        )

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
                    ("file_hash", "file_hash"),
                    ("original_file_hash", "original_file_hash"),
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

        system_columns = (
            self._columns(connection, "message_system")
            if "message_system" in tables
            else set()
        )
        if {"message_row_id", "action_type"}.issubset(system_columns):
            joins.append(
                "LEFT JOIN message_system AS system_event "
                "ON msg._id = system_event.message_row_id"
            )
            selections.append("system_event.action_type AS system_action_type")
        else:
            selections.append("NULL AS system_action_type")
        field_names.append("system_action_type")

        system_specs: tuple[
            tuple[str, str, tuple[tuple[str, str], ...]], ...
        ] = (
            (
                "message_system_chat_participant",
                "system_participant",
                (("user_jid_row_id", "system_participant_jid_row_id"),),
            ),
            (
                "message_system_group",
                "system_group",
                (("is_me_joined", "system_group_is_me_joined"),),
            ),
            (
                "message_system_number_change",
                "system_number_change",
                (
                    ("old_jid_row_id", "system_old_number_jid_row_id"),
                    ("new_jid_row_id", "system_new_number_jid_row_id"),
                ),
            ),
            (
                "message_system_device_change",
                "system_device_change",
                (
                    ("device_added_count", "system_device_added_count"),
                    ("device_removed_count", "system_device_removed_count"),
                ),
            ),
            (
                "message_system_privacy",
                "system_privacy",
                (
                    ("is_transition", "system_privacy_transition"),
                    ("message_privacy_type", "system_privacy_type"),
                ),
            ),
            (
                "message_system_lid_change",
                "system_lid_change",
                (
                    ("old_lid_row_id", "system_old_lid_row_id"),
                    ("new_lid_row_id", "system_new_lid_row_id"),
                    ("display_name", "system_lid_display_name"),
                ),
            ),
            (
                "message_system_initial_privacy_provider",
                "system_initial_privacy",
                (
                    ("privacy_provider", "system_privacy_provider"),
                    ("biz_state_id", "system_biz_state_id"),
                    ("is_deprecated", "system_privacy_deprecated"),
                ),
            ),
            (
                "message_system_business_state",
                "system_business_state",
                (
                    ("privacy_message_type", "system_business_privacy_type"),
                    ("business_name", "system_business_name"),
                    ("is_deprecated", "system_business_deprecated"),
                ),
            ),
            (
                "message_system_ephemeral_setting_not_applied",
                "system_ephemeral",
                (("setting_duration", "system_ephemeral_duration"),),
            ),
            (
                "message_system_block_contact",
                "system_block_contact",
                (("is_blocked", "system_is_blocked"),),
            ),
            (
                "message_system_username_change",
                "system_username_change",
                (
                    ("display_name", "system_username_display_name"),
                    ("old_username", "system_old_username"),
                    ("new_username", "system_new_username"),
                ),
            ),
            (
                "message_system_group_auto_restrict",
                "system_group_restrict",
                (("threshold", "system_group_restrict_threshold"),),
            ),
            (
                "message_system_group_with_parent",
                "system_group_parent",
                (("linked_parent_group_name", "system_parent_group_name"),),
            ),
        )
        joined_system: dict[str, set[str]] = {}
        for table, alias, columns in system_specs:
            table_columns = self._columns(connection, table) if table in tables else set()
            if "message_row_id" in table_columns:
                joins.append(
                    f"LEFT JOIN {table} AS {alias} ON msg._id = {alias}.message_row_id"
                )
                joined_system[table] = table_columns
                for column, output in columns:
                    selections.append(self._column(alias, table_columns, column, output))
                    field_names.append(output)
            else:
                for _column_name, output in columns:
                    selections.append(f"NULL AS {output}")
                    field_names.append(output)

        photo_columns = (
            self._columns(connection, "message_system_photo_change")
            if "message_system_photo_change" in tables
            else set()
        )
        if "message_row_id" in photo_columns:
            joins.append(
                "LEFT JOIN message_system_photo_change AS system_photo_change "
                "ON msg._id = system_photo_change.message_row_id"
            )
            photo_values = [
                f"system_photo_change.{name} IS NOT NULL"
                for name in ("new_photo_id", "old_photo", "new_photo")
                if name in photo_columns
            ]
            selections.append(
                f"CASE WHEN {' OR '.join(photo_values)} THEN 1 ELSE 0 END "
                "AS system_photo_changed"
                if photo_values
                else "0 AS system_photo_changed"
            )
        else:
            selections.append("0 AS system_photo_changed")
        field_names.append("system_photo_changed")

        def append_system_jid(
            *,
            table: str,
            alias: str,
            row_id_column: str,
            output_prefix: str,
        ) -> None:
            table_columns = joined_system.get(table, set())
            if row_id_column in table_columns and {"_id", "user"}.issubset(jid_columns):
                jid_alias = f"{output_prefix}_jid"
                joins.append(
                    f"LEFT JOIN jid AS {jid_alias} "
                    f"ON {alias}.{row_id_column} = {jid_alias}._id"
                )
                selections.extend(
                    [
                        f"{jid_alias}.user AS {output_prefix}_user",
                        self._column(
                            jid_alias,
                            jid_columns,
                            "server",
                            f"{output_prefix}_server",
                        ),
                        self._column(
                            jid_alias,
                            jid_columns,
                            "raw_string",
                            f"{output_prefix}_raw",
                        ),
                    ]
                )
            else:
                selections.extend(
                    [
                        f"NULL AS {output_prefix}_user",
                        f"NULL AS {output_prefix}_server",
                        f"NULL AS {output_prefix}_raw",
                    ]
                )
            field_names.extend(
                [
                    f"{output_prefix}_user",
                    f"{output_prefix}_server",
                    f"{output_prefix}_raw",
                ]
            )

        append_system_jid(
            table="message_system_chat_participant",
            alias="system_participant",
            row_id_column="user_jid_row_id",
            output_prefix="system_participant",
        )
        append_system_jid(
            table="message_system_number_change",
            alias="system_number_change",
            row_id_column="old_jid_row_id",
            output_prefix="system_old_number",
        )
        append_system_jid(
            table="message_system_number_change",
            alias="system_number_change",
            row_id_column="new_jid_row_id",
            output_prefix="system_new_number",
        )
        append_system_jid(
            table="message_system_lid_change",
            alias="system_lid_change",
            row_id_column="old_lid_row_id",
            output_prefix="system_old_lid",
        )
        append_system_jid(
            table="message_system_lid_change",
            alias="system_lid_change",
            row_id_column="new_lid_row_id",
            output_prefix="system_new_lid",
        )

        ui_columns = (
            self._columns(connection, "message_ui_elements")
            if "message_ui_elements" in tables
            else set()
        )
        ui_outputs = (
            ("_id", "ui_element_row_id"),
            ("element_type", "ui_element_type"),
            ("element_content", "ui_element_content"),
            ("description", "ui_description"),
            ("template_id", "ui_template_id"),
            ("footer_text", "ui_footer_text"),
            ("button_text", "ui_button_text"),
            ("message_type", "ui_message_type"),
        )
        if "message_row_id" in ui_columns:
            joins.append(
                "LEFT JOIN message_ui_elements AS ui "
                "ON msg._id = ui.message_row_id"
            )
            for column, output in ui_outputs:
                selections.append(self._column("ui", ui_columns, column, output))
                field_names.append(output)
        else:
            for _column_name, output in ui_outputs:
                selections.append(f"NULL AS {output}")
                field_names.append(output)

        quoted_columns = joined_optional.get("message_quoted", set())
        if "sender_jid_row_id" in quoted_columns and {"_id", "user"}.issubset(jid_columns):
            joins.append(
                "LEFT JOIN jid AS quoted_jid "
                "ON quoted.sender_jid_row_id = quoted_jid._id"
            )
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
            call_outputs = (
                ("_id", "call_log_row_id"),
                ("duration", "call_duration"),
                ("from_me", "call_from_me"),
                ("video_call", "call_is_video"),
                ("call_result", "call_result"),
                ("call_type", "call_type"),
            )
            for column, output in call_outputs:
                selections.append(self._column("call_log", call_columns, column, output))
                field_names.append(output)
        else:
            for output in (
                "call_log_row_id",
                "call_duration",
                "call_from_me",
                "call_is_video",
                "call_result",
                "call_type",
            ):
                selections.append(f"NULL AS {output}")
                field_names.append(output)

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
    def _interactive_content(
        row: dict[str, Any],
        text: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        raw = row.get("ui_element_content")
        payload: dict[str, Any] = {}
        if isinstance(raw, str) and raw.strip():
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded
        title = _clean_text(payload.get("title"), 16_384)
        description = _clean_text(
            payload.get("description") or row.get("ui_description"),
            16_384,
        )
        subtitle = _clean_text(
            payload.get("sub_title") or payload.get("subtitle"),
            16_384,
        )
        footer = _clean_text(
            payload.get("footer_text") or row.get("ui_footer_text"),
            16_384,
        )
        button = _clean_text(
            payload.get("buttonText") or row.get("ui_button_text"),
            16_384,
        )
        extracted = _unique_text(
            [
                text,
                title,
                description,
                subtitle,
                footer,
                button,
                *_collect_ui_text(payload),
            ]
        )
        if row.get("ui_element_row_id") is None:
            return None, text or ""
        return (
            {
                "element_type": (
                    _safe_int(row.get("ui_element_type"))
                    if row.get("ui_element_type") is not None
                    else None
                ),
                "message_type": (
                    _safe_int(row.get("ui_message_type"))
                    if row.get("ui_message_type") is not None
                    else None
                ),
                "title": title,
                "description": description,
                "subtitle": subtitle,
                "footer": footer,
                "button_text": button,
                "template_id": _clean_text(row.get("ui_template_id"), 1024),
            },
            extracted,
        )

    @staticmethod
    def _system_event(row: dict[str, Any], text: str | None) -> dict[str, Any] | None:
        action_value = row.get("system_action_type")
        if action_value is None and _safe_int(row.get("message_type"), -1) != 7:
            return None
        action_type = _safe_int(action_value, -1)
        participant_jid = _jid_value(
            row.get("system_participant_user"),
            row.get("system_participant_server"),
            row.get("system_participant_raw"),
        )
        old_number_jid = _jid_value(
            row.get("system_old_number_user"),
            row.get("system_old_number_server"),
            row.get("system_old_number_raw"),
        )
        new_number_jid = _jid_value(
            row.get("system_new_number_user"),
            row.get("system_new_number_server"),
            row.get("system_new_number_raw"),
        )
        old_lid = _jid_value(
            row.get("system_old_lid_user"),
            row.get("system_old_lid_server"),
            row.get("system_old_lid_raw"),
        )
        new_lid = _jid_value(
            row.get("system_new_lid_user"),
            row.get("system_new_lid_server"),
            row.get("system_new_lid_raw"),
        )

        kind = "unknown"
        label = ""
        if old_number_jid or new_number_jid:
            kind, label = "number_change", "Nomor WhatsApp berubah"
        elif old_lid or new_lid or row.get("system_lid_display_name"):
            kind, label = "lid_change", "Identitas privat WhatsApp diperbarui"
        elif bool(_safe_int(row.get("system_photo_changed"))):
            kind, label = "photo_change", "Foto percakapan diperbarui"
        elif (
            row.get("system_device_added_count") is not None
            or row.get("system_device_removed_count") is not None
        ):
            kind, label = "device_change", "Perangkat tertaut diperbarui"
        elif row.get("system_privacy_type") is not None:
            kind, label = "privacy_change", "Pengaturan privasi diperbarui"
        elif row.get("system_privacy_provider") is not None:
            kind, label = (
                "initial_privacy_provider",
                "Informasi privasi awal percakapan dicatat",
            )
        elif row.get("system_business_privacy_type") is not None:
            kind, label = "business_state", "Status privasi akun bisnis diperbarui"
        elif row.get("system_ephemeral_duration") is not None:
            kind, label = (
                "ephemeral_setting",
                "Pengaturan pesan sementara diperbarui",
            )
        elif row.get("system_is_blocked") is not None:
            blocked = bool(_safe_int(row.get("system_is_blocked")))
            kind, label = (
                "contact_block",
                "Kontak diblokir" if blocked else "Blokir kontak dibuka",
            )
        elif row.get("system_new_username") or row.get("system_old_username"):
            kind, label = "username_change", "Nama pengguna WhatsApp berubah"
        elif row.get("system_group_restrict_threshold") is not None:
            kind, label = "group_restriction", "Pembatasan grup diperbarui"
        elif row.get("system_parent_group_name"):
            kind, label = "group_link", "Tautan komunitas atau grup diperbarui"
        elif participant_jid:
            kind = "participant_change"
            label = (
                "Anggota grup ditambahkan"
                if action_type == 2
                else "Keanggotaan grup diperbarui"
            )
        elif text:
            kind, label = "described_event", text
        else:
            label = (
                f"Peristiwa sistem WhatsApp (kode {action_type})"
                if action_type >= 0
                else "Peristiwa sistem WhatsApp"
            )

        if text and text.casefold() not in label.casefold():
            label = f"{label} · {text}"
        return {
            "action_type": action_type if action_type >= 0 else None,
            "kind": kind,
            "label": _clean_text(label, MAX_MESSAGE_TEXT_CHARS),
            "participant_jid": participant_jid,
            "old_number_jid": old_number_jid,
            "new_number_jid": new_number_jid,
            "old_lid": old_lid,
            "new_lid": new_lid,
            "device_added_count": (
                max(0, _safe_int(row.get("system_device_added_count")))
                if row.get("system_device_added_count") is not None
                else None
            ),
            "device_removed_count": (
                max(0, _safe_int(row.get("system_device_removed_count")))
                if row.get("system_device_removed_count") is not None
                else None
            ),
            "privacy_type": row.get("system_privacy_type"),
            "privacy_provider": row.get("system_privacy_provider"),
            "business_privacy_type": row.get("system_business_privacy_type"),
            "ephemeral_duration": row.get("system_ephemeral_duration"),
        }

    @staticmethod
    def _message_record(
        chat: dict[str, Any],
        participants: list[str],
        past_participants: list[str],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        type_code = _safe_int(row.get("message_type"), -1)
        text = _clean_text(row.get("text_data"))
        system_event = WhatsAppDatabaseParser._system_event(row, text)
        interactive, interactive_text = WhatsAppDatabaseParser._interactive_content(
            row,
            text,
        )
        has_call = row.get("call_log_row_id") is not None or type_code == 90
        if system_event is not None:
            message_type = "system"
        elif has_call:
            message_type = "call"
        elif interactive is not None or type_code in {55, 62}:
            message_type = "interactive"
        else:
            message_type = MESSAGE_TYPE_NAMES.get(type_code, f"type_{type_code}")

        direction, direction_evidence = _message_direction(row.get("from_me"))
        if message_type == "system":
            direction, direction_evidence = "UNKNOWN", "system_event"
        elif message_type == "call" and direction == "UNKNOWN":
            direction, _unused = _message_direction(row.get("call_from_me"))
            direction_evidence = (
                "call_log.from_me" if direction != "UNKNOWN" else "unavailable"
            )

        sender_jid = _jid_value(
            row.get("sender_user"),
            row.get("sender_server"),
            row.get("sender_raw"),
        )
        sender_phone_jid = _jid_value(
            row.get("sender_phone_user"),
            row.get("sender_phone_server"),
            row.get("sender_phone_raw"),
        )
        sender_phone_label = _phone_label(
            row.get("sender_phone_user"),
            row.get("sender_phone_server"),
        ) or _phone_label(row.get("sender_user"), row.get("sender_server"))
        if system_event is not None:
            participant_jid = system_event.get("participant_jid")
        elif chat["is_group"] and direction == "IN":
            participant_jid = sender_phone_jid or sender_jid
        else:
            participant_jid = None
        if message_type == "system":
            actor_kind = "system"
            sender = "WhatsApp"
        elif direction == "OUT":
            actor_kind = "self"
            sender = "Anda"
        elif direction == "IN" and chat["is_group"]:
            actor_kind = "group_participant" if participant_jid else "unknown"
            sender = (
                sender_phone_label
                or sender_phone_jid
                or sender_jid
            )
        elif direction == "IN":
            actor_kind = "peer"
            sender = (
                sender_phone_label
                or sender_phone_jid
                or sender_jid
                or chat["name"]
            )
        else:
            actor_kind = "unknown"
            sender = sender_phone_jid or sender_jid

        caption = _clean_text(row.get("media_caption"))
        latitude = row.get("latitude")
        longitude = row.get("longitude")
        place_name = _clean_text(row.get("place_name"), 1024)
        place_address = _clean_text(row.get("place_address"), 2048)
        location_url = _clean_text(row.get("url"), 2048)
        location = None
        if latitude is not None or longitude is not None or place_name or place_address:
            location = {
                "latitude": latitude,
                "longitude": longitude,
                "place_name": place_name,
                "address": place_address,
                "url": location_url,
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
                "sha256": _media_sha256(row.get("original_file_hash"))
                or _media_sha256(row.get("file_hash")),
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

        call = None
        if message_type == "call":
            duration = max(0, _safe_int(row.get("call_duration")))
            is_video = bool(_safe_int(row.get("call_is_video")))
            call = {
                "kind": "video" if is_video else "voice",
                "duration": duration,
                "result_code": (
                    _safe_int(row.get("call_result"))
                    if row.get("call_result") is not None
                    else None
                ),
                "type_code": (
                    _safe_int(row.get("call_type"))
                    if row.get("call_type") is not None
                    else None
                ),
            }
            primary_text = "Panggilan video WhatsApp" if is_video else "Panggilan suara WhatsApp"
            if duration:
                primary_text = f"{primary_text} · {duration} detik"
        elif message_type == "system":
            primary_text = str(system_event.get("label") or "Peristiwa sistem WhatsApp")
        elif message_type == "interactive":
            primary_text = interactive_text or "Pesan interaktif WhatsApp"
        else:
            primary_text = text or caption
            if message_type == "location" and not primary_text:
                primary_text = "Lokasi dibagikan"
            elif revoked and not primary_text:
                primary_text = "Pesan dicabut"
            elif not primary_text:
                primary_text = f"[Pesan WhatsApp tipe {type_code}]"

        location_text = _unique_text([place_name, place_address, location_url])
        analysis_text = ""
        if message_type not in {"system", "call"} and not revoked:
            analysis_text = _unique_text(
                [
                    text,
                    caption,
                    interactive_text if message_type == "interactive" else None,
                    location_text or None,
                ]
            )
        normalized_text = analysis_text or _clean_text(primary_text) or ""
        preview = _clean_text(primary_text, MAX_PREVIEW_CHARS) or "Pesan WhatsApp"
        return {
            "schema_version": 2,
            "kind": "whatsapp_message",
            "source": "whatsapp",
            "source_app": WHATSAPP_PACKAGE,
            "account_id": chat["account_id"],
            "account_slot": chat["account_slot"],
            "record_id": message_id,
            "album": "WhatsApp",
            "display_name": chat["name"],
            "preview_text": preview,
            "normalized_text": normalized_text,
            "analysis_text": analysis_text,
            "analysis_eligible": bool(analysis_text),
            "captured_at": timestamp,
            "source_created_at": timestamp,
            "conversation": {
                "id": chat["id"],
                "name": chat["name"],
                "address": chat["address"],
                "type": chat["type"],
                "peer_jid": chat["peer_jid"],
                "peer_phone_jid": chat["peer_phone_jid"],
                "is_self_chat": chat["is_self_chat"],
                "participants": participants,
                "past_participants": past_participants,
            },
            "message": {
                "id": message_id,
                "key_id": key_id,
                "direction": direction,
                "direction_evidence": direction_evidence,
                "actor_kind": actor_kind,
                "actor_jid": sender_phone_jid or sender_jid,
                "peer_jid": chat["peer_jid"],
                "participant_jid": participant_jid,
                "sender": sender,
                "type": message_type,
                "type_code": type_code,
                "text": text,
                "display_text": _clean_text(primary_text),
                "timestamp": timestamp,
                "starred": bool(_safe_int(row.get("starred"))),
                "revoked": revoked,
                "revoked_at": _timestamp_iso(row.get("revoke_timestamp")),
                "revoked_by": _clean_text(row.get("revoked_admin"), 512),
                "forward_score": max(0, _safe_int(row.get("forward_score"))),
                "edited_at": _timestamp_iso(row.get("edited_timestamp")),
                "thumbnail_available": bool(
                    _safe_int(row.get("thumbnail_available"))
                ),
                "system_action_type": (
                    system_event.get("action_type") if system_event else None
                ),
                "system_event": system_event,
                "interactive": interactive,
                "call": call,
                "media_filename": media.get("filename") if media else None,
                "media_source_path": media.get("source_path") if media else None,
                "media_size": media.get("file_size") if media else 0,
                "media_mime_type": media.get("mime_type") if media else None,
                "media_sha256": media.get("sha256") if media else None,
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
