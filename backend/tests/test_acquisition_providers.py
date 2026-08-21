from __future__ import annotations

from pathlib import Path

import pytest

from app.acquisition.contracts import (
    AcquisitionContext,
    AcquisitionResult,
    ProviderKind,
    UploadedArchive,
)
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.providers import AcquisitionProviderRegistry
from app.models.schemas import AcquisitionMode, DeviceType, Scenario, SessionStatus


async def progress(
    _phase: SessionStatus,
    _percent: float,
    _message: str,
    **_fields,
) -> None:
    return None


def context(**overrides) -> AcquisitionContext:
    values = {
        "session_id": "session-provider-001",
        "device_id": "device-001",
        "device_type": DeviceType.ANDROID,
        "mode": AcquisitionMode.QUICK,
        "scenario": Scenario.LULUS,
        "file_count": 100,
        "on_progress": progress,
    }
    values.update(overrides)
    return AcquisitionContext(**values)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ctx", "expected"),
    [
        (context(simulated=True), ProviderKind.SIMULATOR),
        (context(device_id="sim-android-01"), ProviderKind.SIMULATOR),
        (context(), ProviderKind.ANDROID_LEGACY),
        (
            context(device_id="ios-device", device_type=DeviceType.IOS),
            ProviderKind.IOS,
        ),
        (
            context(
                device_id="zip:fixture.zip",
                archive=UploadedArchive(b"fixture", "fixture.zip"),
            ),
            ProviderKind.ZIP_UPLOAD,
        ),
    ],
)
def test_provider_dispatch(ctx: AcquisitionContext, expected: ProviderKind) -> None:
    provider = AcquisitionProviderRegistry().provider_for(ctx)
    assert provider.kind == expected


@pytest.mark.unit
async def test_existing_provider_calls_refactored_boundary(monkeypatch, tmp_path: Path) -> None:
    from app.services import acquisition as legacy

    calls: list[tuple] = []

    async def fake_android(*args):
        calls.append(args)
        return tmp_path, 7, 12.5, "adb"

    monkeypatch.setattr(legacy, "acquire_android_adb", fake_android)
    result = await AcquisitionProviderRegistry().acquire(context())

    assert result == AcquisitionResult(
        staging=tmp_path,
        item_count=7,
        duration_ms=12.5,
        method="adb",
        provider=ProviderKind.ANDROID_LEGACY,
    )
    assert calls[0][0:3] == (
        "session-provider-001",
        "device-001",
        AcquisitionMode.QUICK,
    )


@pytest.mark.unit
async def test_android_agent_runner_is_typed_and_explicit(tmp_path: Path) -> None:
    class Runner:
        async def acquire(self, _context: AcquisitionContext) -> AcquisitionResult:
            return AcquisitionResult(
                tmp_path,
                3,
                4.0,
                "android_agent",
                ProviderKind.ANDROID_AGENT,
            )

    registry = AcquisitionProviderRegistry(
        android_agent_enabled=True,
        android_legacy_fallback=False,
        agent_runner=Runner(),
    )
    result = await registry.acquire(context())
    assert result.provider == ProviderKind.ANDROID_AGENT


@pytest.mark.unit
def test_agent_without_runner_requires_controlled_fallback() -> None:
    registry = AcquisitionProviderRegistry(
        android_agent_enabled=True,
        android_legacy_fallback=False,
    )
    with pytest.raises(AcquisitionError) as captured:
        registry.provider_for(context())
    assert captured.value.category == ErrorCategory.AGENT_UNAVAILABLE


