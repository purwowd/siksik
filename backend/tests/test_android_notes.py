from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.acquisition.android_notes.contracts import (
    ANDROID_NOTE_MIME,
    NoteApp,
    NotesAcquisitionResult,
    NotesFlow,
    NotesPolicy,
    NotesState,
    RemoteExport,
)
from app.acquisition.android_notes.extractors import (
    GenericNotesExtractor,
    SamsungNotesExtractor,
    _parse_export,
)
from app.acquisition.android_notes.gateway import (
    AdbNotesGateway,
    parse_foreground_package,
)
from app.acquisition.android_notes.service import (
    AndroidNotesAcquisitionService,
    build_notes_policy,
)
from app.acquisition.android_notes.ui import (
    looks_like_editor,
    looks_like_note_list,
    note_cards,
    parse_note_timestamp,
    parse_ui,
)
from app.acquisition.analysis_plan import build_analysis_plan
from app.acquisition.contracts import AcquisitionResult, ProviderKind
from app.acquisition.process import ProcessResult
from app.core import config
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, DeviceType, Scenario
from app.services import acquisition as acquisition_service
from app.services.acquisition import index_staging
from app.services.analysis import analyze_session, read_preview
from app.services.gallery import ACCESS_ALL, list_items
from app.services.finding_modules import MODULE_SOURCE_SQL, VALID_MODULE_IDS


def _list_xml() -> str:
    return """
    <hierarchy>
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
        <node resource-id="app:id/note_card" class="android.view.ViewGroup" clickable="true" bounds="[40,180][1040,480]">
          <node text="Rencana rapat" class="android.widget.TextView" bounds="[80,210][900,280]" />
          <node text="20 Agustus 2026 10:30" class="android.widget.TextView" bounds="[80,300][600,350]" />
        </node>
        <node resource-id="app:id/note_card" class="android.view.ViewGroup" clickable="true" bounds="[40,520][1040,820]">
          <node text="Catatan lama" class="android.widget.TextView" bounds="[80,550][900,620]" />
          <node text="1 April 2026" class="android.widget.TextView" bounds="[80,640][600,700]" />
        </node>
      </node>
    </hierarchy>
    """


def _editor_xml(body: str) -> str:
    return f"""
    <hierarchy>
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
        <node resource-id="app:id/title" text="Rencana rapat" class="android.widget.EditText" bounds="[60,180][1020,300]" />
        <node resource-id="app:id/content" text="{body}" class="android.widget.EditText" bounds="[60,320][1020,1500]" />
        <node resource-id="app:id/modified_time" text="20 Agustus 2026 10:30" class="android.widget.TextView" bounds="[60,1540][800,1600]" />
      </node>
    </hierarchy>
    """


def _miui_list_xml() -> str:
    return """
    <hierarchy>
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node resource-id="com.miui.notes:id/action_search" text="Cari catatan" class="android.widget.ImageButton" clickable="true" bounds="[870,80][1030,220]" />
        <node resource-id="com.miui.notes:id/recycler_view" class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,220][1080,2180]">
          <node resource-id="com.miui.notes:id/note_group" class="android.view.ViewGroup" clickable="true" bounds="[40,300][1040,680]">
            <node resource-id="com.miui.notes:id/note_title" text="Rencana pemeriksaan" class="android.widget.TextView" bounds="[80,340][920,420]" />
            <node resource-id="com.miui.notes:id/note_content" text="agenda pertama" class="android.widget.TextView" bounds="[80,440][920,540]" />
            <node resource-id="com.miui.notes:id/modified_time" text="20 Agustus 2026 10:30" class="android.widget.TextView" bounds="[80,570][700,630]" />
          </node>
        </node>
        <node resource-id="com.miui.notes:id/note_add" content-desc="Tambah catatan" class="android.widget.ImageButton" clickable="true" bounds="[880,2150][1040,2310]" />
      </node>
    </hierarchy>
    """


