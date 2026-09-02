from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.acquisition.adb import AsyncAdbTransport, validate_component_name
from app.acquisition.android_notes.contracts import (
    NoteApp,
    NotesFlow,
    RemoteExport,
)
from app.acquisition.android_notes.ui import parse_ui
from app.acquisition.errors import AcquisitionError
from app.core.config import settings

SCREEN_SIZE_RE = re.compile(
    r"(?:Physical|Override) size:\s*(\d+)x(\d+)",
    re.IGNORECASE,
)
REMOTE_EXPORT_RE = re.compile(
    r"^/sdcard/[^\r\n\x00]{1,900}\.(?:sdocx|txt)$",
    re.IGNORECASE,
)
COMPONENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]+/[A-Za-z0-9_.$]+$")
FOREGROUND_COMPONENT_RE = re.compile(
    r"(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9_.]{1,254})/[A-Za-z0-9_.$]+"
)
FOREGROUND_LINE_MARKERS = (
    "topResumedActivity",
    "mResumedActivity",
    "ResumedActivity",
    "mCurrentFocus",
    "mFocusedApp",
)
EXPORT_SURFACE_PACKAGES = frozenset(
    {
        "com.android.documentsui",
        "com.google.android.documentsui",
        "com.sec.android.app.myfiles",
        "com.samsung.android.app.notes",
    }
)
KNOWN_APPS = (
    ("com.samsung.android.app.notes", "Samsung Notes", NotesFlow.SAMSUNG_EXPORT),
    ("com.google.android.keep", "Google Keep", NotesFlow.UI_WALK),
    ("com.miui.notes", "Mi Notes", NotesFlow.UI_WALK),
    ("com.coloros.note", "ColorOS Notes", NotesFlow.UI_WALK),
    ("com.coloros.note2", "ColorOS Notes", NotesFlow.UI_WALK),
    ("com.oplus.note", "OPPO Notes", NotesFlow.UI_WALK),
    ("com.realme.note", "realme Notes", NotesFlow.UI_WALK),
    ("com.vivo.notes", "vivo Notes", NotesFlow.UI_WALK),
    ("com.huawei.notepad", "Huawei Notepad", NotesFlow.UI_WALK),
    ("com.android.notes", "Android Notes", NotesFlow.UI_WALK),
    ("com.transsion.notebook", "Notebook", NotesFlow.UI_WALK),
    ("com.socialnmobile.dictapps.notepad.color.note", "ColorNote", NotesFlow.UI_WALK),
    ("com.microsoft.office.onenote", "Microsoft OneNote", NotesFlow.UI_WALK),
    ("com.simplemobiletools.notes.pro", "Simple Notes", NotesFlow.UI_WALK),
)
EXPORT_ROOTS = ("/sdcard/", "/sdcard/Documents", "/sdcard/Download", "/sdcard/Samsung")