@pytest.mark.unit
async def test_ios_provider_adds_social_when_enabled(monkeypatch, tmp_path: Path) -> None:
    from app.services import acquisition as legacy
    from app.acquisition.providers.existing import IOSProvider

    async def fake_backup(*_args):
        return tmp_path, 3, 10.0, "idevicebackup2"

    async def fake_social(*_args):
        return 4

    async def fake_afc(*_args):
        return 0

    monkeypatch.setattr(legacy, "acquire_ios_libimobiledevice", fake_backup)
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_afc)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_afc)
    monkeypatch.setattr(
        "app.acquisition.ios_backup_comms.acquire_ios_backup_comms",
        fake_afc,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_social_ui_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_libimobiledevice_backup_enabled",
        True,
    )
    monkeypatch.setattr("app.core.config.settings.ios_afc_media_enabled", True)
    monkeypatch.setattr("app.core.config.settings.ios_afc_docs_enabled", True)

    result = await IOSProvider().acquire(
        context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
    )
    assert result.item_count == 7
    assert result.method == "idevicebackup2+ios_wda_social"
    assert result.provider == ProviderKind.IOS


@pytest.mark.unit
async def test_ios_provider_keeps_backup_when_social_fails(monkeypatch, tmp_path: Path) -> None:
    from app.acquisition.errors import AcquisitionError, ErrorCategory
    from app.acquisition.providers.existing import IOSProvider
    from app.services import acquisition as legacy

    async def fake_backup(*_args):
        return tmp_path, 3, 10.0, "idevicebackup2"

    async def fake_social(*_args):
        raise AcquisitionError(
            ErrorCategory.AGENT_UNREACHABLE,
            "WDA down",
            retryable=True,
        )

    async def fake_afc(*_args):
        return 0

    monkeypatch.setattr(legacy, "acquire_ios_libimobiledevice", fake_backup)
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_afc)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_afc)
    monkeypatch.setattr(
        "app.acquisition.ios_backup_comms.acquire_ios_backup_comms",
        fake_afc,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_social_ui_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_libimobiledevice_backup_enabled",
        True,
    )

    result = await IOSProvider().acquire(
        context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
    )
    assert result.item_count == 3
    assert result.method == "idevicebackup2"


@pytest.mark.unit
async def test_ios_provider_succeeds_on_empty_backup_with_social(
    monkeypatch, tmp_path: Path
) -> None:
    from app.acquisition.providers.existing import IOSProvider
    from app.services import acquisition as legacy

    async def fake_backup(*_args):
        return tmp_path, 0, 10.0, "idevicebackup2"

    async def fake_social(*_args):
        return 5

    async def fake_afc(*_args):
        return 0

    monkeypatch.setattr(legacy, "acquire_ios_libimobiledevice", fake_backup)
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_afc)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_afc)
    monkeypatch.setattr(
        "app.acquisition.ios_backup_comms.acquire_ios_backup_comms",
        fake_afc,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_social_ui_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_libimobiledevice_backup_enabled",
        True,
    )

    result = await IOSProvider().acquire(
        context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
    )
    assert result.item_count == 5
    assert result.method == "ios_wda_social"


@pytest.mark.unit
async def test_ios_provider_afc_media_docs_and_social(monkeypatch, tmp_path: Path) -> None:
    from app.acquisition.providers.existing import IOSProvider
    from app.core import config as config_mod

    async def fake_media(*_args):
        return 10

    async def fake_docs(*_args):
        return 2

    async def fake_social(*_args):
        return 3

    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_media)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_docs)
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )

    async def fake_comms(*_args):
        return 0

    monkeypatch.setattr(
        "app.acquisition.ios_backup_comms.acquire_ios_backup_comms",
        fake_comms,
    )
    monkeypatch.setattr(config_mod.settings, "ios_social_ui_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_afc_media_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_afc_docs_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_sms_contacts_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_libimobiledevice_backup_enabled", False)
    monkeypatch.setattr(config_mod.settings, "staging_dir", tmp_path)

    result = await IOSProvider().acquire(
        context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
    )
    assert result.item_count == 15
    assert result.method == "ios_wda_social+ios_afc_media+ios_afc_docs"


