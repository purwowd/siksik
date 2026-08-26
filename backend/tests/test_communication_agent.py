from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.acquisition.adb import SpecialAccessState
from app.acquisition.agent_client import AutomationResultV1, InventoryRecordV1
from app.acquisition.automation import (
    RESULT_PREFIX,
    AndroidUiAutomationOrchestrator,
    AutomationConfig,
    AutomationScopeProgressV1,
    enforce_required_scope_evidence,
    instrumentation_failure_token,
    parse_instrumentation_result,
    parse_instrumentation_scope_progress,
)
from app.acquisition.bootstrap_contracts import InstallAction
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.process import ProcessResult
from app.core.logging import StructuredJsonFormatter

SESSION_ID = "session-communication-001"
CRAWL_ID = "crawl-communication-001"
TIMESTAMP = "2026-07-16T10:00:00Z"
HASH = "a" * 64
SESSION_TOKEN = "t" * 32
TOKEN_EXPIRY_EPOCH_MS = 1_800_000_000_000


def base_record(source_kind: str, adapter: str, metadata: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_id": f"record-{source_kind}-001",
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "source_kind": source_kind,
        "source_app": None,
        "source_locator": f"{source_kind}:opaque",
        "observed_at": TIMESTAMP,
        "source_created_at": TIMESTAMP,
        "source_modified_at": None,
        "normalized_text": "fixture content",
        "metadata": metadata,
        "attachment_ids": [],
        "content_sha256": HASH,
        "preprocessing": None,
        "selection": None,
        "provenance": {
            "source_adapter": adapter,
            "enumeration_method": (
                "android_content_provider"
                if source_kind in {"sms", "contact"}
                else "android_uiautomator"
                if source_kind == "visible_ui"
                else "android_notification_listener"
            ),
            "agent_version": "0.4.0",
            "original_staged": False,
        },
    }


@pytest.mark.unit
def test_strict_models_accept_every_phase5_record_kind() -> None:
    sms = base_record(
        "sms",
        "sms_content_provider",
        {
            "direction": "received",
            "address": "+620000000",
            "address_identity": HASH,
            "thread_identity": HASH,
            "message_type": 1,
            "status": 0,
            "subscription_id": 1,
            "is_read": True,
            "is_seen": True,
            "sent_at": TIMESTAMP,
            "warning_codes": [],
        },
    )
    contact = base_record(
        "contact",
        "contacts_content_provider",
        {
            "display_name": "Fixture",
            "lookup_identity": HASH,
            "phones": [
                {"value": "+620000000", "normalized_value": "+620000000", "label": "mobile"}
            ],
            "emails": [],
            "organizations": [
                {"company": "Fixture Org", "title": "Analyst", "department": None}
            ],
            "updated_at": TIMESTAMP,
            "warning_codes": [],
        },
    )
    visible = base_record(
        "visible_ui",
        "accessibility_visible_ui",
        {
            "package_name": "com.instagram.android",
            "social_scope": "own_posts",
            "window_id": 7,
            "activity_context": "FixtureActivity",
            "event_type": 2048,
            "screen_sequence": 1,
            "nodes": [
                {
                    "sequence": 0,
                    "depth": 0,
                    "text": "Fixture",
                    "content_description": None,
                    "class_name": "android.widget.TextView",
                    "view_id": None,
                    "bounds": {"left": 0, "top": 0, "right": 100, "bottom": 100},
                    "clickable": False,
                    "scrollable": False,
                }
            ],
            "screenshot_ids": ["shot_fixture"],
            "warning_codes": [],
        },
    )
    visible["source_app"] = "com.instagram.android"
    visible["attachment_ids"] = ["shot_fixture"]
    notification = base_record(
        "notification",
        "notification_listener",
        {
            "package_name": "com.example.chat",
            "notification_identity": HASH,
            "title": "Fixture",
            "text": "Fixture content",
            "sub_text": None,
            "big_text": None,
            "text_lines": [],
            "category": "msg",
            "channel_id": "messages",
            "post_time": TIMESTAMP,
            "removed_at": None,
            "update_count": 1,
            "warning_codes": [],
        },
    )
    notification["source_app"] = "com.example.chat"

    records = [InventoryRecordV1.model_validate(value) for value in (sms, contact, visible, notification)]

    assert [record.source_kind for record in records] == [
        "sms",
        "contact",
        "visible_ui",
        "notification",
    ]
    assert records[2].attachment_ids == ["shot_fixture"]


