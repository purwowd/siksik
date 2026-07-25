from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.acquisition.agent_artifact import AgentArtifactConfig, AgentArtifactService
from app.acquisition.errors import AcquisitionError, ErrorCategory


def project_fixture(tmp_path: Path, body: str) -> tuple[Path, Path, Path]:
    project = tmp_path / "android-agent"
    source = project / "app" / "src" / "main" / "kotlin" / "Fixture.kt"
    source.parent.mkdir(parents=True)
    source.write_text("class Fixture", encoding="utf-8")
    apk = project / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    counter = project / "build-count.txt"
    wrapper = project / "gradlew"
    wrapper.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    wrapper.chmod(0o700)
    return project, apk, counter


def successful_wrapper(apk: Path, counter: Path) -> str:
    return (
        "from pathlib import Path\n"
        f"apk = Path({str(apk)!r})\n"
        f"counter = Path({str(counter)!r})\n"
        "apk.parent.mkdir(parents=True, exist_ok=True)\n"
        "apk.write_bytes(b'fixture-apk')\n"
        "count = int(counter.read_text() or '0') if counter.exists() else 0\n"
        "counter.write_text(str(count + 1))"
    )


@pytest.mark.unit
async def test_build_cache_reuses_and_invalidates_on_source_change(tmp_path: Path) -> None:
    project, apk, counter = project_fixture(tmp_path, "")
    (project / "gradlew").write_text(
        f"#!{sys.executable}\n{successful_wrapper(apk, counter)}\n",
        encoding="utf-8",
    )
    (project / "gradlew").chmod(0o700)
    service = AgentArtifactService(AgentArtifactConfig(project, apk, 10))

    first = await service.build_debug_apk("request-1")
    second = await service.build_debug_apk("request-2")
    assert first.reused is False
    assert second.reused is True
    assert first.apk_sha256 == second.apk_sha256
    assert counter.read_text() == "1"

    source = project / "app" / "src" / "main" / "kotlin" / "Fixture.kt"
    source.write_text("class FixtureChanged", encoding="utf-8")
    rebuilt = await service.build_debug_apk("request-3")
    assert rebuilt.reused is False
    assert counter.read_text() == "2"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "timeout", "category"),
    [
        ("import sys\nsys.exit(7)", 10, ErrorCategory.AGENT_BUILD_FAILED),
        ("import time\ntime.sleep(2)", 0.01, ErrorCategory.AGENT_BUILD_TIMEOUT),
        ("pass", 10, ErrorCategory.AGENT_BUILD_FAILED),
    ],
)
async def test_build_failure_categories(
    tmp_path: Path,
    body: str,
    timeout: float,
    category: ErrorCategory,
) -> None:
    project, apk, _counter = project_fixture(tmp_path, body)
    service = AgentArtifactService(AgentArtifactConfig(project, apk, timeout))
    with pytest.raises(AcquisitionError) as captured:
        await service.build_debug_apk()
    assert captured.value.category == category


@pytest.mark.unit
async def test_tampered_cached_apk_is_rebuilt(tmp_path: Path) -> None:
    project, apk, counter = project_fixture(tmp_path, "")
    wrapper = project / "gradlew"
    wrapper.write_text(
        f"#!{sys.executable}\n{successful_wrapper(apk, counter)}\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    service = AgentArtifactService(AgentArtifactConfig(project, apk, 10))
    await service.build_debug_apk()
    apk.write_bytes(b"tampered")

    artifact = await service.build_debug_apk()
    assert artifact.reused is False
    assert counter.read_text() == "2"


@pytest.mark.unit
async def test_build_injects_source_digest_as_agent_identity(tmp_path: Path) -> None:
    project, apk, _counter = project_fixture(tmp_path, "")
    captured = project / "captured-build-sha.txt"
    wrapper = project / "gradlew"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        f"apk = Path({str(apk)!r})\n"
        f"captured = Path({str(captured)!r})\n"
        "apk.parent.mkdir(parents=True, exist_ok=True)\n"
        "apk.write_bytes(b'fixture-apk')\n"
        "captured.write_text(os.environ['SIKSIK_AGENT_BUILD_SHA256'])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    service = AgentArtifactService(AgentArtifactConfig(project, apk, 10))

    artifact = await service.build_debug_apk()

    assert captured.read_text() == artifact.input_sha256
    assert len(artifact.input_sha256) == 64