@pytest.mark.unit
async def test_ios_provider_social_only_skips_backup(monkeypatch, tmp_path: Path) -> None:
    from app.acquisition.providers.existing import IOSProvider
    from app.core import config as config_mod

    called_backup = False

    async def fake_backup(*_args):
        nonlocal called_backup
        called_backup = True
        return tmp_path, 0, 10.0, "idevicebackup2"

    async def fake_social(session_id, device_id, staging, mode, on_progress):
        staging.mkdir(parents=True, exist_ok=True)
        return 4

    async def fake_afc(*_args):
        return 0

    monkeypatch.setattr(
        "app.services.acquisition.acquire_ios_libimobiledevice",
        fake_backup,
    )
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_afc)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_afc)
    monkeypatch.setattr(config_mod.settings, "ios_social_ui_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_libimobiledevice_backup_enabled", False)
    monkeypatch.setattr(config_mod.settings, "ios_afc_media_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_afc_docs_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_sms_contacts_enabled", False)
    monkeypatch.setattr(config_mod.settings, "staging_dir", tmp_path)

    result = await IOSProvider().acquire(
        context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
    )
    assert called_backup is False
    assert result.item_count == 4
    assert result.method == "ios_wda_social"


@pytest.mark.unit
async def test_ios_provider_fails_when_backup_and_social_empty(
    monkeypatch, tmp_path: Path
) -> None:
    from app.acquisition.errors import AcquisitionError, ErrorCategory
    from app.acquisition.providers.existing import IOSProvider
    from app.services import acquisition as legacy

    async def fake_backup(*_args):
        return tmp_path, 0, 10.0, "idevicebackup2"

    async def fake_social(*_args):
        raise AcquisitionError(
            ErrorCategory.AGENT_UNREACHABLE,
            "WDA down",
            retryable=True,
        )

    async def fake_afc(*_args):
        return 0

    monkeypatch.setattr(legacy, "acquire_ios_libimobiledevice", fake_backup)
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_afc)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_afc)
    monkeypatch.setattr(
        "app.acquisition.ios_backup_comms.acquire_ios_backup_comms",
        fake_afc,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_social_ui_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.core.config.settings.ios_libimobiledevice_backup_enabled",
        True,
    )

    with pytest.raises(AcquisitionError) as captured:
        await IOSProvider().acquire(
            context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
        )
    assert captured.value.category == ErrorCategory.VALIDATION_ERROR
    assert "kosong" in captured.value.public_message.lower()


@pytest.mark.unit
async def test_ios_provider_includes_backup_comms(monkeypatch, tmp_path: Path) -> None:
    from app.acquisition.providers.existing import IOSProvider
    from app.core import config as config_mod

    async def fake_media(*_args):
        return 1

    async def fake_docs(*_args):
        return 1

    async def fake_comms(*_args):
        return 7

    async def fake_social(*_args):
        return 2

    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_media", fake_media)
    monkeypatch.setattr("app.acquisition.ios_afc.acquire_ios_afc_docs", fake_docs)
    monkeypatch.setattr(
        "app.acquisition.ios_backup_comms.acquire_ios_backup_comms",
        fake_comms,
    )
    monkeypatch.setattr(
        "app.acquisition.ios_social.acquire_ios_social_ui",
        fake_social,
    )
    monkeypatch.setattr(config_mod.settings, "ios_social_ui_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_afc_media_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_afc_docs_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_sms_contacts_enabled", True)
    monkeypatch.setattr(config_mod.settings, "ios_libimobiledevice_backup_enabled", False)
    monkeypatch.setattr(config_mod.settings, "staging_dir", tmp_path)

    result = await IOSProvider().acquire(
        context(device_id="00008101-0008384601D8001E", device_type=DeviceType.IOS)
    )
    assert result.item_count == 11
    assert result.method == (
        "ios_wda_social+ios_afc_media+ios_afc_docs+ios_backup_comms"
    )


@pytest.mark.unit
def test_non_android_provider_never_dispatches_android_agent_runner() -> None:
    class Runner:
        async def acquire(self, _context: AcquisitionContext) -> AcquisitionResult:
            raise AssertionError("Android agent must not run for a non-Android provider")

    registry = AcquisitionProviderRegistry(
        android_agent_enabled=True,
        android_legacy_fallback=False,
        agent_runner=Runner(),
    )

    provider = registry.provider_for(
        context(device_id="ios-device", device_type=DeviceType.IOS)
    )

    assert provider.kind == ProviderKind.IOS
