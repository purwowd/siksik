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
        "ios_devmode_get",
    }
)

WDA_BUNDLE_RE = re.compile(r"com\.facebook\.WebDriverAgentRunner[^\s,]*")
UNTRUSTED_RE = re.compile(
    r"deviceprocesscontrolservice|Error code: 2|could not get pid|Untrusted|not verified",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"^([^@\s]+)@([^@\s]+\.[^@\s]+)$")


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
    async def wda_http_ready(self) -> bool: ...
    def stack_udid_matches(self, udid: str) -> bool: ...
    async def start_altserver(
        self, udid: str, ipa: Path, apple_id: str, password: str
    ) -> asyncio.subprocess.Process: ...


class LiveIosSetupHost:
    def __init__(self) -> None:
        self._mux_lock = asyncio.Lock()
        self._pair_udid: str | None = None
        self._pair_ok = False
        self._pair_ok_until = 0.0

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
            self._pair_udid = udid
            self._pair_ok = ok
            self._pair_ok_until = now + (8.0 if ok else 4.0)
            return ok

    async def pair_request(self, udid: str) -> None:
        await self._run(
            ["idevicepair", "-u", udid, "pair"],
            timeout=12.0,
            operation="ios_pair_request",
            udid=udid,
        )

    async def list_wda_bundle(self, udid: str, *, use_tunnel: bool = False) -> str | None:
        usb = await self._run(
            ["ideviceinstaller", "-u", udid, "-l"],
            timeout=12.0,
            operation="ios_list_apps_usb",
            udid=udid,
        )
        bundle = extract_wda_bundle(usb.stdout or "")
        if bundle or not use_tunnel:
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