@pytest.mark.unit
def test_sensitive_record_cannot_add_content_uri_or_unbounded_node() -> None:
    payload = base_record(
        "visible_ui",
        "accessibility_visible_ui",
        {
            "package_name": "com.instagram.android",
            "social_scope": "own_posts",
            "window_id": 1,
            "activity_context": None,
            "event_type": 1,
            "screen_sequence": 1,
            "nodes": [],
            "screenshot_ids": [],
            "warning_codes": [],
        },
    )
    payload["content_uri"] = "content://private/value"
    with pytest.raises(ValidationError):
        InventoryRecordV1.model_validate(payload)

    del payload["content_uri"]
    payload["metadata"]["nodes"] = [
        {
            "sequence": 0,
            "depth": 17,
            "text": "fixture",
            "content_description": None,
            "class_name": None,
            "view_id": None,
            "bounds": {"left": 0, "top": 0, "right": 1, "bottom": 1},
            "clickable": False,
            "scrollable": False,
        }
    ]
    with pytest.raises(ValidationError):
        InventoryRecordV1.model_validate(payload)


@pytest.mark.unit
def test_phase5_content_is_absent_from_structured_logs_and_public_errors() -> None:
    secret = "OTP 938144 untuk +620000000"
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        "siksik.acquisition.automation",
        logging.INFO,
        __file__,
        1,
        "automation_target_completed",
        (),
        None,
    )
    record.request_id = "request-redaction-001"
    record.target_package = "com.instagram.android"
    record.sms_body = secret
    record.raw_address = "+620000000"
    record.visible_text = secret
    record.notification_text = secret

    serialized_log = formatter.format(record)
    assert secret not in serialized_log
    assert "+620000000" not in serialized_log
    assert json.loads(serialized_log)["target_package"] == "com.instagram.android"

    malformed_output = RESULT_PREFIX + json.dumps({"private_content": secret})
    with pytest.raises(AcquisitionError) as captured:
        parse_instrumentation_result(malformed_output, "com.instagram.android")
    public_error = json.dumps(captured.value.envelope("request-redaction-001"))
    assert secret not in public_error
    assert "+620000000" not in public_error


@pytest.mark.unit
def test_instrumentation_result_is_strict_and_target_bound() -> None:
    payload = {
        "schema_version": 1,
        "target_package": "com.instagram.android",
        "state": "complete",
        "reason": None,
        "scroll_count": 3,
        "screenshot_ids": ["shot_fixture"],
        "duration_ms": 1200,
    }
    output = RESULT_PREFIX + json.dumps(payload, separators=(",", ":"))
    result = parse_instrumentation_result(output, "com.instagram.android")
    assert result.scroll_count == 3
    with pytest.raises(AcquisitionError) as captured:
        parse_instrumentation_result(output, "com.facebook.katana")
    assert captured.value.category == ErrorCategory.AGENT_INVALID_RESPONSE


@pytest.mark.unit
def test_instrumentation_failure_token_does_not_echo_payload() -> None:
    assert (
        instrumentation_failure_token(
            "INSTRUMENTATION_FAILED: Unable to find instrumentation info for: ComponentInfo{com.siksik.agent.automation/androidx.test.runner.AndroidJUnitRunner}",
            "",
        )
        == "runner_not_registered"
    )
    assert instrumentation_failure_token("PROCESS_CRASHED", "") == "process_crashed"
    assert instrumentation_failure_token("other", "") == "instrument_nonzero_exit"


class FakeBuilder:
    def __init__(self) -> None:
        self.calls = 0

    async def build_debug_apk(self, _request_id=None):
        self.calls += 1


