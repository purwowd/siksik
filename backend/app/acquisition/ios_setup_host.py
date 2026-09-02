"""USB / go-ios / AltServer transport for iOS preflight (no operator TTY)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Protocol

from app.acquisition.errors import AcquisitionError, ErrorCategory, acquisition_error
from app.acquisition.ios_setup_log import redact_text, safe_argv, write_setup_ios_log
from app.acquisition.process import run_process
from app.core.config import settings

logger = logging.getLogger("siksik.acquisition.ios_setup")

# Status poll hits these every ~2s; keep setup_ios.log for install/tunnel/fail.
_QUIET_OK = frozenset(
    {
        "ios_pair_validate",
        "ios_list_apps_usb",
        "ios_list_apps_windows",
        "ios_usbipd_list",
        "ios_devmode_get",
    }
)

# AltServer / Apple ID resign replaces com.facebook with the signing-team prefix.
WDA_BUNDLE_RE = re.compile(
    r"(?:[A-Za-z0-9-]+\.)+WebDriverAgentRunner(?:\.[A-Za-z0-9._-]+)*"
)
UNTRUSTED_RE = re.compile(
    r"deviceprocesscontrolservice|Error code: 2|could not get pid|Untrusted|not verified",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^([^@\s]+)@([^@\s]+\.[^@\s]+)$")
LOCKDOWN_RE = re.compile(
    r"could not connect to lockdownd|mux error \(-8\)|lockdownd, error code",
    re.IGNORECASE,
)


def _tool_env() -> dict[str, str]:
    home_bin = str(Path.home() / ".local" / "bin")
    path = os.environ.get("PATH", "")
    merged = f"{home_bin}:{path}" if home_bin not in path.split(":") else path
    return {**os.environ, "PATH": merged}


def _puller_root() -> Path:
    return settings.ios_media_puller_path.resolve()


def _tunnel_port() -> str:
    return os.environ.get("GO_IOS_TUNNEL_INFO_PORT", "60105")


def mask_apple_id(raw: str | None) -> str | None:
    value = (raw or "").strip()
    match = EMAIL_RE.match(value)
    if match is None:
        return None
    local, domain = match.group(1), match.group(2)
    if not local:
        return None
    return f"{local[0]}***@{domain}"


def parse_puller_env() -> dict[str, str]:
    path = _puller_root() / ".env"
    parsed: dict[str, str] = {}
    if not path.is_file():
        return parsed
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return parsed
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            parsed[key] = value
    return parsed


def is_lockdown_error(blob: str) -> bool:
    return bool(LOCKDOWN_RE.search(blob or ""))


_WINDOWS_WDA_LOG_OK = re.compile(
    r"Installation Succeeded|WDA terdeteksi|WDA sudah terpasang",
    re.IGNORECASE,
)
_WINDOWS_WDA_LOG_FAIL = re.compile(r"Install WDA gagal \(exit", re.IGNORECASE)
_WINDOWS_WDA_LOG_MAX_AGE_S = 12 * 3600


def windows_install_log_shows_wda(text: str) -> bool:
    last_ok = -1
    last_fail = -1
    for index, line in enumerate((text or "").splitlines()):
        if _WINDOWS_WDA_LOG_OK.search(line):
            last_ok = index
        if _WINDOWS_WDA_LOG_FAIL.search(line):
            last_fail = index
    return last_ok >= 0 and last_ok > last_fail


def usbipd_apple_attached_to_wsl(blob: str) -> bool:
    for line in (blob or "").splitlines():
        lower = line.lower()
        if "05ac:12a8" not in lower and "05ac:12ab" not in lower:
            continue
        parts = line.split()
        if parts and parts[-1] == "Attached":
            return True
    return False


def extract_wda_bundle(blob: str) -> str | None:
    match = WDA_BUNDLE_RE.search(blob)
    return match.group(0) if match else None


class IosSetupTransport(Protocol):
    async def pair_validate(self, udid: str) -> bool: ...
    async def pair_request(self, udid: str) -> None: ...
    async def list_wda_bundle(self, udid: str, *, use_tunnel: bool = False) -> str | None: ...
    async def developer_mode_enabled(self, udid: str) -> bool | None: ...
    async def reveal_developer_mode(self, udid: str) -> None: ...
    async def ensure_tunnel(self, udid: str) -> None: ...
    async def install_ipa(self, udid: str, ipa: Path) -> bool: ...
    async def launch_bundle(self, udid: str, bundle: str) -> str: ...
    def resolve_ipa(self) -> Path | None: ...
    def resolve_altserver(self) -> Path | None: ...
    def apple_credentials(self) -> tuple[str, str] | None: ...
    def apple_id_hint(self) -> str | None: ...
    def uses_windows_wda_install(self) -> bool: ...
    def windows_wda_present_from_log(self, *, since_unix: float | None = None) -> bool: ...
    def invalidate_usb_location(self) -> None: ...
    async def restore_usb_to_wsl(self) -> None: ...
    def hold_usb_on_windows(self) -> None: ...
    async def release_usb_to_windows(self) -> None: ...
    async def wda_http_ready(self) -> bool: ...
    def stack_udid_matches(self, udid: str) -> bool: ...
    async def start_altserver(
        self, udid: str, ipa: Path, apple_id: str, password: str
    ) -> asyncio.subprocess.Process: ...
    async def start_windows_wda_install(
        self, udid: str, ipa: Path
    ) -> asyncio.subprocess.Process: ...


class LiveIosSetupHost:
    def __init__(self) -> None:
        self._mux_lock = asyncio.Lock()
        self._pair_udid: str | None = None
        self._pair_ok = False
        self._pair_ok_until = 0.0
        self._pair_lockdown = False
        self._usb_wsl: bool | None = None
        self._usb_wsl_until = 0.0

    def _invalidate_pair(self) -> None:
        self._pair_ok = False
        self._pair_ok_until = 0.0

    def pair_lockdown_stale(self) -> bool:
        return self._pair_lockdown

    def invalidate_usb_location(self) -> None:
        self._usb_wsl = None
        self._usb_wsl_until = 0.0

    async def restore_usb_to_wsl(self) -> None:
        from app.acquisition.ios_usb_wsl import ensure_iphone_on_wsl

        self.invalidate_usb_location()
        await ensure_iphone_on_wsl(force=True)

    def hold_usb_on_windows(self) -> None:
        from app.acquisition.ios_usbmux import mark_iphone_usb_windows

        mark_iphone_usb_windows()
        self.invalidate_usb_location()

    async def release_usb_to_windows(self) -> None:
        self.hold_usb_on_windows()
        script = _puller_root() / "ios_automator" / "scripts" / "release_iphone_windows.sh"
        if not script.is_file():
            return
        try:
            await self._run(
                ["bash", str(script)],
                timeout=20.0,
                operation="ios_usb_release_windows",
                cwd=_puller_root(),
            )
        except AcquisitionError:
            write_setup_ios_log(
                "WARN",
                "ios_usb_release_windows",
                detail="detach usbipd skipped",
            )

    def windows_wda_present_from_log(self, *, since_unix: float | None = None) -> bool:
        path = Path("/mnt/c/Users/Admin/wda/install-wda.log")
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False
        now = time.time()
        if now - mtime > _WINDOWS_WDA_LOG_MAX_AGE_S:
            return False
        if since_unix is not None and mtime < since_unix:
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-200_000:]
        except OSError:
            return False
        return windows_install_log_shows_wda(text)

    async def _run(
        self,
        argv: list[str],
        *,
        timeout: float,
        operation: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        udid: str | None = None,
    ):
        verbose = operation not in _QUIET_OK
        if verbose:
            write_setup_ios_log(
                "INFO",
                operation,
                detail=f"begin timeout_s={int(timeout)} argv={safe_argv(argv, udid=udid)}",
                udid=udid,
            )

        async def _on_line(line: str) -> None:
            if not verbose:
                return
            cleaned = redact_text(line.rstrip(), udid=udid).strip()
            if cleaned:
                write_setup_ios_log("CMD", operation, detail=cleaned, udid=udid)

        try:
            result = await run_process(
                argv,
                timeout=timeout,
                cwd=cwd,
                env=env or _tool_env(),
                check=False,
                output_limit_bytes=256 * 1024,
                operation=operation,
                on_stdout_line=_on_line,
                not_found_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
                timeout_category=ErrorCategory.ADB_TIMEOUT,
                failure_category=ErrorCategory.DEPENDENCY_NOT_FOUND,
            )
        except AcquisitionError as exc:
            write_setup_ios_log(
                "ERROR",
                operation,
                detail=exc.public_message,
                udid=udid,
            )
            raise
        err = redact_text((result.stderr or "").strip(), udid=udid)
        if verbose and err:
            write_setup_ios_log("CMD", f"{operation}.stderr", detail=err[:4000], udid=udid)
        if verbose or result.returncode != 0:
            write_setup_ios_log(
                "INFO" if result.returncode == 0 else "WARN",
                operation,
                detail=f"exit={result.returncode}",
                udid=udid,
            )
        return result

    async def pair_validate(self, udid: str) -> bool:
        now = time.monotonic()
        if self._pair_udid == udid and now < self._pair_ok_until:
            return self._pair_ok
        async with self._mux_lock:
            now = time.monotonic()
            if self._pair_udid == udid and now < self._pair_ok_until:
                return self._pair_ok
            result = await self._run(
                ["idevicepair", "-u", udid, "validate"],
                timeout=8.0,
                operation="ios_pair_validate",
                udid=udid,
            )
            ok = result.returncode == 0
            blob = f"{result.stdout or ''}\n{result.stderr or ''}"
            self._pair_udid = udid
            self._pair_ok = ok
            self._pair_lockdown = (not ok) and is_lockdown_error(blob)
            self._pair_ok_until = now + (8.0 if ok else 4.0)
            return ok

    async def pair_request(self, udid: str) -> None:
        await self._run(
            ["idevicepair", "-u", udid, "pair"],
            timeout=12.0,
            operation="ios_pair_request",
            udid=udid,
        )

    async def apple_usb_attached_to_wsl(self) -> bool:
        now = time.monotonic()
        if self._usb_wsl is not None and now < self._usb_wsl_until:
            return self._usb_wsl
        try:
            result = await self._run(
                ["usbipd.exe", "list"],
                timeout=5.0,
                operation="ios_usbipd_list",
            )
        except AcquisitionError:
            self._usb_wsl = True
            self._usb_wsl_until = now + 4.0
            return True
        attached = usbipd_apple_attached_to_wsl(
            f"{result.stdout or ''}\n{result.stderr or ''}"
        )
        self._usb_wsl = attached
        self._usb_wsl_until = now + 4.0
        return attached

    async def _list_wda_windows(self, udid: str) -> str | None:
        script = _puller_root() / "ios_automator" / "scripts" / "check_wda_windows.sh"
        if not script.is_file():
            return None
        try:
            result = await self._run(
                ["bash", str(script), udid],
                timeout=20.0,
                operation="ios_list_apps_windows",
                cwd=_puller_root(),
                udid=udid,
            )
        except AcquisitionError:
            return None
        return extract_wda_bundle(f"{result.stdout or ''}\n{result.stderr or ''}")

    async def _launch_windows(self, udid: str, bundle: str) -> str:
        script = _puller_root() / "ios_automator" / "scripts" / "launch_wda_windows.sh"
        if not script.is_file():
            return "error"
        try:
            result = await self._run(
                ["bash", str(script), udid, bundle],
                timeout=20.0,
                operation="ios_launch_wda_windows",
                cwd=_puller_root(),
                udid=udid,
            )
        except AcquisitionError:
            return "error"
        blob = f"{result.stdout or ''}\n{result.stderr or ''}"
        if UNTRUSTED_RE.search(blob):
            return "untrusted"
        if result.returncode == 0:
            return "ok"
        return "error"

    async def list_wda_bundle(self, udid: str, *, use_tunnel: bool = False) -> str | None:
        windows = self.uses_windows_wda_install()
        if windows:
            if not await self.apple_usb_attached_to_wsl():
                listed = await self._list_wda_windows(udid)
                if listed:
                    return listed
                return None
        usb = await self._run(
            ["ideviceinstaller", "-u", udid, "-l"],
            timeout=8.0 if windows else 12.0,
            operation="ios_list_apps_usb",
            udid=udid,
        )
        usb_blob = f"{usb.stdout or ''}\n{usb.stderr or ''}"
        bundle = extract_wda_bundle(usb.stdout or "")
        if bundle:
            return bundle
        lockdown = is_lockdown_error(usb_blob)
        if lockdown:
            self._invalidate_pair()
            self._pair_lockdown = True
            write_setup_ios_log(
                "WARN",
                "ios_list_apps_usb",
                detail="lockdownd unreachable; not treating as WDA missing",
                udid=udid,
            )
        if windows or not use_tunnel:
            return bundle
        tunnel = await self._run(
            [
                "ios",
                "apps",
                "--list",
                "--udid",
                udid,
                "--tunnel-info-port",
                _tunnel_port(),
            ],
            timeout=15.0,
            operation="ios_list_apps_tunnel",
            udid=udid,
        )
        return extract_wda_bundle((tunnel.stdout or "") + "\n" + (tunnel.stderr or ""))

    async def developer_mode_enabled(self, udid: str) -> bool | None:
        result = await self._run(
            [
                "ios",
                "devmode",
                "get",
                "--udid",
                udid,
                "--tunnel-info-port",
                _tunnel_port(),
            ],
            timeout=12.0,
            operation="ios_devmode_get",
            udid=udid,
        )
        blob = f"{result.stdout}\n{result.stderr}"
        if '"DeveloperModeEnabled":true' in blob or "Developer mode enabled: true" in blob:
            return True
        if '"DeveloperModeEnabled":false' in blob or "Developer mode enabled: false" in blob:
            return False
        if "enabled: true" in blob.lower():
            return True
        if "enabled: false" in blob.lower():
            return False
        return None

    async def reveal_developer_mode(self, udid: str) -> None:
        await self._run(
            [
                "ios",
                "devmode",
                "reveal",
                "--nojson",
                "--udid",
                udid,
                "--tunnel-info-port",
                _tunnel_port(),
            ],
            timeout=15.0,
            operation="ios_devmode_reveal",
            udid=udid,
        )

    async def ensure_tunnel(self, udid: str) -> None:
        script = _puller_root() / "ios_automator" / "scripts" / "start_tunnel.sh"
        if not script.is_file():
            return
        env = {**_tool_env(), "UDID": udid, "GO_IOS_TUNNEL_INFO_PORT": _tunnel_port()}
        await self._run(
            ["bash", str(script), "ensure"],
            timeout=60.0,
            operation="ios_tunnel_ensure",
            cwd=_puller_root(),
            env=env,
            udid=udid,
        )

    async def install_ipa(self, udid: str, ipa: Path) -> bool:
        timeout = float(settings.ios_setup_install_timeout_s)
        result = await self._run(
            [
                "ios",
                "install",
                "--path",
                str(ipa),
                "--udid",
                udid,
                "--tunnel-info-port",
                _tunnel_port(),
            ],
            timeout=timeout,
            operation="ios_install_ipa",
            udid=udid,
        )
        if result.returncode == 0:
            return True
        fallback = await self._run(
            [
                "ios",
                "install",
                str(ipa),
                "--udid",
                udid,
                "--tunnel-info-port",
                _tunnel_port(),
            ],
            timeout=timeout,
            operation="ios_install_ipa_fallback",
            udid=udid,
        )
        return fallback.returncode == 0

    async def launch_bundle(self, udid: str, bundle: str) -> str:
        windows = self.uses_windows_wda_install()
        if windows:
            return await self._launch_windows(udid, bundle)
        result = await self._run(
            [
                "ios",
                "launch",
                bundle,
                "--udid",
                udid,
                "--tunnel-info-port",
                _tunnel_port(),
            ],
            timeout=20.0,
            operation="ios_launch_wda",
            udid=udid,
        )
        blob = f"{result.stdout}\n{result.stderr}"
        if UNTRUSTED_RE.search(blob):
            return "untrusted"
        if result.returncode == 0:
            return "ok"
        return "error"

    def resolve_ipa(self) -> Path | None:
        wda_dir = Path(os.environ.get("WDA_DIR") or (Path.home() / "wda"))
        candidates = [
            wda_dir / "WebDriverAgentRunner-nodsym.ipa",
            wda_dir / "WebDriverAgentRunner.ipa",
            _puller_root() / "WebDriverAgentRunner.ipa",
        ]
        existing = [path for path in candidates if path.is_file()]
        if not existing:
            return None
        return max(existing, key=lambda path: path.stat().st_mtime)

    def resolve_altserver(self) -> Path | None:
        env_bin = os.environ.get("ALTSERVER_BIN")
        if env_bin:
            path = Path(env_bin)
            if path.is_file() and os.access(path, os.X_OK):
                return path
        wda_dir = Path(os.environ.get("WDA_DIR") or (Path.home() / "wda"))
        local = wda_dir / "AltServer"
        if local.is_file() and os.access(local, os.X_OK):
            return local
        return None

    def apple_credentials(self) -> tuple[str, str] | None:
        parsed = parse_puller_env()
        apple_id = (parsed.get("APPLE_ID") or os.environ.get("APPLE_ID") or "").strip()
        password = (
            parsed.get("APPLE_ID_PASSWORD") or os.environ.get("APPLE_ID_PASSWORD") or ""
        ).strip()
        if not apple_id or not password or password.startswith("GANTI_"):
            return None
        return apple_id, password

    def apple_id_hint(self) -> str | None:
        creds = self.apple_credentials()
        if creds is None:
            return None
        return mask_apple_id(creds[0])

    def uses_windows_wda_install(self) -> bool:
        if os.environ.get("USBMUXD_SOCKET_ADDRESS"):
            return False
        try:
            return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
        except OSError:
            return False

    async def wda_http_ready(self) -> bool:
        from app.acquisition.ios_social import _wda_ready

        return await _wda_ready(settings.ios_social_wda_url, timeout_s=2.0)

    def stack_udid_matches(self, udid: str) -> bool:
        from app.acquisition.ios_social import stack_udid_matches

        return stack_udid_matches(udid)

    async def start_altserver(
        self, udid: str, ipa: Path, apple_id: str, password: str
    ) -> asyncio.subprocess.Process:
        binary = self.resolve_altserver()
        if binary is None:
            raise acquisition_error(
                ErrorCategory.DEPENDENCY_NOT_FOUND,
                "AltServer tidak ditemukan untuk memasang WebDriverAgent.",
            )
        env = {
            **_tool_env(),
            "ALTSERVER_ANISETTE_SERVER": os.environ.get(
                "ALTSERVER_ANISETTE_SERVER", "https://ani.sidestore.io"
            ),
        }
        write_setup_ios_log(
            "INFO",
            "altserver_start",
            detail=f"bin={binary.name} ipa={ipa.name}",
            udid=udid,
        )
        try:
            return await asyncio.create_subprocess_exec(
                str(binary),
                "-u",
                udid,
                "-a",
                apple_id,
                "-p",
                password,
                str(ipa),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise acquisition_error(
                ErrorCategory.DEPENDENCY_NOT_FOUND,
                "AltServer tidak dapat dijalankan.",
            ) from exc

    async def start_windows_wda_install(
        self, udid: str, ipa: Path
    ) -> asyncio.subprocess.Process:
        script = _puller_root() / "ios_automator" / "scripts" / "install_wda_windows.sh"
        if not script.is_file():
            raise acquisition_error(
                ErrorCategory.DEPENDENCY_NOT_FOUND,
                "Skrip install_wda_windows.sh tidak ditemukan.",
            )
        env = {
            **_tool_env(),
            "SIKSIK_WDA_INSTALL_WAIT_ENTER": "0",
        }
        write_setup_ios_log(
            "INFO",
            "windows_wda_install_start",
            detail=f"script={script.name} ipa={ipa.name}",
            udid=udid,
        )
        try:
            return await asyncio.create_subprocess_exec(
                "bash",
                str(script),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                cwd=str(_puller_root()),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise acquisition_error(
                ErrorCategory.DEPENDENCY_NOT_FOUND,
                "Tidak dapat menjalankan install_wda_windows.sh.",
            ) from exc
