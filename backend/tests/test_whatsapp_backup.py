from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import zlib
from pathlib import Path

import pytest

from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.contracts import AcquisitionResult, ProviderKind
from app.acquisition.whatsapp_backup import (
    WHATSAPP_MESSAGE_MIME,
    WHATSAPP_UI_ATTEMPTS,
    UIElement,
    WhatsAppBackupAcquisitionService,
    WhatsAppAcquisitionResult,
    WhatsAppBackupArtifact,
    WhatsAppDatabaseParser,
    WhatsAppE2eState,
    WhatsAppLayout,
    WhatsAppNotSignedInError,
    WhatsAppParseError,
    WhatsAppUiAutomationError,
    WhatsAppUiAutomator,
    activity_shows_unsigned_in,
    decrypt_crypt15,
    detect_whatsapp_layout,
    hierarchy_shows_unsigned_in,
)
from app.core.config import settings
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, DeviceType, Scenario
from app.services import acquisition as acquisition_service
from app.services.acquisition import index_staging
from app.services.analysis import analyze_content_result, read_preview
from app.services.gallery import list_items
from app.services.reports import build_session_report, report_to_html


def _crypt15_payload(hex_key: str, plaintext: bytes, iv: bytes) -> bytes:
    from Cryptodome.Cipher import AES

    nested_iv = b"\x0a\x10" + iv
    protobuf = b"\x08\x01" + b"\x1a" + bytes([len(nested_iv)]) + nested_iv
    header = bytes([len(protobuf), 1]) + protobuf
    extracted = hmac.new(b"\x00" * 32, bytes.fromhex(hex_key), hashlib.sha256).digest()
    key = hmac.new(extracted, b"backup encryption\x01", hashlib.sha256).digest()
    encrypted, tag = AES.new(key, AES.MODE_GCM, iv).encrypt_and_digest(
        zlib.compress(plaintext)
    )
    checksum = hashlib.md5(header + encrypted + tag, usedforsecurity=False).digest()
    return header + encrypted + tag + checksum


def test_decrypt_crypt15_validates_header_tag_checksum_and_sqlite(tmp_path: Path) -> None:
    key = "12" * 32
    plaintext = b"SQLite format 3\x00" + b"canonical-whatsapp-test" * 16
    encrypted = tmp_path / "msgstore.db.crypt15"
    decrypted = tmp_path / "msgstore.db"
    encrypted.write_bytes(_crypt15_payload(key, plaintext, bytes(range(16))))

    decrypt_crypt15(key, encrypted, decrypted)

    assert decrypted.read_bytes() == plaintext


def test_decrypt_crypt15_rejects_tampered_authentication(tmp_path: Path) -> None:
    key = "34" * 32
    payload = bytearray(
        _crypt15_payload(
            key,
            b"SQLite format 3\x00" + b"x" * 256,
            bytes(range(16, 32)),
        )
    )
    payload[-20] ^= 0x01
    encrypted = tmp_path / "tampered.crypt15"
    encrypted.write_bytes(payload)

    with pytest.raises(WhatsAppParseError, match="Autentikasi"):
        decrypt_crypt15(key, encrypted, tmp_path / "must-not-exist.db")


