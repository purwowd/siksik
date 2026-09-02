from __future__ import annotations

from pathlib import Path

import pytest

from app.acquisition import ios_usbmux
from app.acquisition.ios_usbmux import (
    apply_usbmux_env,
    clear_iphone_usb_windows_hold,
    lockdown_error_token,
    resolve_usbmux_address,
    windows_holds_iphone_usb,
)
from app.acquisition.process import ProcessResult
from app.core import config
from app.models.schemas import AcquisitionMode, DeviceType, StartSessionRequest


@pytest.mark.unit
def test_wsl_local_usb_uses_unix_usbmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sock = tmp_path / "usbmuxd"
    sock.touch()
    monkeypatch.setattr(ios_usbmux, "_wsl_version_text", lambda: "linux microsoft wsl2")
    monkeypatch.setattr(ios_usbmux, "WSL_USBMUXD_PIPE", str(sock))
    monkeypatch.delenv("USBMUXD_SOCKET_ADDRESS", raising=False)
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(tmp_path / "missing"))
    assert resolve_usbmux_address() == str(sock)
    env = apply_usbmux_env({"PATH": "/usr/bin"})
    assert env["USBMUXD_SOCKET_ADDRESS"] == str(sock)


@pytest.mark.unit
def test_wsl_local_overrides_stale_windows_mux_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock = tmp_path / "usbmuxd"
    sock.touch()
    monkeypatch.setattr(ios_usbmux, "_wsl_version_text", lambda: "microsoft")
    monkeypatch.setattr(ios_usbmux, "WSL_USBMUXD_PIPE", str(sock))
    monkeypatch.setenv("USBMUXD_SOCKET_ADDRESS", "127.0.0.1:27015")
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(tmp_path / "missing"))
    assert resolve_usbmux_address() == str(sock)


@pytest.mark.unit
def test_windows_held_usb_keeps_explicit_tcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "owner"
    owner.write_text("windows", encoding="utf-8")
    monkeypatch.setattr(ios_usbmux, "_wsl_version_text", lambda: "microsoft")
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(owner))
    monkeypatch.setenv("USBMUXD_SOCKET_ADDRESS", "172.18.0.1:27015")
    assert resolve_usbmux_address() == "172.18.0.1:27015"


@pytest.mark.unit
def test_windows_held_usb_without_env_uses_library_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "owner"
    owner.write_text("windows", encoding="utf-8")
    monkeypatch.setattr(ios_usbmux, "_wsl_version_text", lambda: "microsoft")
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(owner))
    monkeypatch.delenv("USBMUXD_SOCKET_ADDRESS", raising=False)
    assert resolve_usbmux_address() is None
    env = apply_usbmux_env({"PATH": "/usr/bin"})
    assert "USBMUXD_SOCKET_ADDRESS" not in env


@pytest.mark.unit
def test_clear_windows_hold_allows_wsl_mux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock = tmp_path / "usbmuxd"
    sock.touch()
    owner = tmp_path / "owner"
    owner.write_text("windows", encoding="utf-8")
    monkeypatch.setattr(ios_usbmux, "_wsl_version_text", lambda: "microsoft")
    monkeypatch.setattr(ios_usbmux, "WSL_USBMUXD_PIPE", str(sock))
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(owner))
    monkeypatch.delenv("USBMUXD_SOCKET_ADDRESS", raising=False)
    assert windows_holds_iphone_usb() is True
    clear_iphone_usb_windows_hold()
    assert windows_holds_iphone_usb() is False
    assert owner.read_text(encoding="utf-8").strip() == "wsl"
    assert resolve_usbmux_address() == str(sock)


@pytest.mark.unit
def test_mark_iphone_usb_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = tmp_path / "owner"
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(owner))
    from app.acquisition.ios_usbmux import mark_iphone_usb_windows

    mark_iphone_usb_windows()
    assert windows_holds_iphone_usb() is True


@pytest.mark.unit
def test_non_wsl_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ios_usbmux, "_wsl_version_text", lambda: "linux version")
    monkeypatch.setenv("USBMUXD_SOCKET_ADDRESS", "/var/run/usbmuxd")
    assert resolve_usbmux_address() == "/var/run/usbmuxd"


