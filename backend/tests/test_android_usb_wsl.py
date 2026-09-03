from __future__ import annotations

import pytest

from app.acquisition.android_usb_wsl import (
    android_busids_needing_wsl_attach,
    apple_busids_needing_wsl_attach,
    is_android_usb,
    is_apple_usb,
    parse_usbipd_connected,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.process import ProcessResult


SAMPLE = """
Connected:
BUSID  VID:PID    DEVICE                                                        STATE
1-2    04e8:6860  Galaxy A23 5G, SAMSUNG Mobile USB Modem #3, ADB Interface     Shared (forced)
1-5    05ac:12a8  Apple Mobile Device USB Composite Device                      Attached
1-9    3277:0093  ASUS FHD webcam, Camera DFU Device                            Not shared
1-10   13d3:3571  Realtek Bluetooth Adapter                                     Not shared

Persisted:
GUID                                  DEVICE
727e187a-8977-4a34-8d0c-ae66b9d9a745  Apple Mobile Device USB Composite Device
"""


@pytest.mark.unit
def test_parse_keeps_android_and_apple_separate() -> None:
    rows = parse_usbipd_connected(SAMPLE)
    android = next(row for row in rows if row.busid == "1-2")
    apple = next(row for row in rows if row.busid == "1-5")
    cam = next(row for row in rows if row.busid == "1-9")
    assert is_android_usb(android) is True
    assert is_apple_usb(android) is False
    assert android.shared is True
    assert is_apple_usb(apple) is True
    assert is_android_usb(apple) is False
    assert is_android_usb(cam) is False


@pytest.mark.unit
def test_only_shared_android_is_queued_for_wsl_attach() -> None:
    assert android_busids_needing_wsl_attach(SAMPLE) == ["1-2"]
    attached = SAMPLE.replace("Shared (forced)", "Attached")
    assert android_busids_needing_wsl_attach(attached) == []
    not_shared = SAMPLE.replace("Shared (forced)", "Not shared")
    assert android_busids_needing_wsl_attach(not_shared) == []


@pytest.mark.unit
def test_apple_shared_is_queued_for_wsl_attach() -> None:
    blob = (
        "Connected:\n"
        "BUSID  VID:PID    DEVICE                                                        STATE\n"
        "1-5    05ac:12a8  Apple Mobile Device USB Composite Device                      Shared\n"
        "1-2    04e8:6860  Galaxy A23 5G, ADB Interface                                  Attached\n"
    )
    assert apple_busids_needing_wsl_attach(blob) == ["1-5"]
    assert android_busids_needing_wsl_attach(blob) == []


@pytest.mark.unit
def test_apple_shared_is_never_treated_as_android() -> None:
    blob = (
        "Connected:\n"
        "BUSID  VID:PID    DEVICE                                                        STATE\n"
        "1-5    05ac:12a8  Apple Mobile Device USB Composite Device                      Shared\n"
    )
    assert android_busids_needing_wsl_attach(blob) == []


@pytest.mark.unit
async def test_ensure_android_attaches_shared_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.acquisition import android_usb_wsl as mod

    monkeypatch.setattr(mod, "running_under_wsl", lambda: True)
    called: list[list[str]] = []

    async def fake_run(argv, **_kwargs):
        called.append([str(item) for item in argv])
        if list(argv[:2]) == ["usbipd.exe", "list"]:
            return ProcessResult(tuple(str(item) for item in argv), 0, SAMPLE, "")
        return ProcessResult(tuple(str(item) for item in argv), 0, "", "")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mod, "run_process", fake_run)
    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    await mod.ensure_android_on_wsl()
    assert ["usbipd.exe", "list"] in called
    assert ["usbipd.exe", "attach", "--wsl", "--busid", "1-2"] in called
    flat = [item for row in called for item in row]
    assert "detach" not in flat
    assert "unbind" not in flat
    assert "--force" not in flat
    assert not any(row[-1] == "1-5" for row in called if "attach" in row)


@pytest.mark.unit
async def test_ensure_shared_attaches_iphone_without_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.acquisition import android_usb_wsl as mod
    from app.acquisition import ios_usbmux

    blob = (
        "Connected:\n"
        "BUSID  VID:PID    DEVICE                                                        STATE\n"
        "1-5    05ac:12a8  Apple Mobile Device USB Composite Device                      Shared\n"
    )
    monkeypatch.setattr(mod, "running_under_wsl", lambda: True)
    monkeypatch.setattr(ios_usbmux, "windows_holds_iphone_usb", lambda: False)
    called: list[list[str]] = []

    async def fake_run(argv, **_kwargs):
        called.append([str(item) for item in argv])
        if list(argv[:2]) == ["usbipd.exe", "list"]:
            return ProcessResult(tuple(str(item) for item in argv), 0, blob, "")
        return ProcessResult(tuple(str(item) for item in argv), 0, "", "")

    async def no_sleep(_seconds: float) -> None:
        return None

    async def noop() -> None:
        return None

    monkeypatch.setattr(mod, "run_process", fake_run)
    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(mod, "_wait_apple_in_lsusb", noop)
    monkeypatch.setattr(mod, "_ensure_usbmuxd", noop)
    await mod.ensure_shared_wsl_usb(attach_android=False, attach_iphone=True)
    assert ["usbipd.exe", "attach", "--wsl", "--busid", "1-5"] in called
    flat = [item for row in called for item in row]
    assert "detach" not in flat
    assert "--force" not in flat
    assert "--startup" not in flat


@pytest.mark.unit
async def test_ensure_android_skips_when_usbipd_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.acquisition import android_usb_wsl as mod

    monkeypatch.setattr(mod, "running_under_wsl", lambda: True)

    async def fake_run(*_args, **_kwargs):
        raise AcquisitionError(
            category=ErrorCategory.DEPENDENCY_NOT_FOUND,
            public_message="usbipd tidak ada",
            retryable=False,
        )

    monkeypatch.setattr(mod, "run_process", fake_run)
    await mod.ensure_android_on_wsl()