def _miui_editor_xml() -> str:
    return """
    <hierarchy>
      <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
        <node resource-id="com.miui.notes:id/action_back" content-desc="Kembali" class="android.widget.ImageButton" clickable="true" bounds="[20,80][180,220]" />
        <node resource-id="com.miui.notes:id/note_title" text="Rencana pemeriksaan" class="android.widget.EditText" bounds="[60,260][1020,420]" />
        <node resource-id="com.miui.notes:id/rich_editor" text="agenda pertama" class="com.miui.notes.editor.NoteRichEditor" bounds="[60,440][1020,2100]" />
      </node>
    </hierarchy>
    """


class FakeNotesGateway:
    def __init__(self) -> None:
        self.app = NoteApp(
            "com.google.android.keep",
            "Google Keep",
            "com.google.android.keep/.activities.BrowseActivity",
            NotesFlow.UI_WALK,
        )
        self.state = "list"
        self.editor_page = 0
        self.editor_opens = 0
        self.restored = False

    async def detect_apps(self):
        return (self.app,)

    async def launch(self, app):
        self.state = "list"
        return app == self.app

    def last_failure_reason(self):
        return None

    async def adopt_export_surface(self):
        return True

    async def restore_agent(self):
        self.restored = True

    async def dump_ui(self, max_bytes):
        assert max_bytes > 0
        if self.state == "list":
            return _list_xml()
        return _editor_xml("agenda pertama" if self.editor_page == 0 else "agenda lanjutan")

    async def screen_size(self):
        return (1080, 1920)

    async def tap(self, x, y):
        if self.state == "list" and y < 500:
            self.state = "editor"
            self.editor_page = 0
            self.editor_opens += 1
        return True

    async def long_press(self, x, y, duration_ms=900):
        return True

    async def swipe(self, start, end, duration_ms):
        if self.state == "editor":
            self.editor_page += 1
        return True

    async def back(self):
        self.state = "list"
        return True

    async def settle(self, seconds):
        return None

    async def list_exports(self):
        return ()

    async def pull_export(self, remote, destination, timeout_s):
        return False


class FakeSamsungGateway:
    def __init__(self) -> None:
        self.app = NoteApp(
            "com.samsung.android.app.notes",
            "Samsung Notes",
            "com.samsung.android.app.notes/.memolist.MemoListActivity",
            NotesFlow.SAMSUNG_EXPORT,
        )
        self.state = "list"
        self.actions: list[str] = []

    async def detect_apps(self):
        return (self.app,)

    async def launch(self, app):
        self.state = "list"
        return app == self.app

    def last_failure_reason(self):
        return None

    async def adopt_export_surface(self):
        return True

    async def restore_agent(self):
        return None

    async def dump_ui(self, max_bytes):
        label_by_state = {
            "list": ("Menu", "Rencana pemeriksaan"),
            "drawer": ("Semua catatan",),
            "selection": ("Pilih semua",),
            "selected": ("2 catatan dipilih", "Lainnya"),
            "menu": ("Simpan sebagai file",),
            "format": ("Samsung Notes file",),
            "picker": ("Selesai",),
            "saved": ("Rencana pemeriksaan",),
        }
        labels = label_by_state[self.state]
        nodes = []
        for index, label in enumerate(labels):
            resource = "note_card" if "Rencana" in label else f"action_{index}"
            nodes.append(
                f'<node resource-id="app:id/{resource}" text="{label}" '
                f'class="android.view.ViewGroup" clickable="true" '
                f'bounds="[40,{120 + index * 240}][1040,{320 + index * 240}]" />'
            )
        return f"<hierarchy><node bounds=\"[0,0][1080,1920]\">{''.join(nodes)}</node></hierarchy>"

    async def screen_size(self):
        return (1080, 1920)

    async def tap(self, x, y):
        transitions = {
            "list": "drawer",
            "drawer": "list",
            "selection": "selected",
            "selected": "menu",
            "menu": "format",
            "format": "picker",
            "picker": "saved",
        }
        self.actions.append(f"tap:{self.state}")
        self.state = transitions.get(self.state, self.state)
        return True

    async def long_press(self, x, y, duration_ms=900):
        self.actions.append("long_press:list")
        self.state = "selection"
        return True

    async def swipe(self, start, end, duration_ms):
        return True

    async def back(self):
        self.state = "list"
        return True

    async def settle(self, seconds):
        return None

    async def list_exports(self):
        if self.state == "saved":
            return (RemoteExport("/sdcard/Documents/note.sdocx", 512, 100),)
        return ()

    async def pull_export(self, remote, destination, timeout_s):
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(
                "note.xml",
                "<document><title>Rencana pemeriksaan</title><text>indikasi makar</text><date>20 Agustus 2026 10:30</date></document>",
            )
        return True

    async def cleanup_export(self, remote):
        return True