@pytest.mark.unit
async def test_idle_ensure_skips_while_windows_holds_usb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition import ios_usb_wsl

    owner = tmp_path / "owner"
    owner.write_text("windows\n", encoding="utf-8")
    puller = tmp_path / "puller"
    script = puller / "ios_automator" / "scripts" / "ensure_iphone_wsl.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(owner))
    monkeypatch.setattr(ios_usb_wsl, "running_under_wsl", lambda: True)
    monkeypatch.setattr(config.settings, "ios_media_puller_path", puller)
    called: list[list[str]] = []

    async def fake_run(argv, **kwargs):
        called.append([str(item) for item in argv])
        return ProcessResult(tuple(str(item) for item in argv), 0, "", "")

    monkeypatch.setattr(ios_usb_wsl, "run_process", fake_run)
    await ios_usb_wsl.ensure_iphone_on_wsl()
    assert called == []
    assert windows_holds_iphone_usb() is True


@pytest.mark.unit
async def test_reattach_clears_windows_hold_and_claims_wsl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition import ios_usb_wsl

    owner = tmp_path / "owner"
    owner.write_text("windows\n", encoding="utf-8")
    puller = tmp_path / "puller"
    script = puller / "ios_automator" / "scripts" / "ensure_iphone_wsl.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("SIKSIK_IPHONE_USB_OWNER", str(owner))
    monkeypatch.setattr(ios_usb_wsl, "running_under_wsl", lambda: True)
    monkeypatch.setattr(config.settings, "ios_media_puller_path", puller)
    called: list[list[str]] = []

    async def fake_run(argv, **kwargs):
        called.append([str(item) for item in argv])
        return ProcessResult(tuple(str(item) for item in argv), 0, "", "")

    monkeypatch.setattr(ios_usb_wsl, "run_process", fake_run)
    await ios_usb_wsl.ensure_iphone_on_wsl(reattach=True)
    assert windows_holds_iphone_usb() is False
    assert called and called[0][-1] == "--startup"
    assert str(script) in called[0]


@pytest.mark.unit
async def test_lockdown_ok_skips_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition import ios_usb_wsl

    recover = tmp_path / "recover_ios_lockdown.sh"
    recover.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(ios_usb_wsl, "running_under_wsl", lambda: True)
    monkeypatch.setattr(
        config.settings,
        "ios_media_puller_path",
        tmp_path,
    )
    called: list[str] = []

    async def fake_run(argv, **kwargs):
        called.append(str(argv[0]))
        return ProcessResult(tuple(str(item) for item in argv), 0, "iPhone\n", "")

    monkeypatch.setattr(ios_usb_wsl, "run_process", fake_run)
    await ios_usb_wsl.ensure_iphone_lockdown(udid="00008101-0008384601D8001E")
    assert called == ["ideviceinfo"]


@pytest.mark.unit
async def test_lockdown_mux8_runs_recover_then_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition import ios_usb_wsl

    puller = tmp_path / "puller"
    script = puller / "ios_automator" / "scripts" / "recover_ios_lockdown.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(ios_usb_wsl, "running_under_wsl", lambda: True)
    monkeypatch.setattr(config.settings, "ios_media_puller_path", puller)
    probes = {"n": 0}
    ops: list[str] = []

    async def fake_run(argv, **kwargs):
        ops.append(str(kwargs.get("operation") or argv[0]))
        if kwargs.get("operation") == "ios_lockdown_probe":
            probes["n"] += 1
            if probes["n"] == 1:
                return ProcessResult(
                    tuple(str(item) for item in argv),
                    255,
                    "",
                    "ERROR: Could not connect to lockdownd: Mux error (-8)\n",
                )
            return ProcessResult(tuple(str(item) for item in argv), 0, "iPhone\n", "")
        return ProcessResult(tuple(str(item) for item in argv), 0, "", "")

    monkeypatch.setattr(ios_usb_wsl, "run_process", fake_run)
    await ios_usb_wsl.ensure_iphone_lockdown(udid="00008101-0008384601D8001E")
    assert ops[0] == "ios_lockdown_probe"
    assert "ios_lockdown_recover" in ops
    assert ops[-1] == "ios_lockdown_probe"