def _build_whatsapp_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE jid (
            _id INTEGER PRIMARY KEY,
            user TEXT,
            server TEXT,
            raw_string TEXT
        );
        CREATE TABLE jid_map (lid_row_id INTEGER, jid_row_id INTEGER);
        CREATE TABLE props (key TEXT, value TEXT);
        CREATE TABLE chat (
            _id INTEGER PRIMARY KEY,
            jid_row_id INTEGER,
            hidden INTEGER,
            subject TEXT,
            sort_timestamp INTEGER
        );
        CREATE TABLE message (
            _id INTEGER PRIMARY KEY,
            sort_id INTEGER,
            chat_row_id INTEGER,
            from_me INTEGER,
            key_id TEXT,
            sender_jid_row_id INTEGER,
            timestamp INTEGER,
            message_type INTEGER,
            text_data TEXT,
            starred INTEGER
        );
        CREATE VIEW available_message_view AS SELECT * FROM message;
        CREATE TABLE message_media (
            message_row_id INTEGER PRIMARY KEY,
            file_size INTEGER,
            media_name TEXT,
            media_caption TEXT,
            mime_type TEXT,
            file_path TEXT,
            media_duration INTEGER
        );
        CREATE TABLE message_location (
            message_row_id INTEGER PRIMARY KEY,
            latitude REAL,
            longitude REAL,
            place_name TEXT,
            place_address TEXT,
            url TEXT
        );
        CREATE TABLE message_quoted (
            message_row_id INTEGER PRIMARY KEY,
            text_data TEXT,
            sender_jid_row_id INTEGER
        );
        CREATE TABLE message_revoked (
            message_row_id INTEGER PRIMARY KEY,
            revoked_key_id TEXT,
            revoke_timestamp INTEGER
        );
        CREATE TABLE message_forwarded (
            message_row_id INTEGER PRIMARY KEY,
            forward_score INTEGER
        );
        CREATE TABLE message_edit_info (
            message_row_id INTEGER PRIMARY KEY,
            edited_timestamp INTEGER
        );
        CREATE TABLE message_system (
            message_row_id INTEGER PRIMARY KEY,
            action_type INTEGER
        );
        CREATE TABLE message_system_initial_privacy_provider (
            message_row_id INTEGER PRIMARY KEY,
            privacy_provider INTEGER,
            biz_state_id INTEGER,
            is_deprecated INTEGER
        );
        CREATE TABLE message_ui_elements (
            _id INTEGER PRIMARY KEY,
            message_row_id INTEGER,
            element_type INTEGER,
            element_content TEXT,
            description TEXT,
            template_id TEXT,
            footer_text TEXT,
            button_text TEXT,
            message_type INTEGER
        );
        CREATE TABLE message_call_log (
            message_row_id INTEGER PRIMARY KEY,
            call_log_row_id INTEGER
        );
        CREATE TABLE call_log (
            _id INTEGER PRIMARY KEY,
            from_me INTEGER,
            video_call INTEGER,
            duration INTEGER,
            call_result INTEGER,
            call_type INTEGER
        );
        CREATE TABLE group_participant_user (
            group_jid_row_id INTEGER,
            user_jid_row_id INTEGER
        );
        CREATE TABLE group_past_participant_user (
            group_jid_row_id INTEGER,
            user_jid_row_id INTEGER
        );
        """
    )
    connection.executemany(
        "INSERT INTO jid (_id, user, server, raw_string) VALUES (?, ?, ?, ?)",
        [
            (1, "lid-direct", "lid", "lid-direct@lid"),
            (2, "group-one", "g.us", "group-one@g.us"),
            (3, "628111111111", "s.whatsapp.net", "628111111111@s.whatsapp.net"),
            (4, "628222222222", "s.whatsapp.net", "628222222222@s.whatsapp.net"),
            (5, "628333333333", "s.whatsapp.net", "628333333333@s.whatsapp.net"),
            (6, "self-lid", "lid_me", "self-lid@lid_me"),
            (7, "628999999999", "status_me", "628999999999@status_me"),
        ],
    )
    connection.execute("INSERT INTO jid_map VALUES (1, 3)")
    connection.executemany(
        "INSERT INTO chat VALUES (?, ?, ?, ?, ?)",
        [
            (10, 1, 0, None, 1_785_000_000_000),
            (20, 2, 0, "Tim Uji", 1_786_000_000_000),
        ],
    )
    connection.executemany(
        "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (100, 1, 10, 1, "out-key", 0, 1_785_000_000_000, 0, "pesan keluar", 1),
            (200, 2, 20, 0, "group-key", 4, 1_786_000_000_000, 1, None, 0),
            (300, 3, 10, 0, "old-key", 3, 1_600_000_000_000, 0, "terlalu lama", 0),
            (400, 4, 10, 1, "system-key", 0, 1_786_000_000_100, 7, None, 0),
            (500, 5, 10, 0, "interactive-key", 3, 1_786_000_000_200, 55, None, 0),
            (600, 6, 10, 0, "call-key", 3, 1_786_000_000_300, 90, None, 0),
        ],
    )
    connection.execute(
        "INSERT INTO message_media VALUES (?, ?, ?, ?, ?, ?, ?)",
        (200, 1_024, "image.jpg", "caption berisiko", "image/jpeg", "/Media/image.jpg", 0),
    )
    connection.execute(
        "INSERT INTO message_quoted VALUES (?, ?, ?)",
        (200, "pesan yang dikutip", 5),
    )
    connection.execute("INSERT INTO message_forwarded VALUES (?, ?)", (200, 2))
    connection.execute("INSERT INTO message_system VALUES (?, ?)", (400, 67))
    connection.execute(
        "INSERT INTO message_system_initial_privacy_provider VALUES (?, ?, ?, ?)",
        (400, 0, 10, 0),
    )
    connection.execute(
        "INSERT INTO message_ui_elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            1,
            500,
            6,
            json.dumps({"title": "Pilih layanan", "description": "Konfirmasi pesanan"}),
            None,
            "template-1",
            None,
            None,
            None,
        ),
    )
    connection.execute("INSERT INTO call_log VALUES (?, ?, ?, ?, ?, ?)", (1, 0, 0, 0, 2, 0))
    connection.execute("INSERT INTO message_call_log VALUES (?, ?)", (600, 1))
    connection.execute(
        "INSERT INTO group_participant_user VALUES (?, ?)",
        (2, 4),
    )
    connection.commit()
    connection.close()


def test_database_parser_exports_canonical_chat_messages_defensively(
    tmp_path: Path,
) -> None:
    database = tmp_path / "msgstore.db"
    output = tmp_path / "whatsapp"
    _build_whatsapp_database(database)

    summary = WhatsAppDatabaseParser(
        database,
        output,
        cutoff_epoch_ms=1_700_000_000_000,
    ).export()

    assert summary.conversation_count == 2
    assert summary.message_count == 5
    assert summary.skipped_messages == 0
    records = [json.loads(path.read_text()) for path in output.rglob("*.json")]
    assert {record["kind"] for record in records} == {"whatsapp_message"}
    assert {record["message"]["direction"] for record in records} == {"IN", "OUT", "UNKNOWN"}
    group = next(record for record in records if record["conversation"]["type"] == "group")
    assert group["conversation"]["name"] == "Tim Uji"
    assert group["message"]["type"] == "image"
    assert group["message"]["sender"] == "+628222222222"
    assert group["message"]["media"]["caption"] == "caption berisiko"
    assert group["message"]["quote"]["text"] == "pesan yang dikutip"
    assert group["message"]["forward_score"] == 2
    assert "caption berisiko" in group["normalized_text"]
    assert "pesan yang dikutip" not in group["analysis_text"]
    direct = next(
        record
        for record in records
        if record["conversation"]["type"] == "chat"
        and record["message"]["type"] == "text"
    )
    assert direct["conversation"]["name"] == "+628111111111"
    assert direct["conversation"]["is_self_chat"] is False
    assert direct["account_id"]
    system = next(record for record in records if record["message"]["type"] == "system")
    assert system["message"]["direction"] == "UNKNOWN"
    assert system["message"]["actor_kind"] == "system"
    assert system["message"]["system_event"]["kind"] == "initial_privacy_provider"
    assert system["analysis_text"] == ""
    interactive = next(
        record for record in records if record["message"]["type"] == "interactive"
    )
    assert "Pilih layanan" in interactive["analysis_text"]
    call = next(record for record in records if record["message"]["type"] == "call")
    assert call["message"]["call"]["kind"] == "voice"
    assert call["analysis_text"] == ""
    assert all("wa_64digit" not in json.dumps(record) for record in records)


@pytest.mark.asyncio
async def test_ui_automator_retries_full_attempt_exactly_four_times(tmp_path: Path) -> None:
    calls = 0
    events: list[dict] = []

    async def no_sleep(_seconds: float) -> None:
        return None

    class AlwaysFails(WhatsAppUiAutomator):
        async def _single_attempt(self) -> WhatsAppBackupArtifact:
            nonlocal calls
            calls += 1
            raise WhatsAppUiAutomationError("gagal")

    automator = AlwaysFails(serial="device-1", work_dir=tmp_path, sleep=no_sleep)

    async def on_progress(*_args, **fields) -> None:
        events.append(fields)

    with pytest.raises(AcquisitionError) as raised:
        await automator.acquire_backup(on_progress=on_progress)

    assert calls == WHATSAPP_UI_ATTEMPTS == 4
    assert [event["whatsapp_ui_attempt"] for event in events] == [1, 2, 3, 4]
    assert raised.value.category == ErrorCategory.ACCESS_TIMEOUT
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_ui_automator_returns_on_fourth_success(tmp_path: Path) -> None:
    calls = 0
    backup = tmp_path / "msgstore.db.crypt15"
    backup.write_bytes(b"backup")

    async def no_sleep(_seconds: float) -> None:
        return None

    class FourthSucceeds(WhatsAppUiAutomator):
        async def _single_attempt(self) -> WhatsAppBackupArtifact:
            nonlocal calls
            calls += 1
            if calls < 4:
                raise WhatsAppUiAutomationError("belum")
            return WhatsAppBackupArtifact(backup, "ab" * 32, 1)

    automator = FourthSucceeds(serial="device-1", work_dir=tmp_path, sleep=no_sleep)

    async def on_progress(*_args, **_fields) -> None:
        return None

    result = await automator.acquire_backup(on_progress=on_progress)

    assert calls == 4
    assert result.ui_attempts == 4


def _ui(
    resource_id: str = "",
    text: str = "",
    content_desc: str = "",
    *,
    bounds: tuple[int, int, int, int] = (0, 0, 120, 80),
    clickable: bool = True,
) -> UIElement:
    return UIElement(resource_id, text, content_desc, bounds, clickable)


def _screen(*elements: UIElement) -> list[UIElement]:
    return [
        _ui(bounds=(0, 0, 1080, 2400), clickable=False),
        *elements,
    ]


def test_layout_detection_prefers_new_profile_tab_and_preserves_old_overflow() -> None:
    overflow = _ui(
        "com.whatsapp:id/menuitem_overflow",
        content_desc="Opsi lainnya",
        bounds=(960, 80, 1080, 220),
    )
    profile_tab = _ui(
        content_desc="Anda",
        bounds=(820, 2050, 1080, 2300),
    )
    message_you = _ui(text="You: pesan", bounds=(100, 500, 700, 620))

    assert (
        detect_whatsapp_layout(_screen(overflow, profile_tab, message_you))
        is WhatsAppLayout.PROFILE_TAB
    )
    assert (
        detect_whatsapp_layout(_screen(overflow, message_you))
        is WhatsAppLayout.OVERFLOW_MENU
    )
    assert detect_whatsapp_layout(_screen(message_you)) is WhatsAppLayout.UNKNOWN


@pytest.mark.asyncio
async def test_new_layout_navigation_uses_profile_tab_scroll_and_chat_rows(
    tmp_path: Path,
) -> None:
    screens = iter(
        [
            _screen(
                _ui(
                    "com.whatsapp:id/conversations_row_contact_name",
                    bounds=(80, 420, 620, 520),
                ),
                _ui(
                    "com.whatsapp:id/menuitem_overflow",
                    content_desc="Opsi lainnya",
                    bounds=(960, 80, 1080, 220),
                ),
                _ui(content_desc="Anda", bounds=(820, 2050, 1080, 2300)),
            ),
            _screen(
                _ui("com.whatsapp:id/me_tab_root_layout"),
                _ui("com.whatsapp:id/settings_account_info", text="Akun"),
            ),
            _screen(
                _ui("com.whatsapp:id/me_tab_root_layout"),
                _ui(
                    "com.whatsapp:id/settings_chat",
                    content_desc="Chat,Riwayat obrolan, cadangan",
                    bounds=(20, 1250, 1060, 1450),
                ),
            ),
            _screen(
                _ui("com.whatsapp:id/enter_key_preference"),
                _ui(
                    "com.whatsapp:id/chat_backup_preference",
                    content_desc="Cadangan obrolan",
                    bounds=(20, 1350, 1060, 1530),
                ),
            ),
            _screen(_ui("com.whatsapp:id/google_drive_backup_now_btn")),
        ]
    )
    taps: list[str] = []
    scrolls = 0

    async def no_sleep(_seconds: float) -> None:
        return None

    class NewLayoutAutomator(WhatsAppUiAutomator):
        async def launch_whatsapp(self) -> None:
            return None

        async def _activity_top_dump(self) -> str:
            return ""

        async def dump_hierarchy(self) -> list[UIElement]:
            return next(screens)

        async def tap_element(
            self,
            element: UIElement,
            delay: float = 1.5,
        ) -> None:
            del delay
            taps.append(element.resource_id or element.content_desc or element.text)

        async def swipe_up(self, elements: list[UIElement]) -> None:
            nonlocal scrolls
            del elements
            scrolls += 1

    automator = NewLayoutAutomator(
        serial="device-1",
        work_dir=tmp_path,
        sleep=no_sleep,
    )

    assert await automator.navigate_to_chat_backup() is True
    assert automator.layout_mode is WhatsAppLayout.PROFILE_TAB
    assert taps == [
        "Anda",
        "com.whatsapp:id/settings_chat",
        "com.whatsapp:id/chat_backup_preference",
    ]
    assert scrolls == 1


@pytest.mark.asyncio
async def test_old_layout_navigation_remains_overflow_settings_path(
    tmp_path: Path,
) -> None:
    screens = iter(
        [
            _screen(
                _ui(
                    "com.whatsapp:id/conversations_row_contact_name",
                    bounds=(80, 420, 620, 520),
                ),
                _ui(
                    "com.whatsapp:id/menuitem_overflow",
                    content_desc="More options",
                    bounds=(960, 80, 1080, 220),
                ),
            ),
            _screen(
                _ui(text="New group"),
                _ui(text="Settings", bounds=(600, 900, 1040, 1040)),
            ),
            _screen(
                _ui("com.whatsapp:id/settings_nested_scroll_view"),
                _ui(
                    "com.whatsapp:id/settings_chat",
                    content_desc="Chats,Chat history, backup",
                    bounds=(20, 850, 1060, 1030),
                ),
            ),
            _screen(
                _ui("com.whatsapp:id/enter_key_preference"),
                _ui(
                    "com.whatsapp:id/chat_backup_preference",
                    content_desc="Chat backup",
                    bounds=(20, 1350, 1060, 1530),
                ),
            ),
            _screen(_ui("com.whatsapp:id/google_drive_backup_now_btn")),
        ]
    )
    taps: list[str] = []

    async def no_sleep(_seconds: float) -> None:
        return None

    class OldLayoutAutomator(WhatsAppUiAutomator):
        async def launch_whatsapp(self) -> None:
            return None

        async def _activity_top_dump(self) -> str:
            return ""

        async def dump_hierarchy(self) -> list[UIElement]:
            return next(screens)

        async def tap_element(
            self,
            element: UIElement,
            delay: float = 1.5,
        ) -> None:
            del delay
            taps.append(element.resource_id or element.content_desc or element.text)

    automator = OldLayoutAutomator(
        serial="device-1",
        work_dir=tmp_path,
        sleep=no_sleep,
    )

    assert await automator.navigate_to_chat_backup() is True
    assert automator.layout_mode is WhatsAppLayout.OVERFLOW_MENU
    assert taps == [
        "com.whatsapp:id/menuitem_overflow",
        "Settings",
        "com.whatsapp:id/settings_chat",
        "com.whatsapp:id/chat_backup_preference",
    ]


def test_encrypted_backup_state_supports_english_and_indonesian() -> None:
    disabled = _ui(
        "com.whatsapp:id/settings_gdrive_e2e_encryption",
        content_desc="Cadangan terenkripsi end-to-end,Nonaktif",
        bounds=(20, 1500, 1060, 1680),
    )
    enabled = _ui(
        "com.whatsapp:id/settings_gdrive_e2e_encryption",
        content_desc="End-to-end encrypted backup,On · 64-digit key",
        bounds=(20, 1500, 1060, 1680),
    )

    assert (
        WhatsAppUiAutomator._encrypted_backup_state(_screen(disabled), disabled)
        is WhatsAppE2eState.DISABLED
    )
    assert (
        WhatsAppUiAutomator._encrypted_backup_state(_screen(enabled), enabled)
        is WhatsAppE2eState.ENABLED
    )


@pytest.mark.asyncio
async def test_existing_encryption_is_replaced_with_new_64_digit_key(
    tmp_path: Path,
) -> None:
    encrypted_on = _ui(
        "com.whatsapp:id/settings_gdrive_e2e_encryption",
        content_desc="Cadangan terenkripsi end-to-end,Nyala · Kunci enkripsi 64 digit",
        bounds=(20, 1500, 1060, 1680),
    )
    encrypted_off = _ui(
        "com.whatsapp:id/settings_gdrive_e2e_encryption",
        content_desc="Cadangan terenkripsi end-to-end,Nonaktif",
        bounds=(20, 1500, 1060, 1680),
    )
    chunks = [_ui(text=f"{value:04x}") for value in range(16)]
    screens = iter(
        [
            _screen(encrypted_on),
            _screen(
                _ui("com.whatsapp:id/enc_backup_enabled_landing_disable_button")
            ),
            _screen(_ui("com.whatsapp:id/enc_backup_encryption_key_input_forgot")),
            _screen(_ui("com.whatsapp:id/confirm_disable_disable_button")),
            _screen(_ui("com.whatsapp:id/disable_done_done_button")),
            _screen(encrypted_off),
            _screen(encrypted_off),
            _screen(_ui("com.whatsapp:id/enable_info_more_options_button")),
            _screen(_ui("com.whatsapp:id/enc_backup_more_options_encryption_key")),
            _screen(_ui("com.whatsapp:id/encryption_key_info_bottom_button")),
            _screen(
                *chunks,
                _ui(
                    "com.whatsapp:id/encryption_key_info_bottom_button",
                    text="LANJUT",
                ),
            ),
            _screen(_ui("com.whatsapp:id/encryption_key_confirm_button_confirm")),
            _screen(_ui("com.whatsapp:id/enable_done_create_button")),
            _screen(encrypted_on),
        ]
    )
    taps: list[str] = []

    async def no_sleep(_seconds: float) -> None:
        return None

    class EncryptionAutomator(WhatsAppUiAutomator):
        async def dump_hierarchy(self) -> list[UIElement]:
            return next(screens)

        async def tap_element(
            self,
            element: UIElement,
            delay: float = 1.5,
        ) -> None:
            del delay
            taps.append(element.resource_id or element.text)

    automator = EncryptionAutomator(
        serial="device-1",
        work_dir=tmp_path,
        sleep=no_sleep,
    )

    key = await automator.setup_or_extract_64digit_key()

    assert key == "".join(f"{value:04x}" for value in range(16))
    assert "com.whatsapp:id/enc_backup_enabled_landing_disable_button" in taps
    assert "com.whatsapp:id/enc_backup_encryption_key_input_forgot" in taps
    assert "com.whatsapp:id/confirm_disable_disable_button" in taps
    assert "com.whatsapp:id/enc_backup_more_options_encryption_key" in taps
    assert "com.whatsapp:id/encryption_key_confirm_button_confirm" in taps
    assert "com.whatsapp:id/enable_done_create_button" in taps


def test_hierarchy_detects_phone_registration_and_ignores_logged_in_chats() -> None:
    assert hierarchy_shows_unsigned_in(
        [_ui("com.whatsapp:id/registration_phone", "phone")]
    )
    assert hierarchy_shows_unsigned_in(
        [_ui(text="Selamat datang di WhatsApp")]
    )
    assert hierarchy_shows_unsigned_in(
        [_ui(text="AGREE AND CONTINUE")]
    )
    assert hierarchy_shows_unsigned_in(
        [_ui(text="Masukkan nomor telepon Anda")]
    )
    assert not hierarchy_shows_unsigned_in(
        [
            _ui("com.whatsapp:id/menuitem_overflow", content_desc="Opsi lainnya"),
            _ui(text="Grup baru"),
            _ui(text="Perangkat tertaut"),
        ]
    )
    assert not hierarchy_shows_unsigned_in(
        [_ui("com.whatsapp:id/google_drive_backup_now_btn", "CADANGKAN")]
    )


def test_activity_dump_detects_registration_funnel() -> None:
    assert activity_shows_unsigned_in(
        "mResumedActivity: ActivityRecord{abc com.whatsapp/.registration.RegisterPhone t7}"
    )
    assert activity_shows_unsigned_in(
        "ACTIVITY com.whatsapp/.registration.EULA 123\n"
    )
    assert not activity_shows_unsigned_in(
        "mResumedActivity: ActivityRecord{abc com.whatsapp/.HomeActivity t7}"
    )


@pytest.mark.asyncio
async def test_unsigned_in_does_not_retry_ui_attempts(tmp_path: Path) -> None:
    calls = 0

    async def no_sleep(_seconds: float) -> None:
        return None

    class UnsignedIn(WhatsAppUiAutomator):
        async def _single_attempt(self) -> WhatsAppBackupArtifact:
            nonlocal calls
            calls += 1
            raise WhatsAppNotSignedInError

    automator = UnsignedIn(serial="device-1", work_dir=tmp_path, sleep=no_sleep)

    async def on_progress(*_args, **_fields) -> None:
        return None

    with pytest.raises(WhatsAppNotSignedInError):
        await automator.acquire_backup(on_progress=on_progress)

    assert calls == 1


@pytest.mark.asyncio
async def test_unsigned_in_is_fail_soft_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def package_installed(_self) -> bool:
        return True

    async def acquire_backup(_self, **_kwargs) -> WhatsAppBackupArtifact:
        raise WhatsAppNotSignedInError

    monkeypatch.setattr(WhatsAppUiAutomator, "package_installed", package_installed)
    monkeypatch.setattr(WhatsAppUiAutomator, "acquire_backup", acquire_backup)
    events: list[dict] = []

    async def on_progress(*_args, **fields) -> None:
        events.append(fields)

    result = await WhatsAppBackupAcquisitionService().acquire(
        serial="device-1",
        staging=tmp_path,
        mode=AcquisitionMode.QUICK,
        on_progress=on_progress,
    )

    assert result is None
    assert events[-1]["whatsapp_state"] == "not_signed_in"


@pytest.mark.asyncio
async def test_navigate_raises_unsigned_in_before_tapping(
    tmp_path: Path,
) -> None:
    taps = 0

    async def no_sleep(_seconds: float) -> None:
        return None

    class RegistrationScreen(WhatsAppUiAutomator):
        async def launch_whatsapp(self) -> None:
            return None

        async def _activity_top_dump(self) -> str:
            return ""

        async def dump_hierarchy(self) -> list[UIElement]:
            return [_ui("com.whatsapp:id/registration_phone", "Enter your phone number")]

        async def tap_element(self, element: UIElement, delay: float = 1.5) -> None:
            nonlocal taps
            taps += 1
            del element, delay

    automator = RegistrationScreen(serial="device-1", work_dir=tmp_path, sleep=no_sleep)

    with pytest.raises(WhatsAppNotSignedInError):
        await automator.navigate_to_chat_backup()

    assert taps == 0


@pytest.mark.asyncio
async def test_parser_failure_is_fail_soft_after_successful_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "msgstore.db.crypt15"
    backup.write_bytes(b"invalid")

    async def package_installed(_self) -> bool:
        return True

    async def acquire_backup(_self, **_kwargs) -> WhatsAppBackupArtifact:
        return WhatsAppBackupArtifact(backup, "cd" * 32, 2)

    def fail_decrypt(*_args, **_kwargs) -> None:
        raise WhatsAppParseError("format berubah")

    monkeypatch.setattr(WhatsAppUiAutomator, "package_installed", package_installed)
    monkeypatch.setattr(WhatsAppUiAutomator, "acquire_backup", acquire_backup)
    monkeypatch.setattr(
        "app.acquisition.whatsapp_backup.decrypt_crypt15",
        fail_decrypt,
    )
    events: list[dict] = []

    async def on_progress(*_args, **fields) -> None:
        events.append(fields)

    result = await WhatsAppBackupAcquisitionService().acquire(
        serial="device-1",
        staging=tmp_path,
        mode=AcquisitionMode.QUICK,
        on_progress=on_progress,
    )

    assert result is not None
    assert result.state == "parse_unavailable"
    assert result.ui_attempts == 2
    assert events[-1]["whatsapp_state"] == "parse_unavailable"


@pytest.mark.asyncio
async def test_dispatch_appends_native_whatsapp_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_provider(_self, _context) -> AcquisitionResult:
        return AcquisitionResult(
            staging,
            3,
            10.0,
            "android_agent",
            ProviderKind.ANDROID_AGENT,
        )

    async def fake_whatsapp(self, **_kwargs) -> WhatsAppAcquisitionResult:
        del self
        return WhatsAppAcquisitionResult(2, 1, 0, 3, 25.0, "complete")

    monkeypatch.setattr(
        "app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire",
        fake_provider,
    )
    monkeypatch.setattr(
        WhatsAppBackupAcquisitionService,
        "acquire",
        fake_whatsapp,
    )
    monkeypatch.setattr(settings, "android_recovery_enabled", False)
    monkeypatch.setattr(settings, "gmail_acquisition_enabled", False)
    monkeypatch.setattr(settings, "browser_history_enabled", False)

    async def on_progress(*_args, **_kwargs) -> None:
        return None

    result = await acquisition_service.acquire_dispatch(
        session_id="session-wa-dispatch",
        device_id="device-wa",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
    )

    assert result == (staging, 5, 35.0, "android_agent+whatsapp_crypt15")


@pytest.mark.asyncio
async def test_dispatch_does_not_swallow_ui_automation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_provider(_self, _context) -> AcquisitionResult:
        return AcquisitionResult(staging, 1, 1.0, "android", ProviderKind.ANDROID_LEGACY)

    async def fail_whatsapp(self, **_kwargs):
        del self
        raise AcquisitionError(
            ErrorCategory.ACCESS_TIMEOUT,
            "UI automator WhatsApp gagal setelah 4 percobaan penuh.",
        )

    monkeypatch.setattr(
        "app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire",
        fake_provider,
    )
    monkeypatch.setattr(
        WhatsAppBackupAcquisitionService,
        "acquire",
        fail_whatsapp,
    )
    monkeypatch.setattr(settings, "android_recovery_enabled", False)
    monkeypatch.setattr(settings, "gmail_acquisition_enabled", False)
    monkeypatch.setattr(settings, "browser_history_enabled", False)

    async def on_progress(*_args, **_kwargs) -> None:
        return None

    with pytest.raises(AcquisitionError, match="4 percobaan"):
        await acquisition_service.acquire_dispatch(
            session_id="session-wa-strict",
            device_id="device-wa",
            device_type=DeviceType.ANDROID,
            simulated=False,
            mode=AcquisitionMode.FULL,
            scenario=Scenario.LULUS,
            file_count=0,
            on_progress=on_progress,
        )


@pytest.mark.asyncio
async def test_dispatch_skips_unsigned_in_whatsapp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_provider(_self, _context) -> AcquisitionResult:
        return AcquisitionResult(staging, 3, 10.0, "android_agent", ProviderKind.ANDROID_AGENT)

    async def package_installed(_self) -> bool:
        return True

    async def acquire_backup(_self, **_kwargs) -> WhatsAppBackupArtifact:
        raise WhatsAppNotSignedInError

    monkeypatch.setattr(
        "app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire",
        fake_provider,
    )
    monkeypatch.setattr(WhatsAppUiAutomator, "package_installed", package_installed)
    monkeypatch.setattr(WhatsAppUiAutomator, "acquire_backup", acquire_backup)
    monkeypatch.setattr(settings, "android_recovery_enabled", False)
    monkeypatch.setattr(settings, "gmail_acquisition_enabled", False)
    monkeypatch.setattr(settings, "browser_history_enabled", False)

    events: list[dict] = []

    async def on_progress(*_args, **fields) -> None:
        events.append(fields)

    result = await acquisition_service.acquire_dispatch(
        session_id="session-wa-unsigned",
        device_id="device-wa",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
    )

    assert result == (staging, 3, 10.0, "android_agent")
    assert any(event.get("whatsapp_state") == "not_signed_in" for event in events)


@pytest.mark.asyncio
async def test_canonical_message_runs_analysis_gallery_and_report_pipeline(
    client,
) -> None:
    del client
    session_id = "session-whatsapp-pipeline"
    now = utcnow()
    await db.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_by,
            review_candidates, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "android-whatsapp",
            "android",
            "WhatsApp pipeline",
            "quick",
            "lulus",
            "completed",
            json.dumps(
                {
                    "phase": "completed",
                    "percent": 100,
                    "message": "Selesai",
                    "whatsapp_state": "complete",
                    "whatsapp_ui_attempt": 1,
                    "whatsapp_ui_attempts": 4,
                    "whatsapp_messages": 1,
                    "whatsapp_conversations": 1,
                }
            ),
            json.dumps({}),
            "MENUNGGU REVIEW",
            None,
            "admin",
            0,
            now,
            now,
        ),
    )
    staging = settings.staging_dir / session_id
    secret = staging / "_whatsapp" / "wa_64digit.key"
    secret.parent.mkdir(parents=True)
    secret.write_text("ef" * 32)
    canonical = {
        "schema_version": 1,
        "kind": "whatsapp_message",
        "source": "whatsapp",
        "record_id": "message-opaque-1",
        "album": "WhatsApp",
        "display_name": "Ruang Uji",
        "preview_text": "rencana rahasia malam ini",
        "normalized_text": "Ruang Uji\n+628100000000\nrencana rahasia malam ini",
        "captured_at": "2026-08-20T12:30:00+00:00",
        "source_created_at": "2026-08-20T12:30:00+00:00",
        "conversation": {
            "id": "conversation-opaque-1",
            "name": "Ruang Uji",
            "address": "+628100000000",
            "type": "group",
        },
        "message": {
            "id": "message-opaque-1",
            "direction": "IN",
            "sender": "+628122222222",
            "type": "text",
            "timestamp": "2026-08-20T12:30:00+00:00",
            "starred": True,
            "revoked": False,
            "forward_score": 1,
            "edited_at": None,
            "quote": {"sender": "+628133333333", "text": "pesan awal"},
            "media": {
                "filename": "evidence.jpg",
                "file_size": 4,
                "mime_type": "image/jpeg",
                "caption": None,
                "source_path": "/Media/evidence.jpg",
                "duration": 0,
            },
        },
    }
    target = (
        staging
        / "whatsapp"
        / "conversation-opaque-1"
        / "message-opaque-1.whatsapp-message.json"
    )
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(canonical), encoding="utf-8")
    media_target = staging / "media_image" / "evidence.jpg"
    media_target.parent.mkdir(parents=True)
    media_target.write_bytes(b"jpeg")

    async def on_progress(*_args, **_kwargs) -> None:
        return None

    indexed, _duration = await index_staging(session_id, staging, on_progress)
    assert indexed == 2
    rows = await db.fetchall(
        "SELECT * FROM files WHERE session_id = ?",
        (session_id,),
    )
    assert len(rows) == 2
    canonical_row = next(row for row in rows if row["mime"] == WHATSAPP_MESSAGE_MIME)
    media_row = next(row for row in rows if row["mime"] == "image/jpeg")
    assert "_whatsapp" not in canonical_row["path"]
    metadata = json.loads(canonical_row["meta_json"])
    assert metadata["conversation_id"] == "conversation-opaque-1"
    assert metadata["quoted_text"] == "pesan awal"
    media_metadata = json.loads(media_row["meta_json"])
    assert media_metadata["source_app"] == "com.whatsapp"
    assert media_metadata["source_app_inferred"] is False
    assert media_metadata["whatsapp_message_id"] == "message-opaque-1"

    preview = await read_preview(target, WHATSAPP_MESSAGE_MIME)
    assert preview == canonical["preview_text"]
    outcome = analyze_content_result(
        target,
        WHATSAPP_MESSAGE_MIME,
        "whatsapp",
        preview,
        ["rahasia"],
    )
    assert any(finding["label"] == "Indikasi: rahasia" for finding in outcome.findings)

    file_id = str(media_row["id"])
    await db.execute(
        """
        INSERT INTO findings (
            id, session_id, file_id, source, path, category, label,
            confidence, layer_origin, evidence, review_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "finding-whatsapp-1",
            session_id,
            file_id,
            "media_image",
            media_row["path"],
            "incitement",
            "Indikasi: rahasia",
            0.91,
            "L2",
            "rencana rahasia malam ini",
            "pending",
            now,
        ),
    )

    gallery = await list_items(
        session_id,
        AcquisitionMode.QUICK,
        "whatsapp",
        1,
        50,
    )
    assert gallery.total == 2
    assert gallery.pagination_total == 1
    item = next(entry for entry in gallery.items if entry.presentation == "chat")
    linked_media = next(entry for entry in gallery.items if entry.whatsapp_media is not None)
    assert item.presentation == "chat"
    assert item.chat is not None
    assert item.chat.conversation_name == "Ruang Uji"
    assert item.chat.direction == "IN"
    assert item.flagged is True
    assert item.finding_badges
    assert linked_media.whatsapp_media is not None
    assert linked_media.whatsapp_media.conversation_id == "conversation-opaque-1"

    report = await build_session_report(session_id)
    assert report["whatsapp_data"]["total_messages"] == 1
    assert report["whatsapp_rooms"][0]["messages"][0]["flagged"] is True
    html = report_to_html(report)
    assert "Percakapan WhatsApp" in html
    assert "Ruang Uji" in html
    assert "Temuan" in html