class FakeAdbNotesTransport:
    def __init__(
        self,
        foreground_package: str | None,
        *,
        ui_package: str = "com.samsung.android.app.notes",
    ) -> None:
        self.foreground_package = foreground_package
        self.ui_package = ui_package
        self.inputs: list[tuple[str, ...]] = []
        self.started: list[str] = []

    async def start_activity(self, _serial, component, _extras, **_kwargs):
        self.started.append(component)

    async def run(self, _serial, args, **_kwargs):
        command = tuple(args)
        if command in {
            ("shell", "cmd", "activity", "get-resumed-activity"),
            ("shell", "dumpsys", "activity", "activities"),
        }:
            if self.foreground_package is None:
                return ProcessResult(command, 0, "", "")
            output = (
                "mResumedActivity: ActivityRecord{abc u0 "
                f"{self.foreground_package}/.MainActivity t7}}"
            )
            return ProcessResult(command, 0, output, "")
        if command[:3] == ("shell", "uiautomator", "dump"):
            return ProcessResult(command, 0, "UI hierarchy dumped", "")
        if command[:2] == ("exec-out", "cat"):
            output = (
                '<hierarchy><node package="'
                f"{self.ui_package}"
                '" class="android.widget.FrameLayout" '
                'bounds="[0,0][1080,1920]" /></hierarchy>'
            )
            return ProcessResult(command, 0, output, "")
        if command[:2] == ("shell", "input"):
            self.inputs.append(command)
        return ProcessResult(command, 0, "", "")


def _policy() -> NotesPolicy:
    return NotesPolicy(
        mode=AcquisitionMode.QUICK,
        not_before=datetime(2026, 5, 28, tzinfo=timezone.utc),
        max_notes=20,
        max_list_scrolls=3,
        max_editor_scrolls=4,
        timeout_s=30.0,
        max_note_chars=20_000,
        max_export_file_bytes=1024 * 1024,
        max_export_bytes=4 * 1024 * 1024,
        max_ui_bytes=1024 * 1024,
    )


def _samsung_app() -> NoteApp:
    return NoteApp(
        "com.samsung.android.app.notes",
        "Samsung Notes",
        "com.samsung.android.app.notes/.memolist.MemoListActivity",
        NotesFlow.SAMSUNG_EXPORT,
    )


def _generic_app() -> NoteApp:
    return NoteApp(
        "com.google.android.keep",
        "Google Keep",
        "com.google.android.keep/.activities.BrowseActivity",
        NotesFlow.UI_WALK,
    )


@pytest.mark.unit
def test_notes_ui_parser_and_time_scope() -> None:
    snapshot = parse_ui(_list_xml())
    assert len(snapshot.nodes) == 8
    parsed = parse_note_timestamp(
        "20 Agustus 2026 10:30",
        datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert parsed == datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc)
    assert "notes" in VALID_MODULE_IDS
    assert "LOWER(f.source) = 'notes'" in MODULE_SOURCE_SQL["notes"][0]


@pytest.mark.unit
def test_miui_list_excludes_actions_and_does_not_masquerade_as_editor() -> None:
    snapshot = parse_ui(_miui_list_xml())

    cards = note_cards(snapshot, (1080, 2400))

    assert [card.resource_id for card in cards] == ["com.miui.notes:id/note_group"]
    assert looks_like_note_list(snapshot, (1080, 2400)) is True
    assert looks_like_editor(snapshot) is False
    assert looks_like_editor(parse_ui(_miui_editor_xml())) is True


@pytest.mark.unit
def test_notes_policy_separates_quick_and_full_windows_and_budgets() -> None:
    reference = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
    quick = build_notes_policy(AcquisitionMode.QUICK, reference=reference)
    full = build_notes_policy(AcquisitionMode.FULL, reference=reference)
    assert quick.not_before == datetime(2026, 5, 28, 12, tzinfo=timezone.utc)
    assert full.not_before == datetime(2026, 2, 28, 12, tzinfo=timezone.utc)
    assert quick.max_notes < full.max_notes
    assert quick.max_list_scrolls < full.max_list_scrolls
    assert quick.max_export_bytes < full.max_export_bytes