@pytest.mark.unit
async def test_lockdown_still_dead_after_recover_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition import ios_usb_wsl
    from app.acquisition.errors import AcquisitionError

    puller = tmp_path / "puller"
    script = puller / "ios_automator" / "scripts" / "recover_ios_lockdown.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    monkeypatch.setattr(ios_usb_wsl, "running_under_wsl", lambda: True)
    monkeypatch.setattr(config.settings, "ios_media_puller_path", puller)

    async def fake_run(argv, **kwargs):
        return ProcessResult(
            tuple(str(item) for item in argv),
            255,
            "",
            "ERROR: Could not connect to lockdownd: Mux error (-8)\n",
        )

    monkeypatch.setattr(ios_usb_wsl, "run_process", fake_run)
    with pytest.raises(AcquisitionError) as exc:
        await ios_usb_wsl.ensure_iphone_lockdown(udid="00008101-0008384601D8001E")
    assert "Mux -8" in exc.value.public_message
    assert exc.value.retryable is True


@pytest.mark.unit
def test_live_ios_pipeline_claims_wsl_usb_before_detect() -> None:
    import inspect

    from app.services.sessions import SessionManager, _live_ios_request

    req = StartSessionRequest(
        device_id="00008101-0008384601D8001E",
        device_type=DeviceType.IOS,
        mode=AcquisitionMode.QUICK,
    )
    sim = StartSessionRequest(
        device_id="sim-iphone-01",
        device_type=DeviceType.IOS,
        mode=AcquisitionMode.QUICK,
        force_simulated=True,
    )
    assert _live_ios_request(req) is True
    assert _live_ios_request(sim) is False
    source = inspect.getsource(SessionManager._run_pipeline)
    claim_at = source.find("ensure_iphone_on_wsl(force=True, reattach=True)")
    lockdown_at = source.find("ensure_iphone_lockdown(")
    detect_at = source.find("detect_devices(")
    restore_at = source.rfind("ensure_iphone_on_wsl(force=True)")
    assert 0 <= claim_at < lockdown_at < detect_at < restore_at
    assert "Memastikan iPhone USB di WSL" in source
    assert "Memeriksa lockdownd iPhone" in source


@pytest.mark.unit
def test_lockdown_error_token_extracts_usbmux_class() -> None:
    blob = "failed\npymobiledevice3.exceptions.ConnectionFailedToUsbmuxdError\n"
    assert lockdown_error_token(blob) == "ConnectionFailedToUsbmuxdError"
    assert lockdown_error_token("clean") is None


@pytest.mark.unit
async def test_afc_puller_env_sets_wsl_usbmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition import ios_afc

    captured: list[dict[str, str]] = []

    async def fake_process(argv, **kwargs):
        captured.append(dict(kwargs.get("env") or {}))
        command = tuple(str(item) for item in argv)
        if kwargs.get("operation") == "ios_afc_media":
            output = Path(command[command.index("-o") + 1])
            output.mkdir(parents=True)
            (output / "shot.jpg").write_bytes(b"img")
        return ProcessResult(command, 0, "", "")

    async def progress(*_args, **_fields) -> None:
        return None

    monkeypatch.setattr(ios_usbmux, "resolve_usbmux_address", lambda: "/var/run/usbmuxd")
    monkeypatch.setattr(ios_afc, "run_process", fake_process)
    monkeypatch.setattr(config.settings, "ios_afc_media_enabled", True)
    monkeypatch.setattr(config.settings, "ios_photo_library_recovery_enabled", False)

    moved = await ios_afc.acquire_ios_afc_media(
        "ios-session",
        "00008101-0008384601D8001E",
        tmp_path / "staging",
        AcquisitionMode.QUICK,
        progress,
    )

    assert moved == 1
    assert captured
    assert captured[0]["USBMUXD_SOCKET_ADDRESS"] == "/var/run/usbmuxd"
    assert captured[0]["UDID"] == "00008101-0008384601D8001E"