class FakeAutomationAdb:
    def __init__(self, results: dict[str, AutomationResultV1]) -> None:
        self.results = results
        self.installs: list[Path] = []
        self.instrumented: list[str] = []
        self.instrumentation_arguments: list[dict[str, object]] = []
        self.restarted: list[tuple[str, dict[str, str | int]]] = []
        self.restart_failure = False
        self.force_stopped = False
        self.suspended = False

    async def install_apk(self, _serial, apk_path, **_kwargs) -> None:
        self.installs.append(apk_path)

    async def package_exists(self, _serial, package_name) -> bool:
        if package_name == "com.siksik.agent.automation":
            return bool(self.installs)
        return package_name in self.results

    async def run_instrumentation(self, _serial, **kwargs) -> ProcessResult:
        target = kwargs["arguments"]["target_package"]
        self.instrumented.append(target)
        self.instrumentation_arguments.append(kwargs["arguments"])
        payload = self.results[target].model_dump_json()
        return ProcessResult(("adb",), 0, RESULT_PREFIX + payload, "")

    async def start_activity(self, _serial, component, extras, **_kwargs) -> None:
        self.restarted.append((component, extras))
        if self.restart_failure:
            raise AcquisitionError(ErrorCategory.ADB_COMMAND_FAILED, "restart failed")

    async def force_stop(self, _serial, _package_name) -> None:
        self.force_stopped = True

    async def current_user_id(self, _serial) -> int:
        return 0

    async def special_access_state(self, _serial, _package_name, _access, **_kwargs):
        return SpecialAccessState.GRANTED

    async def set_text_only_cover_visible(self, _serial, _package_name, **_kwargs) -> None:
        return None

    async def probe_text_only_cover_status(self, _serial, _package_name, **_kwargs) -> str | None:
        return "shown"

    async def restore_accessibility_service(
        self,
        _serial,
        _package_name,
        _component,
        **_kwargs,
    ):
        return SpecialAccessState.GRANTED

    async def suspend_accessibility_service(
        self,
        _serial,
        _package_name,
        _component,
        **_kwargs,
    ) -> bool:
        self.suspended = True
        return True

    async def accessibility_service_bound(self, _serial, _component) -> bool:
        return True

    async def accessibility_binding_state(self, _serial, _component):
        from app.acquisition.adb import AccessibilityBindingState

        return AccessibilityBindingState.BOUND

    async def wait_accessibility_service_bound(
        self,
        _serial,
        _component,
        *,
        timeout_seconds: float = 8.0,
        poll_seconds: float = 0.3,
    ) -> bool:
        return True


class FakePackageCoordinator:
    def __init__(self, adb: FakeAutomationAdb, apk: Path) -> None:
        self._adb = adb
        self._apk = apk
        self.calls = 0

    async def ensure_installed(self, *, serial: str, **_kwargs) -> InstallAction:
        self.calls += 1
        await self._adb.install_apk(serial, self._apk)
        return InstallAction.UPDATE


def automation_config(apk: Path) -> AutomationConfig:
    return AutomationConfig(
        apk_path=apk,
        package_name="com.siksik.agent.automation",
        runner_component=(
            "com.siksik.agent.automation/com.siksik.agent.automation.SiksikAndroidJUnitRunner"
        ),
        test_class="com.siksik.agent.automation.SocialCrawlInstrumentation",
        agent_component="com.siksik.agent/.session.BootstrapActivity",
        agent_package_name="com.siksik.agent",
        accessibility_component=(
            "com.siksik.agent/"
            "com.siksik.agent.accessibility.CaptureAccessibilityService"
        ),
        install_timeout_seconds=30,
        target_timeout_seconds=30,
        quick_scrolls=3,
        full_scrolls=12,
        quick_screenshots=3,
        full_screenshots=8,
    )