class AdbNotesGateway:
    def __init__(
        self,
        serial: str,
        transport: AsyncAdbTransport,
        *,
        agent_component: str | None = None,
        foreground_attempts: int = 20,
        foreground_poll_s: float = 0.4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if foreground_attempts < 1 or not 0 <= foreground_poll_s <= 5.0:
            raise ValueError("Invalid Notes foreground verification policy")
        self._serial = serial
        self._transport = transport
        self._agent_component = agent_component or settings.android_agent_component
        self._foreground_attempts = foreground_attempts
        self._foreground_poll_s = foreground_poll_s
        self._sleep = sleep
        self._active_package: str | None = None
        self._export_surface_package: str | None = None
        self._last_failure: str | None = None
        digest = hashlib.sha256(f"notes-ui:{serial}".encode("utf-8")).hexdigest()[:16]
        self._dump_path = f"/data/local/tmp/siksik_notes_{digest}.xml"

    async def detect_apps(self) -> tuple[NoteApp, ...]:
        detected: list[NoteApp] = []
        known_packages = {package for package, _label, _flow in KNOWN_APPS}
        for package, label, flow in KNOWN_APPS:
            if not await self._transport.package_exists(self._serial, package):
                continue
            component = await self._resolve_component(package)
            if component is not None:
                detected.append(NoteApp(package, label, component, flow))
        if detected:
            return tuple(detected)
        result = await self._transport.run(
            self._serial,
            ["shell", "pm", "list", "packages", "-3"],
            operation="android_notes_package_list",
            check=False,
        )
        for line in result.stdout.splitlines():
            package = line.removeprefix("package:").strip()
            lowered = package.casefold()
            if package in known_packages or not any(
                token in lowered for token in ("note", "memo", "notepad")
            ):
                continue
            component = await self._resolve_component(package)
            if component is None:
                continue
            label = package.rsplit(".", 1)[-1].replace("_", " ").strip().title() or "Catatan"
            detected.append(NoteApp(package, label[:80], component, NotesFlow.UI_WALK))
            if len(detected) >= 4:
                break
        return tuple(detected)

    async def _resolve_component(self, package: str) -> str | None:
        if package == "com.samsung.android.app.notes":
            memo_target = "com.samsung.android.app.notes/.memolist.MemoListActivity"
            memo_check = await self._transport.run(
                self._serial,
                [
                    "shell",
                    "cmd",
                    "package",
                    "resolve-activity",
                    "--brief",
                    "--user",
                    "0",
                    memo_target,
                ],
                operation="android_notes_memo_activity_resolve",
                check=False,
            )
            for line in reversed(memo_check.stdout.splitlines()):
                candidate = line.strip()
                if candidate == memo_target or (
                    COMPONENT_RE.fullmatch(candidate)
                    and candidate.startswith("com.samsung.android.app.notes/")
                ):
                    try:
                        return validate_component_name(candidate, package)
                    except AcquisitionError:
                        break

        result = await self._transport.run(
            self._serial,
            [
                "shell",
                "cmd",
                "package",
                "resolve-activity",
                "--brief",
                "--user",
                "0",
                package,
            ],
            operation="android_notes_activity_resolve",
            check=False,
        )
        for line in reversed(result.stdout.splitlines()):
            candidate = line.strip()
            if not COMPONENT_RE.fullmatch(candidate):
                continue
            try:
                return validate_component_name(candidate, package)
            except AcquisitionError:
                continue
        return None

    async def launch(self, app: NoteApp) -> bool:
        self._active_package = None
        self._export_surface_package = None
        self._last_failure = None
        try:
            await self._transport.start_activity(
                self._serial,
                app.component,
                {},
                timeout=20.0,
            )
        except AcquisitionError:
            self._last_failure = "notes_launch_failed"
            return False
        for attempt in range(self._foreground_attempts):
            observed = await self._foreground_package()
            if observed == app.package_name:
                self._active_package = app.package_name
                self._last_failure = None
                return True
            if attempt + 1 < self._foreground_attempts:
                await self._sleep(self._foreground_poll_s)
        self._last_failure = (
            "notes_foreground_unavailable"
            if observed is None
            else "notes_foreground_mismatch"
        )
        return False

    def last_failure_reason(self) -> str | None:
        return self._last_failure

    async def adopt_export_surface(self) -> bool:
        if self._active_package is None:
            self._last_failure = "notes_export_surface_unrecognized"
            return False
        observed: str | None = None
        for attempt in range(6):
            observed = await self._foreground_package()
            if observed == self._active_package:
                self._export_surface_package = None
                self._last_failure = None
                return True
            if observed in EXPORT_SURFACE_PACKAGES:
                self._export_surface_package = observed
                self._last_failure = None
                return True
            if attempt + 1 < 6:
                await self._sleep(0.3)
        self._last_failure = (
            "notes_foreground_unavailable"
            if observed is None
            else "notes_export_surface_unrecognized"
        )
        return False

    async def restore_agent(self) -> None:
        self._active_package = None
        self._export_surface_package = None
        try:
            await self._transport.start_activity(
                self._serial,
                self._agent_component,
                {},
                timeout=15.0,
            )
        except AcquisitionError:
            return

    async def dump_ui(self, max_bytes: int) -> str:
        before = await self._allowed_foreground_package()
        if before is None:
            return ""
        try:
            dumped = await self._transport.run(
                self._serial,
                ["shell", "uiautomator", "dump", "--compressed", self._dump_path],
                operation="android_notes_ui_dump",
                timeout=15.0,
                check=False,
            )
            if dumped.returncode != 0:
                self._last_failure = "notes_ui_dump_failed"
                return ""
            output = await self._transport.run(
                self._serial,
                ["exec-out", "cat", self._dump_path],
                operation="android_notes_ui_read",
                timeout=15.0,
                check=False,
            )
            if output.returncode != 0 or output.output_truncated:
                self._last_failure = "notes_ui_read_failed"
                return ""
            encoded = output.stdout.encode("utf-8", errors="replace")
            if len(encoded) > max_bytes:
                self._last_failure = "notes_ui_dump_oversized"
                return ""
            after = await self._allowed_foreground_package()
            if after is None or after != before:
                self._last_failure = "notes_foreground_changed"
                return ""
            snapshot = parse_ui(output.stdout)
            if not snapshot.nodes or after not in snapshot.package_names():
                self._last_failure = "notes_ui_surface_mismatch"
                return ""
            self._last_failure = None
            return output.stdout
        finally:
            await self._transport.run(
                self._serial,
                ["shell", "rm", "-f", self._dump_path],
                operation="android_notes_ui_cleanup",
                timeout=10.0,
                check=False,
            )

    async def screen_size(self) -> tuple[int, int]:
        result = await self._transport.run(
            self._serial,
            ["shell", "wm", "size"],
            operation="android_notes_screen_size",
            check=False,
        )
        matches = list(SCREEN_SIZE_RE.finditer(result.stdout))
        if not matches:
            return (1080, 1920)
        width, height = (int(value) for value in matches[-1].groups())
        if not 240 <= width <= 10_000 or not 320 <= height <= 20_000:
            return (1080, 1920)
        return width, height

    async def tap(self, x: int, y: int) -> bool:
        return await self._input(["tap", str(max(0, x)), str(max(0, y))])

    async def long_press(self, x: int, y: int, duration_ms: int = 900) -> bool:
        duration = min(max(duration_ms, 300), 3_000)
        return await self._input(
            [
                "swipe",
                str(max(0, x)),
                str(max(0, y)),
                str(max(0, x)),
                str(max(0, y)),
                str(duration),
            ]
        )

    async def swipe(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        duration_ms: int,
    ) -> bool:
        duration = min(max(duration_ms, 100), 3_000)
        return await self._input(
            [
                "swipe",
                str(max(0, start[0])),
                str(max(0, start[1])),
                str(max(0, end[0])),
                str(max(0, end[1])),
                str(duration),
            ]
        )

    async def back(self) -> bool:
        return await self._input(["keyevent", "4"])

    async def _input(self, args: list[str]) -> bool:
        if await self._allowed_foreground_package() is None:
            return False
        result = await self._transport.run(
            self._serial,
            ["shell", "input", *args],
            operation="android_notes_ui_input",
            timeout=10.0,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}".casefold()
        denied = any(
            token in output
            for token in ("securityexception", "inject_events", "permission denied")
        )
        if result.returncode != 0 or denied:
            self._last_failure = "notes_ui_input_denied"
            return False
        after = await self._allowed_foreground_package()
        if after is None:
            self._last_failure = "notes_foreground_changed"
            return False
        self._last_failure = None
        return True

    async def settle(self, seconds: float) -> None:
        await self._sleep(min(max(seconds, 0.0), 3.0))

    async def _allowed_foreground_package(self) -> str | None:
        observed = await self._foreground_package()
        allowed = {
            package
            for package in (self._active_package, self._export_surface_package)
            if package is not None
        }
        if observed in allowed:
            return observed
        self._last_failure = (
            "notes_foreground_unavailable"
            if observed is None
            else "notes_foreground_mismatch"
        )
        return None

    async def _foreground_package(self) -> str | None:
        probes = (
            (
                ["shell", "cmd", "activity", "get-resumed-activity"],
                "android_notes_foreground_probe",
            ),
            (
                ["shell", "dumpsys", "activity", "activities"],
                "android_notes_foreground_fallback",
            ),
        )
        for args, operation in probes:
            try:
                result = await self._transport.run(
                    self._serial,
                    args,
                    operation=operation,
                    timeout=10.0,
                    check=False,
                )
            except AcquisitionError:
                continue
            package = parse_foreground_package(f"{result.stdout}\n{result.stderr}")
            if package is not None:
                return package
        return None

    async def list_exports(self) -> tuple[RemoteExport, ...]:
        paths: set[str] = set()
        for root in EXPORT_ROOTS:
            depth = "2" if root == "/sdcard/" else "4"
            for suffix in ("*.sdocx", "*.txt"):
                result = await self._transport.run(
                    self._serial,
                    [
                        "shell",
                        "find",
                        root,
                        "-maxdepth",
                        depth,
                        "-type",
                        "f",
                        "-iname",
                        suffix,
                    ],
                    operation="android_notes_export_list",
                    timeout=20.0,
                    check=False,
                )
                for line in result.stdout.splitlines():
                    candidate = line.strip()
                    if REMOTE_EXPORT_RE.fullmatch(candidate):
                        paths.add(candidate)
                    if len(paths) >= 2_048:
                        break
                if len(paths) >= 2_048:
                    break
            if len(paths) >= 2_048:
                break
        exports: list[RemoteExport] = []
        for path in sorted(paths):
            result = await self._transport.run(
                self._serial,
                ["shell", "stat", "-c", "%s,%Y", path],
                operation="android_notes_export_stat",
                timeout=10.0,
                check=False,
            )
            size: int | None = None
            modified: int | None = None
            if result.returncode == 0:
                tokens = result.stdout.strip().split(",", 1)
                if len(tokens) == 2:
                    try:
                        size = max(0, int(tokens[0]))
                        modified = max(0, int(tokens[1]))
                    except ValueError:
                        size = None
                        modified = None
            exports.append(RemoteExport(path, size, modified))
        return tuple(exports)

    async def pull_export(
        self,
        remote: RemoteExport,
        destination: Path,
        timeout_s: float,
    ) -> bool:
        if REMOTE_EXPORT_RE.fullmatch(remote.path) is None:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = await self._transport.run(
            self._serial,
            ["pull", remote.path, str(destination)],
            operation="android_notes_export_pull",
            timeout=timeout_s,
            check=False,
        )
        return result.returncode == 0 and destination.is_file()

    async def cleanup_export(self, remote: RemoteExport) -> bool:
        if REMOTE_EXPORT_RE.fullmatch(remote.path) is None:
            return False
        result = await self._transport.run(
            self._serial,
            ["shell", "rm", "-f", remote.path],
            operation="android_notes_export_cleanup",
            timeout=10.0,
            check=False,
        )
        return result.returncode == 0


def parse_foreground_package(value: str) -> str | None:
    lines = value.splitlines()
    prioritized = [
        line
        for marker in FOREGROUND_LINE_MARKERS
        for line in lines
        if marker in line
    ]
    candidates = prioritized or (
        [line for line in lines if line.strip()] if len(lines) <= 4 else []
    )
    for line in candidates:
        match = FOREGROUND_COMPONENT_RE.search(line)
        if match is not None:
            return match.group(1)
    return None
