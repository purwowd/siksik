from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.acquisition.ios_setup import IosSetupService
from app.acquisition.ios_setup_host import mask_apple_id
from app.models.schemas import (
    AnalysisScope,
    DeviceType,
    IosSetupState,
    StartSessionRequest,
)

UDID = "00008101-0008384601D8001E"


class FakeHost:
    def __init__(self, tmp_path: Path) -> None:
        self.paired = False
        self.devmode: bool | None = True
        self.bundle: str | None = None
        self.ipa = tmp_path / "WebDriverAgentRunner.ipa"
        self.ipa.write_bytes(b"ipa")
        self.install_ok = False
        self.launch_result = "untrusted"
        self.http_ready = False
        self.stack_match = False
        self.pair_requests = 0
        self.install_calls = 0
        self.use_altserver = False
        self.expected_code = "123456"

    async def pair_validate(self, udid: str) -> bool:
        del udid
        return self.paired

    async def pair_request(self, udid: str) -> None:
        del udid
        self.pair_requests += 1
        self.paired = True

    async def list_wda_bundle(self, udid: str, *, use_tunnel: bool = False) -> str | None:
        del udid, use_tunnel
        return self.bundle

    async def developer_mode_enabled(self, udid: str) -> bool | None:
        del udid
        return self.devmode

    async def reveal_developer_mode(self, udid: str) -> None:
        del udid

    async def ensure_tunnel(self, udid: str) -> None:
        del udid

    async def install_ipa(self, udid: str, ipa: Path) -> bool:
        del udid, ipa
        self.install_calls += 1
        if self.install_ok:
            self.bundle = "com.facebook.WebDriverAgentRunner.xctrunner"
        return self.install_ok

    async def launch_bundle(self, udid: str, bundle: str) -> str:
        del udid, bundle
        return self.launch_result

    def resolve_ipa(self) -> Path | None:
        return self.ipa

    def resolve_altserver(self) -> Path | None:
        return Path("/usr/bin/true")

    def apple_credentials(self) -> tuple[str, str] | None:
        return ("lab@example.com", "secret")

    def apple_id_hint(self) -> str | None:
        return mask_apple_id("lab@example.com")

    async def wda_http_ready(self) -> bool:
        return self.http_ready

    def stack_udid_matches(self, udid: str) -> bool:
        del udid
        return self.stack_match

    async def start_altserver(
        self, udid: str, ipa: Path, apple_id: str, password: str
    ) -> asyncio.subprocess.Process:
        del udid, ipa, apple_id, password
        script = (
            "import sys\n"
            "line = sys.stdin.readline().strip()\n"
            f"sys.exit(0 if line == '{self.expected_code}' else 1)\n"
        )
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )


async def _wait_state(svc: IosSetupService, state: IosSetupState) -> None:
    last = None
    for _ in range(80):
        last = await svc.status(UDID)
        if last.state == state:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"state {state.value} not reached; last={None if last is None else last.state} {None if last is None else last.message}"
    )


async def _false() -> bool:
    return False


@pytest.mark.unit
def test_mask_apple_id_hides_local_part() -> None:
    assert mask_apple_id("deniirwangarut@gmail.com") == "d***@gmail.com"
    assert mask_apple_id("") is None


@pytest.mark.unit
def test_setup_ios_log_redacts_udid_and_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.acquisition.ios_setup_log import redact_text, safe_argv, write_setup_ios_log
    from app.core import config

    log_path = tmp_path / "setup_ios.log"
    monkeypatch.setattr(config.settings, "ios_setup_log_path", log_path)
    write_setup_ios_log(
        "INFO",
        "ios_install_ipa",
        detail=f"begin udid={UDID}",
        udid=UDID,
    )
    text = log_path.read_text(encoding="utf-8")
    assert UDID not in text
    assert "<udid>" in text
    assert "ios_install_ipa" in text
    assert redact_text("123456\n", udid=UDID).strip() == "<code>"
    argv = safe_argv(["AltServer", "-u", UDID, "-p", "secret", "app.ipa"], udid=UDID)
    assert "secret" not in argv
    assert UDID not in argv


@pytest.mark.unit
async def test_failed_setup_points_to_setup_ios_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import config

    log_path = tmp_path / "setup_ios.log"
    monkeypatch.setattr(config.settings, "ios_setup_log_path", log_path)
    monkeypatch.setattr("app.services.sessions.sessions.has_in_flight", _false)
    host = FakeHost(tmp_path)
    host.resolve_ipa = lambda: None  # type: ignore[method-assign]
    svc = IosSetupService(host)
    svc.pair_polls = 1
    svc.devmode_polls = 1
    svc.poll_sleep_s = 0.0

    await svc.start(UDID)
    await _wait_state(svc, IosSetupState.FAILED)
    status = await svc.status(UDID)
    assert "setup_ios.log" in status.message
    text = log_path.read_text(encoding="utf-8")
    assert "setup_started" in text
    assert "setup_failed" in text
    assert UDID not in text