def make_orchestrator(
    apk: Path,
    adb: FakeAutomationAdb,
    builder: FakeBuilder,
    **kwargs,
) -> AndroidUiAutomationOrchestrator:
    return AndroidUiAutomationOrchestrator(
        automation_config(apk),
        adb,
        builder,
        package_coordinator=FakePackageCoordinator(adb, apk),
        **kwargs,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "cutoff_month", "expected_scrolls", "expected_screenshots"),
    [
        ("quick", 5, 3, 3),
        ("full", 2, 12, 8),
    ],
)
async def test_orchestrator_builds_installs_and_reports_missing_target(
    tmp_path: Path,
    mode: str,
    cutoff_month: int,
    expected_scrolls: int,
    expected_screenshots: int,
) -> None:
    apk = tmp_path / "automation-debug.apk"
    apk.write_bytes(b"fixture")
    instagram = AutomationResultV1(
        schema_version=1,
        target_package="com.instagram.android",
        state="complete",
        reason=None,
        scroll_count=3,
        screenshot_ids=["shot_fixture"],
        duration_ms=100,
    )
    adb = FakeAutomationAdb({"com.instagram.android": instagram})
    builder = FakeBuilder()
    orchestrator = make_orchestrator(
        apk,
        adb,
        builder,
        clock=lambda: datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
    )
    expected_cutoff = int(
        datetime(2026, cutoff_month, 14, 10, 0, tzinfo=timezone.utc).timestamp() * 1000
    )

    results = await orchestrator.run(
        serial="serial-fixture",
        session_id=SESSION_ID,
        session_token=SESSION_TOKEN,
        token_expires_at_epoch_ms=TOKEN_EXPIRY_EPOCH_MS,
        crawl_id=CRAWL_ID,
        mode=mode,
        not_before_epoch_ms=expected_cutoff if mode == "full" else None,
        target_packages=("com.instagram.android", "com.facebook.katana"),
        request_id="request-fixture",
    )

    assert builder.calls == 1
    assert adb.installs == [apk]
    assert adb.suspended is True
    assert adb.instrumented == ["com.instagram.android"]
    assert adb.instrumentation_arguments[0]["not_before_epoch_ms"] == expected_cutoff
    assert adb.instrumentation_arguments[0]["max_scrolls"] == expected_scrolls
    assert adb.instrumentation_arguments[0]["max_screenshots"] == expected_screenshots
    assert adb.restarted == [
        (
            "com.siksik.agent/.session.BootstrapActivity",
            {
                "session_id": SESSION_ID,
                "session_token": SESSION_TOKEN,
                "token_expires_at_epoch_ms": TOKEN_EXPIRY_EPOCH_MS,
            },
        ),
    ]
    assert [result.state for result in results] == ["complete", "target_missing"]


@pytest.mark.unit
async def test_orchestrator_rejects_invalid_time_cutoff(tmp_path: Path) -> None:
    apk = tmp_path / "automation-debug.apk"
    apk.write_bytes(b"fixture")
    builder = FakeBuilder()
    orchestrator = AndroidUiAutomationOrchestrator(
        automation_config(apk),
        FakeAutomationAdb({}),
        builder,
    )

    with pytest.raises(AcquisitionError) as captured:
        await orchestrator.run(
            serial="serial-fixture",
            session_id=SESSION_ID,
            session_token=SESSION_TOKEN,
            token_expires_at_epoch_ms=TOKEN_EXPIRY_EPOCH_MS,
            crawl_id=CRAWL_ID,
            mode="quick",
            not_before_epoch_ms=True,
            target_packages=("com.instagram.android",),
            request_id="request-fixture",
        )

    assert captured.value.category == ErrorCategory.VALIDATION_ERROR
    assert builder.calls == 0


@pytest.mark.unit
async def test_orchestrator_marks_agent_restart_failure_explicitly(tmp_path: Path) -> None:
    apk = tmp_path / "automation-debug.apk"
    apk.write_bytes(b"fixture")
    result = AutomationResultV1(
        schema_version=1,
        target_package="com.instagram.android",
        state="complete",
        reason=None,
        scroll_count=3,
        screenshot_ids=["shot_fixture"],
        duration_ms=100,
    )
    adb = FakeAutomationAdb({"com.instagram.android": result})
    adb.restart_failure = True
    orchestrator = make_orchestrator(apk, adb, FakeBuilder())

    results = await orchestrator.run(
        serial="serial-fixture",
        session_id=SESSION_ID,
        session_token=SESSION_TOKEN,
        token_expires_at_epoch_ms=TOKEN_EXPIRY_EPOCH_MS,
        crawl_id=CRAWL_ID,
        mode="quick",
        target_packages=("com.instagram.android",),
        request_id="request-fixture",
    )

    assert results[0].state == "complete"
    assert results[0].reason is None
    assert len(adb.restarted) == 1