@pytest.mark.unit
def test_notes_foreground_parser_reads_resumed_activity_output() -> None:
    assert (
        parse_foreground_package(
            "mResumedActivity: ActivityRecord{abc u0 "
            "com.twitter.android/.StartActivity t7}"
        )
        == "com.twitter.android"
    )
    assert (
        parse_foreground_package(
            "noise com.untrusted.example/.Noise\n"
            "topResumedActivity=ActivityRecord{abc u0 "
            "com.samsung.android.app.notes/.memolist.MemoListActivity t8}"
        )
        == "com.samsung.android.app.notes"
    )
    assert parse_foreground_package("Activity task state unavailable") is None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "app",
    (_samsung_app(), _generic_app()),
    ids=("samsung_export", "non_samsung_ui_walk"),
)
async def test_notes_gateway_rejects_launch_while_x_remains_foreground(
    app: NoteApp,
) -> None:
    transport = FakeAdbNotesTransport("com.twitter.android")
    gateway = AdbNotesGateway(
        "serial-fixture",
        transport,  # type: ignore[arg-type]
        foreground_attempts=1,
        foreground_poll_s=0,
    )

    assert await gateway.launch(app) is False
    assert gateway.last_failure_reason() == "notes_foreground_mismatch"
    assert await gateway.tap(100, 200) is False
    assert transport.inputs == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notes_gateway_stops_input_after_foreground_changes() -> None:
    transport = FakeAdbNotesTransport("com.samsung.android.app.notes")
    gateway = AdbNotesGateway(
        "serial-fixture",
        transport,  # type: ignore[arg-type]
        foreground_attempts=1,
        foreground_poll_s=0,
    )
    assert await gateway.launch(_samsung_app()) is True

    transport.foreground_package = "com.twitter.android"

    assert await gateway.swipe((500, 1400), (500, 500), 450) is False
    assert gateway.last_failure_reason() == "notes_foreground_mismatch"
    assert transport.inputs == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notes_gateway_rejects_ui_dump_from_another_package() -> None:
    transport = FakeAdbNotesTransport(
        "com.samsung.android.app.notes",
        ui_package="com.twitter.android",
    )
    gateway = AdbNotesGateway(
        "serial-fixture",
        transport,  # type: ignore[arg-type]
        foreground_attempts=1,
        foreground_poll_s=0,
    )
    assert await gateway.launch(_samsung_app()) is True

    assert await gateway.dump_ui(1024 * 1024) == ""
    assert gateway.last_failure_reason() == "notes_ui_surface_mismatch"
    assert transport.inputs == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notes_gateway_allows_only_adopted_system_export_surface() -> None:
    transport = FakeAdbNotesTransport("com.samsung.android.app.notes")
    gateway = AdbNotesGateway(
        "serial-fixture",
        transport,  # type: ignore[arg-type]
        foreground_attempts=1,
        foreground_poll_s=0,
    )
    assert await gateway.adopt_export_surface() is False
    assert gateway.last_failure_reason() == "notes_export_surface_unrecognized"
    assert await gateway.launch(_samsung_app()) is True
    transport.foreground_package = "com.android.documentsui"

    assert await gateway.adopt_export_surface() is True
    assert await gateway.tap(100, 200) is True
    assert transport.inputs == [("shell", "input", "tap", "100", "200")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_notes_walk_filters_old_cards_and_reads_deep_content() -> None:
    gateway = FakeNotesGateway()
    result = await GenericNotesExtractor(gateway).extract(gateway.app, _policy())
    assert result.state == NotesState.COMPLETE
    assert len(result.records) == 1
    assert result.skipped == 1
    assert gateway.editor_opens == 1
    assert result.records[0].title == "Rencana rapat"
    assert "agenda pertama" in result.records[0].body
    assert "agenda lanjutan" in result.records[0].body
    assert result.records[0].source_modified_at == "2026-08-20T10:30:00Z"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_notes_walk_retries_card_until_editor_transition() -> None:
    class DelayedEditorGateway(FakeNotesGateway):
        def __init__(self) -> None:
            super().__init__()
            self.tap_attempts = 0
            self.back_calls = 0

        async def tap(self, x, y):
            if self.state == "list" and y < 500:
                self.tap_attempts += 1
                if self.tap_attempts == 4:
                    self.state = "editor"
                    self.editor_page = 0
                    self.editor_opens += 1
            return True

        async def back(self):
            self.back_calls += 1
            return await super().back()

    gateway = DelayedEditorGateway()

    result = await GenericNotesExtractor(gateway).extract(gateway.app, _policy())

    assert len(result.records) == 1
    assert gateway.tap_attempts == 4
    assert gateway.editor_opens == 1
    assert gateway.back_calls == 1
    assert "notes_editor_unrecognized" not in result.warnings


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_notes_walk_does_not_back_out_when_tap_stays_on_list() -> None:
    class NoOpTapGateway(FakeNotesGateway):
        def __init__(self) -> None:
            super().__init__()
            self.tap_attempts = 0
            self.back_calls = 0

        async def dump_ui(self, max_bytes):
            assert max_bytes > 0
            return _miui_list_xml()

        async def screen_size(self):
            return (1080, 2400)

        async def tap(self, x, y):
            self.tap_attempts += 1
            return True

        async def back(self):
            self.back_calls += 1
            return await super().back()

    gateway = NoOpTapGateway()

    result = await GenericNotesExtractor(gateway).extract(gateway.app, _policy())

    assert result.records == ()
    assert result.skipped == 1
    assert result.warnings == ("notes_editor_unrecognized",)
    assert gateway.tap_attempts == 4
    assert gateway.back_calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_notes_walk_stops_after_foreground_loss() -> None:
    class ForegroundLossGateway(FakeNotesGateway):
        def __init__(self) -> None:
            super().__init__()
            self.failure: str | None = None
            self.inputs: list[str] = []

        async def launch(self, app):
            self.failure = None
            return await super().launch(app)

        def last_failure_reason(self):
            return self.failure

        async def dump_ui(self, max_bytes):
            if self.state == "list":
                return await super().dump_ui(max_bytes)
            self.failure = "notes_foreground_mismatch"
            return ""

        async def tap(self, x, y):
            self.inputs.append("tap")
            return await super().tap(x, y)

        async def swipe(self, start, end, duration_ms):
            self.inputs.append("swipe")
            return await super().swipe(start, end, duration_ms)

        async def back(self):
            self.inputs.append("back")
            return await super().back()

    gateway = ForegroundLossGateway()

    result = await GenericNotesExtractor(gateway).extract(gateway.app, _policy())

    assert result.flow == NotesFlow.UI_WALK
    assert result.state == NotesState.PARTIAL
    assert result.records == ()
    assert result.warnings == ("notes_foreground_mismatch",)
    assert gateway.inputs == ["tap"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_samsung_notes_flow_exports_selected_notes_as_sdocx() -> None:
    gateway = FakeSamsungGateway()
    result = await SamsungNotesExtractor(gateway).extract(gateway.app, _policy())
    assert result.state == NotesState.COMPLETE
    assert result.flow == NotesFlow.SAMSUNG_EXPORT
    assert len(result.records) == 1
    assert result.records[0].extraction_method == "android_notes_samsung_export"
    assert gateway.actions == [
        "tap:list",
        "tap:drawer",
        "long_press:list",
        "tap:selection",
        "tap:selected",
        "tap:menu",
        "tap:format",
        "tap:picker",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_samsung_notes_indonesian_one_ui_labels_and_sdcard_export() -> None:
    class IndonesianOneUiSamsungGateway(FakeSamsungGateway):
        def __init__(self) -> None:
            super().__init__()

        async def dump_ui(self, max_bytes):
            if self.state == "list":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.samsung.android.app.notes:id/drawer_button" content-desc="Laci navigasi" '
                    'class="android.widget.ImageView" clickable="true" bounds="[36,112][132,208]" />'
                    '<node resource-id="com.samsung.android.app.notes:id/root_cardview" text="Catatan Penting" '
                    'class="android.widget.FrameLayout" clickable="true" bounds="[48,320][1032,600]">'
                    '<node text="Catatan Penting" class="android.widget.TextView" bounds="[72,344][900,420]" />'
                    '<node text="Isi rahasia" class="android.widget.TextView" bounds="[72,430][900,520]" />'
                    '</node>'
                    '</node></hierarchy>'
                )
            elif self.state == "drawer":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.samsung.android.app.notes:id/all_notes" text="Semua catatan" '
                    'class="android.widget.TextView" clickable="true" bounds="[48,400][600,520]" />'
                    '</node></hierarchy>'
                )
            elif self.state == "selection":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.samsung.android.app.notes:id/checkbox_withtext" text="Semua" '
                    'class="android.widget.CheckBox" clickable="true" bounds="[48,120][280,220]" />'
                    '</node></hierarchy>'
                )
            elif self.state == "selected":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.samsung.android.app.notes:id/check_info" text="1 dipilih" '
                    'class="android.widget.TextView" bounds="[48,120][300,220]" />'
                    '<node resource-id="com.samsung.android.app.notes:id/more_options" content-desc="Opsi lainnya" '
                    'class="android.widget.ImageView" clickable="true" bounds="[948,120][1044,220]" />'
                    '</node></hierarchy>'
                )
            elif self.state == "menu":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.samsung.android.app.notes:id/title" text="Spn sbg file" '
                    'class="android.widget.TextView" clickable="true" bounds="[500,240][980,360]" />'
                    '</node></hierarchy>'
                )
            elif self.state == "format":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.samsung.android.app.notes:id/item_title" text="File Samsung Notes" '
                    'class="android.widget.TextView" clickable="true" bounds="[100,800][980,940]" />'
                    '</node></hierarchy>'
                )
            elif self.state == "picker":
                return (
                    '<hierarchy><node bounds="[0,0][1080,2400]">'
                    '<node resource-id="com.sec.android.app.myfiles:id/menu_done" text="Selesai" '
                    'class="android.widget.Button" clickable="true" bounds="[800,2100][1020,2240]" />'
                    '</node></hierarchy>'
                )
            return '<hierarchy><node bounds="[0,0][1080,2400]"></node></hierarchy>'

        async def list_exports(self):
            if self.state == "saved":
                return (RemoteExport("/sdcard/001_Pwd.sdocx", 1024, 200),)
            return ()

    gateway = IndonesianOneUiSamsungGateway()
    result = await SamsungNotesExtractor(gateway).extract(gateway.app, _policy())
    assert result.state == NotesState.COMPLETE
    assert result.flow == NotesFlow.SAMSUNG_EXPORT
    assert len(result.records) == 1
    assert result.records[0].title == "Rencana pemeriksaan"
    assert gateway.actions == [
        "tap:list",
        "tap:drawer",
        "long_press:list",
        "tap:selection",
        "tap:selected",
        "tap:menu",
        "tap:format",
        "tap:picker",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_samsung_notes_drawer_recovers_via_back_when_all_notes_missing() -> None:
    class MissingAllNotesGateway(FakeSamsungGateway):
        def __init__(self) -> None:
            super().__init__()
            self.back_calls = 0

        async def dump_ui(self, max_bytes):
            if self.state == "drawer":
                # Drawer is open but All Notes item is missing
                return '<hierarchy><node bounds="[0,0][1080,1920]"><node text="Lain-lain" clickable="true" bounds="[40,120][600,240]" /></node></hierarchy>'
            return await super().dump_ui(max_bytes)

        async def back(self):
            self.back_calls += 1
            return await super().back()

    gateway = MissingAllNotesGateway()
    result = await SamsungNotesExtractor(gateway).extract(gateway.app, _policy())
    assert result.state == NotesState.COMPLETE
    assert gateway.back_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_samsung_notes_export_failure_does_not_trigger_generic_scroll(
    tmp_path: Path,
) -> None:
    class FailingSamsungExportGateway(FakeSamsungGateway):
        def __init__(self) -> None:
            super().__init__()
            self.swipes: list[str] = []

        async def list_exports(self):
            # Never produces export file -> export fails with notes_export_not_found
            return ()

        async def swipe(self, start, end, duration_ms):
            self.swipes.append("swipe")
            return await super().swipe(start, end, duration_ms)

    gateway = FailingSamsungExportGateway()
    service = AndroidNotesAcquisitionService(lambda _serial: gateway)

    async def _progress(*_args, **_kwargs):
        pass

    result = await service.acquire(
        session_id="samsung-no-scroll-fallback",
        serial="serial-fixture",
        staging=tmp_path,
        mode=AcquisitionMode.QUICK,
        simulated=False,
        on_progress=_progress,
        request_id="request-fixture",
        reference=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )

    assert result.item_count == 0
    # Must NOT execute any swipe/scroll commands
    assert gateway.swipes == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notes_service_does_not_fallback_after_foreground_mismatch(
    tmp_path: Path,
) -> None:
    class ForegroundMismatchGateway(FakeSamsungGateway):
        def __init__(self) -> None:
            super().__init__()
            self.launch_calls = 0
            self.restored = False

        async def launch(self, _app):
            self.launch_calls += 1
            return False

        def last_failure_reason(self):
            return "notes_foreground_mismatch"

        async def restore_agent(self):
            self.restored = True

    gateway = ForegroundMismatchGateway()
    service = AndroidNotesAcquisitionService(lambda _serial: gateway)
    progress_updates: list[dict[str, object]] = []

    async def on_progress(*_args, **kwargs):
        progress_updates.append(kwargs)

    result = await service.acquire(
        session_id="notes-foreground-mismatch",
        serial="serial-fixture",
        staging=tmp_path,
        mode=AcquisitionMode.QUICK,
        simulated=False,
        on_progress=on_progress,
        request_id="request-fixture",
        reference=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )

    assert result.state == NotesState.UNAVAILABLE
    assert result.item_count == 0
    assert result.warnings == ("notes_foreground_mismatch",)
    assert gateway.launch_calls == 1
    assert gateway.actions == []
    assert gateway.restored is True
    assert progress_updates[0]["crawl_state"] is None
    assert progress_updates[0]["crawl_source"] is None
    assert progress_updates[0]["crawl_target"] is None


@pytest.mark.unit
def test_samsung_sdocx_parser_is_bounded_and_extracts_note(tmp_path: Path) -> None:
    path = tmp_path / "sample.sdocx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "note.xml",
            "<document><title>Agenda pemeriksaan</title><text>indikasi makar</text><date>20 Agustus 2026 10:30</date></document>",
        )
    app = NoteApp(
        "com.samsung.android.app.notes",
        "Samsung Notes",
        "com.samsung.android.app.notes/.memolist.MemoListActivity",
        NotesFlow.SAMSUNG_EXPORT,
    )
    records = _parse_export(path, app, _policy())
    assert len(records) == 1
    assert "Agenda pemeriksaan" in records[0].normalized_text
    assert "indikasi makar" in records[0].normalized_text
    assert records[0].source_modified_at == "2026-08-20T10:30:00Z"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_notes_service_persists_canonical_records(tmp_path: Path) -> None:
    gateway = FakeNotesGateway()
    service = AndroidNotesAcquisitionService(lambda _serial: gateway)

    async def on_progress(*_args, **_kwargs):
        return None

    result = await service.acquire(
        session_id="notes-service-test",
        serial="test-device",
        staging=tmp_path,
        mode=AcquisitionMode.QUICK,
        simulated=False,
        on_progress=on_progress,
        request_id="request-test",
        reference=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    assert result.item_count == 1
    assert result.method == "android_notes_ui_walk"
    assert gateway.restored is True
    stored = list((tmp_path / "notes").glob("*.json"))
    assert len(stored) == 1
    payload = json.loads(stored[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "android_note"
    assert payload["time_scope_months"] == 3
    assert "agenda lanjutan" in payload["normalized_text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acquire_dispatch_adds_notes_to_android_provider_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    async def fake_provider(_self, _context):
        return AcquisitionResult(
            staging,
            4,
            10.0,
            "android_agent",
            ProviderKind.ANDROID_AGENT,
        )

    async def fake_notes(self, **_kwargs):
        return NotesAcquisitionResult(
            2,
            15.0,
            "android_notes_ui_walk",
            NotesState.COMPLETE,
            NotesFlow.UI_WALK,
            "com.google.android.keep",
            "Google Keep",
        )

    async def fake_reference(_session_id):
        return datetime(2026, 8, 28, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire",
        fake_provider,
    )
    monkeypatch.setattr(
        AndroidNotesAcquisitionService,
        "acquire",
        fake_notes,
    )
    monkeypatch.setattr(
        "app.acquisition.gmail_oauth.session_acquisition_reference",
        fake_reference,
    )
    monkeypatch.setattr(config.settings, "android_notes_enabled", True)
    monkeypatch.setattr(config.settings, "android_recovery_enabled", False)
    monkeypatch.setattr(config.settings, "gmail_acquisition_enabled", False)
    monkeypatch.setattr(config.settings, "browser_history_enabled", False)

    async def on_progress(*_args, **_kwargs):
        return None

    result = await acquisition_service.acquire_dispatch(
        session_id="notes-dispatch-test",
        device_id="android-notes-test",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
        analysis_plan=build_analysis_plan(
            scope="device",
            device_sources=["notes"],
        ),
    )
    assert result == (
        staging,
        6,
        25.0,
        "android_agent+android_notes_ui_walk",
    )


@pytest.mark.asyncio
async def test_notes_index_analysis_preview_and_gallery_contract(client, tmp_path: Path) -> None:
    session_id = "session-android-notes-001"
    now = utcnow()
    await db.execute(
        "INSERT INTO sessions (id, device_id, device_type, label, mode, scenario, "
        "status, progress_json, timing_json, recommendation, error, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            "android-test",
            "android",
            "Notes integration",
            "quick",
            "lulus",
            "indexing",
            json.dumps(
                {
                    "phase": "indexing",
                    "percent": 50,
                    "message": "Indexing",
                    "analysis_scope": "device",
                    "device_sources": ["notes"],
                    "social_targets": [],
                }
            ),
            "{}",
            None,
            None,
            now,
            now,
        ),
    )
    staging = tmp_path / session_id
    directory = staging / "notes"
    directory.mkdir(parents=True)
    note_path = directory / "record.json"
    note_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "android_note",
                "record_id": "a" * 64,
                "source": "notes",
                "source_app": "com.google.android.keep",
                "source_app_label": "Google Keep",
                "title": "Agenda",
                "body": "indikasi makar",
                "normalized_text": "Agenda\nindikasi makar",
                "preview_text": "Agenda indikasi makar",
                "display_name": "Agenda",
                "album": "Catatan",
                "observed_at": "2026-08-20T10:30:00Z",
                "captured_at": "2026-08-20T10:30:00Z",
                "source_modified_at": "2026-08-20T10:30:00Z",
                "extraction_method": "android_notes_ui_walk",
                "analysis_eligible": True,
                "time_scope_months": 3,
            }
        ),
        encoding="utf-8",
    )

    async def on_progress(*_args, **_kwargs):
        return None

    indexed, _ = await index_staging(session_id, staging, on_progress)
    assert indexed == 1
    row = await db.fetchone(
        "SELECT source, mime, meta_json FROM files WHERE session_id = ?",
        (session_id,),
    )
    assert row is not None
    assert row["source"] == "notes"
    assert row["mime"] == ANDROID_NOTE_MIME
    metadata = json.loads(row["meta_json"])
    assert metadata["artifact_role"] == "canonical_note"
    assert metadata["album"] == "Catatan"
    preview = await read_preview(note_path, ANDROID_NOTE_MIME)
    assert preview == "Agenda\nindikasi makar"
    analyzed, findings, *_ = await analyze_session(
        session_id,
        staging,
        AcquisitionMode.QUICK,
        on_progress,
    )
    assert analyzed == 1
    assert findings > 0
    gallery = await list_items(
        session_id,
        AcquisitionMode.QUICK,
        ACCESS_ALL,
        1,
        20,
    )
    assert len(gallery.items) == 1
    assert gallery.items[0].album == "Catatan"
    assert gallery.items[0].presentation == "text"
    assert gallery.items[0].preview_text == "Agenda indikasi makar"
    assert gallery.items[0].source_path == "Catatan/Agenda"
    assert gallery.items[0].flagged is True
