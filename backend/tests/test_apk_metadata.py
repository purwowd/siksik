from __future__ import annotations

from pathlib import Path

import pytest

from app.acquisition.apk_metadata import ApkMetadataConfig, ApkMetadataInspector
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.process import ProcessResult


def sdk_fixture(tmp_path: Path) -> Path:
    sdk = tmp_path / "sdk"
    tools = (
        sdk / "cmdline-tools" / "latest" / "bin" / "apkanalyzer",
        sdk / "build-tools" / "35.0.0" / "apksigner",
    )
    for tool in tools:
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o700)
    return sdk


@pytest.mark.unit
async def test_inspector_reads_bounded_verified_metadata(tmp_path: Path) -> None:
    sdk = sdk_fixture(tmp_path)
    apk = tmp_path / "agent fixture.apk"
    apk.write_bytes(b"fixture-apk")
    calls: list[tuple[str, ...]] = []

    async def runner(argv, **kwargs):
        command = tuple(argv)
        calls.append(command)
        operation = kwargs["operation"]
        outputs = {
            "apk_application_id_probe": "com.siksik.agent\n",
            "apk_version_code_probe": "2\n",
            "apk_version_name_probe": "0.2.0\n",
            "apk_signature_probe": (
                "Signer #1 certificate SHA-256 digest: " + "AB:" * 31 + "AB\n"
            ),
            "apk_manifest_probe": (
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                'android:sharedUserId="com.siksik.agent" />'
            ),
        }
        return ProcessResult(command, 0, outputs[operation], "")

    inspector = ApkMetadataInspector(ApkMetadataConfig(android_home=sdk), runner=runner)
    metadata = await inspector.inspect(apk)

    assert metadata.package_name == "com.siksik.agent"
    assert metadata.version_code == 2
    assert metadata.version_name == "0.2.0"
    assert metadata.signer_sha256 == "ab" * 32
    assert metadata.uses_shared_user_id is True
    assert metadata.size_bytes == len(b"fixture-apk")
    assert len(metadata.apk_sha256) == 64
    assert len(calls) == 5
    assert all(command[-1] == str(apk.resolve()) for command in calls)
    assert all(
        value not in {"sh", "bash", "zsh", "-c"}
        for call in calls
        for value in call
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("operation", "stdout", "category"),
    [
        ("apk_application_id_probe", "invalid package!", ErrorCategory.AGENT_INSTALL_FAILED),
        ("apk_version_code_probe", "zero", ErrorCategory.AGENT_INSTALL_FAILED),
        ("apk_signature_probe", "certificate unavailable", ErrorCategory.AGENT_INSTALL_FAILED),
    ],
)
async def test_inspector_rejects_invalid_metadata(
    tmp_path: Path,
    operation: str,
    stdout: str,
    category: ErrorCategory,
) -> None:
    sdk = sdk_fixture(tmp_path)
    apk = tmp_path / "agent.apk"
    apk.write_bytes(b"fixture")

    async def runner(argv, **kwargs):
        current = kwargs["operation"]
        outputs = {
            "apk_application_id_probe": "com.siksik.agent",
            "apk_version_code_probe": "2",
            "apk_version_name_probe": "0.2.0",
            "apk_signature_probe": (
                "Signer #1 certificate SHA-256 digest: " + "ab" * 32
            ),
            "apk_manifest_probe": (
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                'android:sharedUserId="com.siksik.agent" />'
            ),
        }
        outputs[operation] = stdout
        return ProcessResult(tuple(argv), 0, outputs[current], "")

    inspector = ApkMetadataInspector(ApkMetadataConfig(android_home=sdk), runner=runner)
    with pytest.raises(AcquisitionError) as captured:
        await inspector.inspect(apk)
    assert captured.value.category == category


@pytest.mark.unit
async def test_inspector_maps_tool_failure_without_exposing_output(tmp_path: Path) -> None:
    sdk = sdk_fixture(tmp_path)
    apk = tmp_path / "agent.apk"
    apk.write_bytes(b"fixture")

    async def runner(argv, **_kwargs):
        return ProcessResult(tuple(argv), 7, "private-tool-output", "failure")

    inspector = ApkMetadataInspector(ApkMetadataConfig(android_home=sdk), runner=runner)
    with pytest.raises(AcquisitionError) as captured:
        await inspector.inspect(apk)
    assert captured.value.category == ErrorCategory.AGENT_INSTALL_FAILED
    assert captured.value.dependency_exit_code == 7
    assert "private-tool-output" not in str(captured.value)


@pytest.mark.unit
async def test_inspector_rejects_non_apk_input(tmp_path: Path) -> None:
    inspector = ApkMetadataInspector(ApkMetadataConfig(android_home=tmp_path))
    path = tmp_path / "agent.bin"
    path.write_bytes(b"fixture")
    with pytest.raises(AcquisitionError) as captured:
        await inspector.inspect(path)
    assert captured.value.category == ErrorCategory.VALIDATION_ERROR