@pytest.mark.unit
async def test_orchestrator_force_stops_instrumentation_on_timeout(tmp_path: Path) -> None:
    apk = tmp_path / "automation-debug.apk"
    apk.write_bytes(b"fixture")

    class TimeoutAdb(FakeAutomationAdb):
        async def run_instrumentation(self, _serial, **_kwargs) -> ProcessResult:
            raise AcquisitionError(
                ErrorCategory.ADB_TIMEOUT,
                "instrumentation timed out",
                retryable=True,
            )

    adb = TimeoutAdb({"com.instagram.android": None})  # type: ignore[arg-type]
    orchestrator = make_orchestrator(apk, adb, FakeBuilder())

    results = await orchestrator.run(
        serial="serial-fixture",
        session_id=SESSION_ID,
        session_token=SESSION_TOKEN,
        token_expires_at_epoch_ms=TOKEN_EXPIRY_EPOCH_MS,
        crawl_id=CRAWL_ID,
        mode="quick",
        target_packages=("com.instagram.android",),
        request_id="request-fixture",
    )

    assert adb.force_stopped is True
    assert results[0].state == "timeout"
    assert results[0].reason == "automation_timeout"


@pytest.mark.unit
async def test_orchestrator_cancellation_stops_only_automation_package(tmp_path: Path) -> None:
    apk = tmp_path / "automation-debug.apk"
    apk.write_bytes(b"fixture")
    started = asyncio.Event()

    class BlockingAdb(FakeAutomationAdb):
        async def run_instrumentation(self, _serial, **_kwargs) -> ProcessResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    result = AutomationResultV1(
        schema_version=1,
        target_package="com.instagram.android",
        state="complete",
        reason=None,
        scroll_count=0,
        screenshot_ids=[],
        duration_ms=0,
    )
    adb = BlockingAdb({"com.instagram.android": result})
    orchestrator = make_orchestrator(apk, adb, FakeBuilder())
    task = asyncio.create_task(
        orchestrator.run(
            serial="serial-fixture",
            session_id=SESSION_ID,
            session_token=SESSION_TOKEN,
            token_expires_at_epoch_ms=TOKEN_EXPIRY_EPOCH_MS,
            crawl_id=CRAWL_ID,
            mode="quick",
            target_packages=("com.instagram.android",),
            request_id="request-fixture",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adb.force_stopped is True
    assert len(adb.restarted) == 1


@pytest.mark.unit
def test_normalize_automation_result_upgrades_text_only_partial_to_failed() -> None:
    from app.acquisition.automation import normalize_automation_result

    partial = AutomationResultV1(
        schema_version=1,
        target_package="com.twitter.android",
        state="partial",
        reason="scope_navigation_incomplete",
        scroll_count=2,
        screenshot_ids=[],
        duration_ms=1000,
    )
    normalized = normalize_automation_result(partial, "com.twitter.android")
    assert normalized.state == "failed"
    assert normalized.reason == "scope_navigation_incomplete"

    instagram = normalize_automation_result(
        partial.model_copy(update={"target_package": "com.instagram.android"}),
        "com.instagram.android",
    )
    assert instagram.state == "failed"
    assert instagram.reason == "scope_navigation_incomplete"


def _scope_progress_line(
    *,
    target: str = "com.instagram.android",
    scope: str = "own_profile",
    stage: str = "checkpoint_saved",
    state: str = "complete",
    attempt: int = 1,
    scroll_count: int = 0,
    screenshot_count: int = 0,
    failure_class: str | None = None,
    reason: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "schema_version": 1,
        "target_package": target,
        "scope": scope,
        "stage": stage,
        "state": state,
        "attempt": attempt,
        "scroll_count": scroll_count,
        "screenshot_count": screenshot_count,
    }
    if failure_class is not None:
        payload["failure_class"] = failure_class
    if reason is not None:
        payload["reason"] = reason
    return (
        "INSTRUMENTATION_STATUS: siksik_scope_progress="
        + json.dumps(payload, separators=(",", ":"))
    )


@pytest.mark.unit
def test_parse_instrumentation_scope_progress_accepts_valid_line() -> None:
    parsed = parse_instrumentation_scope_progress(
        _scope_progress_line(
            scope="own_posts",
            stage="attempt_started",
            state="running",
            attempt=2,
            scroll_count=4,
            screenshot_count=1,
        ),
        "com.instagram.android",
    )
    assert parsed is not None
    assert parsed.scope == "own_posts"
    assert parsed.state == "running"
    assert parsed.attempt == 2
    assert parsed.scroll_count == 4
    assert parsed.screenshot_count == 1


@pytest.mark.unit
def test_parse_instrumentation_scope_progress_ignores_unrelated_lines() -> None:
    assert (
        parse_instrumentation_scope_progress(
            "INSTRUMENTATION_STATUS: stream=stdout",
            "com.instagram.android",
        )
        is None
    )


@pytest.mark.unit
def test_parse_instrumentation_scope_progress_rejects_inconsistent_payload() -> None:
    with pytest.raises(AcquisitionError) as exc:
        parse_instrumentation_scope_progress(
            _scope_progress_line(scope="own_tweets"),
            "com.instagram.android",
        )
    assert exc.value.category == ErrorCategory.AGENT_INVALID_RESPONSE


@pytest.mark.unit
def test_enforce_required_scope_evidence_keeps_complete_when_progress_empty() -> None:
    result = AutomationResultV1(
        schema_version=1,
        target_package="com.instagram.android",
        state="complete",
        reason=None,
        scroll_count=3,
        screenshot_ids=[],
        duration_ms=1000,
    )
    assert (
        enforce_required_scope_evidence(result, "com.instagram.android", []).state
        == "complete"
    )


@pytest.mark.unit
def test_enforce_required_scope_evidence_fails_when_observed_scopes_incomplete() -> None:
    result = AutomationResultV1(
        schema_version=1,
        target_package="com.instagram.android",
        state="complete",
        reason=None,
        scroll_count=3,
        screenshot_ids=[],
        duration_ms=1000,
    )
    progress = [
        AutomationScopeProgressV1(
            target_package="com.instagram.android",
            scope="own_profile",
            stage="checkpoint_saved",
            state="complete",
            attempt=1,
            failure_class=None,
            reason=None,
            scroll_count=0,
            screenshot_count=0,
        ),
        AutomationScopeProgressV1(
            target_package="com.instagram.android",
            scope="own_posts",
            stage="attempt_failed",
            state="failed",
            attempt=3,
            failure_class="action",
            reason="scope_navigation_failed",
            scroll_count=0,
            screenshot_count=0,
        ),
    ]
    enforced = enforce_required_scope_evidence(
        result,
        "com.instagram.android",
        progress,
    )
    assert enforced.state == "failed"
    assert enforced.reason == "required_scope_evidence_missing"


@pytest.mark.unit
def test_enforce_required_scope_evidence_keeps_complete_when_all_required_complete() -> None:
    result = AutomationResultV1(
        schema_version=1,
        target_package="com.twitter.android",
        state="complete",
        reason=None,
        scroll_count=10,
        screenshot_ids=[],
        duration_ms=2000,
    )
    progress = [
        AutomationScopeProgressV1(
            target_package="com.twitter.android",
            scope=scope,
            stage="checkpoint_saved",
            state="complete",
            attempt=1,
            failure_class=None,
            reason=None,
            scroll_count=1,
            screenshot_count=0,
        )
        for scope in ("own_profile", "own_tweets", "own_replies")
    ]
    assert (
        enforce_required_scope_evidence(result, "com.twitter.android", progress).state
        == "complete"
    )