@pytest.mark.unit
async def test_probe_unpaired_then_needs_wda(tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    svc = IosSetupService(host)

    status = await svc.status(UDID)
    assert status.state == IosSetupState.USB_UNPAIRED
    assert status.ready is False

    host.paired = True
    status = await svc.status(UDID)
    assert status.state == IosSetupState.NEEDS_WDA
    assert status.apple_id_hint == "l***@example.com"


@pytest.mark.unit
async def test_start_install_then_ack_trust_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(tmp_path)
    host.install_ok = True
    svc = IosSetupService(host)
    svc.pair_polls = 1
    svc.devmode_polls = 1
    svc.poll_sleep_s = 0.0
    monkeypatch.setattr("app.services.sessions.sessions.has_in_flight", _false)

    started = await svc.start(UDID)
    assert started.state == IosSetupState.AWAITING_USB_TRUST
    await _wait_state(svc, IosSetupState.AWAITING_DEVELOPER_TRUST)
    assert host.install_calls == 1
    assert host.pair_requests == 1

    still = await svc.ack_trust(UDID)
    assert still.state == IosSetupState.AWAITING_DEVELOPER_TRUST
    assert still.wda_installed is True

    host.launch_result = "ok"
    ready = await svc.ack_trust(UDID)
    assert ready.state == IosSetupState.READY
    assert ready.ready is True

    req = StartSessionRequest(
        device_id=UDID,
        device_type=DeviceType.IOS,
        analysis_scope=AnalysisScope.SOCIAL,
        social_targets=["instagram"],
    )
    await svc.assert_session_allowed(req)


@pytest.mark.unit
async def test_altserver_code_then_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeHost(tmp_path)
    host.install_ok = False
    svc = IosSetupService(host)
    svc.pair_polls = 1
    svc.devmode_polls = 1
    svc.poll_sleep_s = 0.0
    monkeypatch.setattr("app.services.sessions.sessions.has_in_flight", _false)

    await svc.start(UDID)
    await _wait_state(svc, IosSetupState.AWAITING_APPLE_ID_CODE)
    submitted = await svc.submit_code(UDID, "123456")
    assert submitted.code_required is False
    assert "123456" not in submitted.message
    await _wait_state(svc, IosSetupState.AWAITING_DEVELOPER_TRUST)
    host.bundle = "com.facebook.WebDriverAgentRunner.xctrunner"
    host.launch_result = "ok"
    ready = await svc.ack_trust(UDID)
    assert ready.state == IosSetupState.READY


@pytest.mark.unit
async def test_social_session_blocked_until_ready(tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.paired = True
    svc = IosSetupService(host)
    req = StartSessionRequest(
        device_id=UDID,
        device_type=DeviceType.IOS,
        analysis_scope=AnalysisScope.SOCIAL,
        social_targets=["instagram"],
    )
    with pytest.raises(RuntimeError, match="WebDriverAgent"):
        await svc.assert_session_allowed(req)

    device_only = StartSessionRequest(
        device_id=UDID,
        device_type=DeviceType.IOS,
        analysis_scope=AnalysisScope.DEVICE,
        device_sources=["gallery"],
    )
    await svc.assert_session_allowed(device_only)


@pytest.mark.unit
async def test_simulator_session_skips_setup(tmp_path: Path) -> None:
    svc = IosSetupService(FakeHost(tmp_path))
    req = StartSessionRequest(
        device_id="sim-iphone-01",
        device_type=DeviceType.IOS,
        analysis_scope=AnalysisScope.SOCIAL,
        social_targets=["instagram"],
        force_simulated=True,
    )
    await svc.assert_session_allowed(req)


@pytest.mark.api
async def test_get_ios_setup_returns_status(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.acquisition.ios_setup import ios_setup
    from app.models.schemas import IosSetupStatus

    async def fake_status(device_id: str) -> IosSetupStatus:
        assert device_id == UDID
        return IosSetupStatus(
            state=IosSetupState.NEEDS_WDA,
            message="WebDriverAgent belum terpasang. Jalankan Siapkan iPhone.",
            paired=True,
            wda_installed=False,
            ready=False,
            code_required=False,
            apple_id_hint="l***@example.com",
        )

    monkeypatch.setattr(ios_setup, "status", fake_status)
    res = await client.get(f"/api/v1/ios/setup?device_id={UDID}")
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "needs_wda"
    assert body["ready"] is False
    assert body["apple_id_hint"] == "l***@example.com"


@pytest.mark.api
async def test_live_ios_social_session_requires_setup(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.acquisition.ios_setup import ios_setup

    async def blocked(_req: StartSessionRequest) -> None:
        raise RuntimeError(
            "Siapkan iPhone dulu (USB Trust, Developer Mode, WebDriverAgent) sebelum akuisisi sosmed."
        )

    monkeypatch.setattr(ios_setup, "assert_session_allowed", blocked)
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": UDID,
            "device_type": "ios",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 50,
            "analysis_scope": "social",
            "social_targets": ["instagram"],
            "participant": {"full_name": "Tes iOS", "registration_no": "IOS-SETUP-1"},
        },
    )
    assert res.status_code == 409
    assert "Siapkan iPhone" in res.json()["detail"]


@pytest.mark.api
async def test_sim_ios_social_session_skips_setup_gate(client) -> None:
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-iphone-01",
            "device_type": "ios",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 40,
            "analysis_scope": "social",
            "social_targets": ["instagram"],
            "force_simulated": True,
        },
    )
    assert res.status_code == 200
