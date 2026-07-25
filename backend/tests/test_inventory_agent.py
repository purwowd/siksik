from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.acquisition.agent_client import (
    INVENTORY_SOURCES,
    AgentClient,
    AgentSessionV1,
    AutomationResultV1,
    InventoryPageV1,
    InventoryRunV1,
    LiveSelectedRecordPageV1,
    PreprocessedRecordPageV1,
    PreprocessingRunV1,
    SelectionCandidatePageV1,
    SelectionRunV1,
)
from app.acquisition.bootstrap_runner import Phase4AndroidAgentRunner, Phase5AndroidAgentRunner
from app.acquisition.bootstrap_contracts import runtime_permissions_for_api
from app.acquisition.contracts import AcquisitionContext, AcquisitionResult, ProviderKind
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.runtime import AgentRuntimeSecrets
from app.models.schemas import AcquisitionMode, DeviceType, Scenario, SessionStatus

SESSION_ID = "session-inventory-001"
CRAWL_ID = "crawl-inventory-001"
TIMESTAMP = "2026-07-16T10:00:00Z"


def preprocessing_payload(state: str = "complete") -> dict[str, object]:
    return {
        "schema_version": 1,
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "state": state,
        "started_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        "completed_at": TIMESTAMP if state != "running" else None,
        "deadline_at": "2026-07-16T10:10:00Z",
        "totals": {
            "total": 0,
            "pending": 0,
            "processing": 0,
            "completed": 0,
            "skipped": 0,
            "truncated": 0,
            "failed": 0,
            "cancelled": 0,
        },
        "preprocessor_totals": {
            name: {
                "attempted": 0,
                "processed": 0,
                "skipped": 0,
                "truncated": 0,
                "failed": 0,
                "cancelled": 0,
            }
            for name in (
                "exact_hash",
                "perceptual_hash",
                "ocr",
                "document_text",
                "face",
                "objects",
            )
        },
        "partial_reasons": [],
    }


@pytest.mark.unit
def test_runtime_permission_matrix_covers_android_storage_branches_and_audio() -> None:
    api_33 = {item.permission: item.required for item in runtime_permissions_for_api(33)}
    api_29 = {item.permission: item.required for item in runtime_permissions_for_api(29)}
    api_28 = {item.permission: item.required for item in runtime_permissions_for_api(28)}

    assert api_33["android.permission.READ_MEDIA_IMAGES"] is True
    assert api_33["android.permission.READ_MEDIA_VIDEO"] is True
    assert api_33["android.permission.READ_MEDIA_AUDIO"] is True
    assert api_33["android.permission.ACCESS_MEDIA_LOCATION"] is False
    assert api_33["android.permission.READ_SMS"] is False
    assert api_33["android.permission.READ_CONTACTS"] is False
    assert api_29 == {
        "android.permission.READ_EXTERNAL_STORAGE": True,
        "android.permission.ACCESS_MEDIA_LOCATION": False,
        "android.permission.READ_SMS": False,
        "android.permission.READ_CONTACTS": False,
    }
    assert api_28 == {
        "android.permission.READ_EXTERNAL_STORAGE": True,
        "android.permission.READ_SMS": False,
        "android.permission.READ_CONTACTS": False,
    }


def source_progress(state: str = "pending", *, reason: str | None = None) -> dict[str, object]:
    return {
        "state": state,
        "scanned_count": 0,
        "discovered_count": 0,
        "duplicate_count": 0,
        "sampled": False,
        "reason": reason,
        "resume_cursor": None,
    }


def run_payload(
    state: str = "crawling",
    *,
    source_state: str = "pending",
) -> dict[str, object]:
    progress = {source: source_progress(source_state) for source in sorted(INVENTORY_SOURCES)}
    reasons: list[dict[str, str]] = []
    if source_state in {"denied", "restricted", "unsupported", "partial", "failed"}:
        reasons = [
            {"source": source, "state": source_state, "reason": "fixture_reason"}
            for source in sorted(INVENTORY_SOURCES)
        ]
        for value in progress.values():
            value["reason"] = "fixture_reason"
    return {
        "schema_version": 1,
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "mode": "full",
        "state": state,
        "started_at": TIMESTAMP,
        "updated_at": TIMESTAMP,
        "completed_at": (
            TIMESTAMP if state in {"complete", "partial", "cancelled", "failed"} else None
        ),
        "source_progress": progress,
        "totals": {"scanned": 0, "discovered": 0, "duplicates": 0},
        "partial_reasons": reasons,
        "resume_cursors": {},
    }


def record_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_id": "record-inventory-001",
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "source_kind": "media_image",
        "source_app": "whatsapp",
        "source_locator": "public_whatsapp:opaque-fixture",
        "observed_at": TIMESTAMP,
        "source_created_at": TIMESTAMP,
        "source_modified_at": TIMESTAMP,
        "normalized_text": None,
        "metadata": {
            "display_name": "fixture.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 1024,
            "width": 32,
            "height": 24,
            "duration_ms": None,
            "date_taken": TIMESTAMP,
            "date_added": TIMESTAMP,
            "date_modified": TIMESTAMP,
            "capture_time": TIMESTAMP,
            "capture_time_source": "date_taken",
            "directory_hint": "Android/media/com.whatsapp/WhatsApp/Media",
            "exif": {
                "state": "present",
                "orientation": 1,
                "camera_make": "Fixture",
                "camera_model": None,
                "lens_model": None,
                "exposure_time": None,
                "aperture": None,
                "focal_length": None,
                "iso": None,
                "latitude": -6.2,
                "longitude": 106.8,
                "altitude": None,
                "captured_at": TIMESTAMP,
                "warning_codes": [],
            },
            "warning_codes": [],
            "thumbnail_available": True,
        },
        "attachment_ids": [],
        "content_sha256": None,
        "preprocessing": None,
        "selection": None,
        "provenance": {
            "source_adapter": "public_whatsapp",
            "enumeration_method": "android_platform_api",
            "agent_version": "0.3.0",
            "original_staged": False,
        },
    }


def page_payload(
    *,
    state: str = "complete",
    next_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "crawl_id": CRAWL_ID,
        "siksik_session_id": SESSION_ID,
        "source_adapter": "public_whatsapp",
        "source_state": state,
        "source_reason": None,
        "sampled": False,
        "scanned_count": 1,
        "discovered_count": 1,
        "duplicate_count": 0,
        "records": [record_payload()],
        "next_cursor": next_cursor,
    }


def agent_response(request: httpx.Request, status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"X-Request-ID": request.headers["X-Request-ID"]},
    )


@pytest.mark.unit
async def test_inventory_client_uses_typed_authenticated_routes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/records"):
            return agent_response(request, 200, page_payload())
        if request.url.path.endswith("/cancel"):
            return agent_response(request, 200, run_payload("cancelled", source_state="cancelled"))
        if request.url.path.endswith("/resume"):
            return agent_response(request, 200, run_payload())
        if request.method == "POST":
            return agent_response(request, 201, run_payload())
        return agent_response(request, 200, run_payload("complete", source_state="complete"))

    client = AgentClient(43111, "t" * 32, transport=httpx.MockTransport(handler))
    started = await client.start_inventory(
        SESSION_ID,
        "full",
        request_id="inventory-request-001",
    )
    page = await client.inventory_page(
        SESSION_ID,
        CRAWL_ID,
        "public_whatsapp",
        limit=50,
        request_id="inventory-request-002",
    )
    status = await client.inventory_status(
        SESSION_ID,
        CRAWL_ID,
        request_id="inventory-request-003",
    )
    cancelled = await client.cancel_inventory(
        SESSION_ID,
        CRAWL_ID,
        request_id="inventory-request-004",
    )
    resumed = await client.resume_inventory(
        SESSION_ID,
        CRAWL_ID,
        request_id="inventory-request-005",
    )

    assert started.body.crawl_id == CRAWL_ID
    assert page.body.records[0].metadata.mime_type == "image/jpeg"
    assert status.body.state == "complete"
    assert cancelled.body.state == "cancelled"
    assert resumed.body.state == "crawling"
    assert json.loads(seen[0].content) == {
        "mode": "full",
        "document_grant_id": None,
        "target_packages": [],
    }
    assert dict(seen[1].url.params) == {"source": "public_whatsapp", "limit": "50"}
    assert all(item.headers["Authorization"] == "Bearer " + "t" * 32 for item in seen)


@pytest.mark.unit
async def test_inventory_client_rejects_content_uri_or_extra_record_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = page_payload()
        payload["records"][0]["content_uri"] = "content://must-not-cross-wire"
        return agent_response(request, 200, payload)

    client = AgentClient(43111, "t" * 32, transport=httpx.MockTransport(handler))
    with pytest.raises(AcquisitionError) as captured:
        await client.inventory_page(
            SESSION_ID,
            CRAWL_ID,
            "public_whatsapp",
            request_id="inventory-request-invalid",
        )
    assert captured.value.category == ErrorCategory.AGENT_INVALID_RESPONSE


@pytest.mark.unit
async def test_agent_client_validates_preprocessing_run_and_typed_records() -> None:
    processed = record_payload()
    processed["content_sha256"] = "a" * 64
    processed["normalized_text"] = "fixture text"
    processed["preprocessing"] = {
        "schema_version": 1,
        "status": "completed",
        "warnings": [],
        "exact_hash": {
            "engine": {
                "name": "SHA-256",
                "version": "FIPS-180-4",
                "model_asset": None,
                "model_sha256": None,
            },
            "status": "completed",
            "duration_ms": 2,
            "warnings": [],
            "sha256": "a" * 64,
            "bytes_read": 1024,
        },
        "face_cluster_ids": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        request_id = request.headers["x-request-id"]
        payload = (
            {
                "schema_version": 1,
                "crawl_id": CRAWL_ID,
                "siksik_session_id": SESSION_ID,
                "records": [processed],
                "next_cursor": None,
            }
            if request.url.path.endswith("/preprocessing/records")
            else preprocessing_payload("complete")
        )
        return httpx.Response(
            200,
            json=payload,
            headers={"X-Request-ID": request_id},
        )

    client = AgentClient(43111, "t" * 32, transport=httpx.MockTransport(handler))
    run = await client.start_preprocessing(
        SESSION_ID,
        CRAWL_ID,
        request_id="preprocessing-start",
    )
    page = await client.preprocessing_records(
        SESSION_ID,
        CRAWL_ID,
        request_id="preprocessing-records",
    )

    assert run.body.state == "complete"
    assert page.body.records[0].preprocessing is not None
    assert page.body.records[0].preprocessing.exact_hash is not None
    assert page.body.records[0].preprocessing.exact_hash.sha256 == "a" * 64


class FakeBootstrap:
    def __init__(self) -> None:
        self.calls = 0

    async def bootstrap(self, **_kwargs) -> None:
        self.calls += 1


class FakeRegistry:
    async def get(self, session_id: str) -> AgentRuntimeSecrets:
        return AgentRuntimeSecrets(
            session_id=session_id,
            serial="device-fixture",
            token="t" * 32,
            forward_host_port=43111,
            token_expires_at="2026-07-16T11:00:00+00:00",
        )


class FakeInventoryClient:
    def __init__(self, final: InventoryRunV1) -> None:
        self.final = final
        self.page_sources: list[str] = []
        self.page_limits: dict[str, int] = {}
        self.bootstrap_calls = 0
        self.cancelled = False
        self.automation_results: list[AutomationResultV1] = []
        self.selection_run: SelectionRunV1 | None = None

    async def bootstrap(self, session_id, _api_version, **_kwargs):
        self.bootstrap_calls += 1
        return SimpleNamespace(
            body=AgentSessionV1(
                session_id=session_id,
                api_version="1.0",
                state="active",
            )
        )

    async def start_inventory(self, *_args, **_kwargs):
        return SimpleNamespace(body=InventoryRunV1.model_validate(run_payload()))

    async def inventory_page(self, _session, _crawl, source, **kwargs):
        self.page_sources.append(source)
        self.page_limits[source] = kwargs["limit"]
        payload = page_payload()
        payload["source_adapter"] = source
        payload["records"] = []
        payload["discovered_count"] = 0
        payload["scanned_count"] = 0
        return SimpleNamespace(body=InventoryPageV1.model_validate(payload))

    async def inventory_status(self, *_args, **_kwargs):
        return SimpleNamespace(body=self.final)

    async def cancel_inventory(self, *_args, **_kwargs):
        self.cancelled = True

    async def report_automation_result(self, _session, _crawl, result, **_kwargs):
        self.automation_results.append(result)
        return SimpleNamespace(body=InventoryRunV1.model_validate(run_payload()))

    async def start_preprocessing(self, *_args, **_kwargs):
        return SimpleNamespace(
            body=PreprocessingRunV1.model_validate(preprocessing_payload()),
        )

    async def preprocessing_status(self, *_args, **_kwargs):
        return SimpleNamespace(
            body=PreprocessingRunV1.model_validate(preprocessing_payload()),
        )

    async def preprocessing_records(self, *_args, **_kwargs):
        return SimpleNamespace(
            body=PreprocessedRecordPageV1.model_validate(
                {
                    "schema_version": 1,
                    "crawl_id": CRAWL_ID,
                    "siksik_session_id": SESSION_ID,
                    "records": [],
                    "next_cursor": None,
                }
            ),
        )

    async def cancel_preprocessing(self, *_args, **_kwargs):
        self.cancelled = True
        return SimpleNamespace(
            body=PreprocessingRunV1.model_validate(preprocessing_payload("cancelled")),
        )

    async def start_selection(
        self,
        _session,
        _crawl,
        policy_fingerprint,
        review_candidates,
        **_kwargs,
    ):
        self.selection_run = SelectionRunV1.model_validate(
            {
                "schema_version": 1,
                "crawl_id": CRAWL_ID,
                "siksik_session_id": SESSION_ID,
                "state": "confirmed",
                "policy_version": "siksik-selection-v1",
                "policy_fingerprint": policy_fingerprint,
                "revision": 1,
                "selection_fingerprint": "b" * 64,
                "review_candidates": review_candidates,
                "totals": {
                    "total": 0,
                    "evaluated": 0,
                    "candidates": 0,
                    "auto_selected": 0,
                    "selected": 0,
                    "below_threshold": 0,
                    "selected_bytes": 0,
                },
                "started_at": TIMESTAMP,
                "updated_at": TIMESTAMP,
                "frozen_at": TIMESTAMP,
                "confirmed_at": TIMESTAMP,
                "failure_reason": None,
            }
        )
        return SimpleNamespace(body=self.selection_run)

    async def selection_status(self, *_args, **_kwargs):
        return SimpleNamespace(body=self.selection_run)

    async def selection_candidates(self, *_args, **_kwargs):
        assert self.selection_run is not None
        return SimpleNamespace(
            body=SelectionCandidatePageV1.model_validate(
                {
                    "schema_version": 1,
                    "crawl_id": CRAWL_ID,
                    "siksik_session_id": SESSION_ID,
                    "revision": self.selection_run.revision,
                    "selection_fingerprint": self.selection_run.selection_fingerprint,
                    "records": [],
                    "next_cursor": None,
                }
            )
        )

    async def live_selected_records(self, *_args, **_kwargs):
        return SimpleNamespace(
            body=LiveSelectedRecordPageV1.model_validate(
                {
                    "schema_version": 1,
                    "crawl_id": CRAWL_ID,
                    "siksik_session_id": SESSION_ID,
                    "selection_state": "confirmed",
                    "review_candidates": False,
                    "records": [],
                    "next_cursor": None,
                }
            )
        )

    async def cancel_selection(self, *_args, **_kwargs):
        self.cancelled = True


class FakeSelectionRepository:
    def __init__(self) -> None:
        self.runs: list[SelectionRunV1] = []

    async def begin_snapshot(self, run: SelectionRunV1) -> None:
        self.runs.append(run)

    async def append_candidates(self, _session_id, _crawl_id, _candidates) -> None:
        return None

    async def finish_snapshot(self, run: SelectionRunV1) -> None:
        self.runs.append(run)


class FakeAutomation:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return [
            AutomationResultV1(
                schema_version=1,
                target_package="com.instagram.android",
                state="complete",
                reason=None,
                scroll_count=3,
                screenshot_ids=["shot_fixture"],
                duration_ms=100,
            )
        ]


class FakeTransfer:
    def __init__(self, staging: Path, item_count: int = 1) -> None:
        self.staging = staging
        self.item_count = item_count
        self.calls = 0

    async def ingest(self, _context, _client, _selection) -> AcquisitionResult:
        self.calls += 1
        return AcquisitionResult(
            staging=self.staging,
            item_count=self.item_count,
            duration_ms=1.0,
            method="android_agent_direct_manifest",
            provider=ProviderKind.ANDROID_AGENT,
        )


async def progress(
    _phase: SessionStatus,
    _percent: float,
    _message: str,
    **_fields,
) -> None:
    return None


def acquisition_context(
    mode: AcquisitionMode = AcquisitionMode.FULL,
    *,
    review_candidates: bool = False,
) -> AcquisitionContext:
    return AcquisitionContext(
        session_id=SESSION_ID,
        device_id="device-fixture",
        device_type=DeviceType.ANDROID,
        mode=mode,
        scenario=Scenario.LULUS,
        file_count=10,
        on_progress=progress,
        request_id="inventory-runner-request",
        review_candidates=review_candidates,
    )


@pytest.mark.unit
async def test_phase4_runner_exhausts_every_accessible_source(tmp_path: Path) -> None:
    final = InventoryRunV1.model_validate(run_payload("complete", source_state="complete"))
    client = FakeInventoryClient(final)
    bootstrap = FakeBootstrap()

    runner = Phase4AndroidAgentRunner(
        bootstrap,
        runtime_registry=FakeRegistry(),
        client_factory=lambda _port, _token: client,
        page_size=25,
        selection_repository=FakeSelectionRepository(),
        transfer=FakeTransfer(tmp_path, 3),
    )
    result = await runner.acquire(acquisition_context())

    assert bootstrap.calls == 1
    assert set(client.page_sources) == INVENTORY_SOURCES
    assert len(client.page_sources) == len(INVENTORY_SOURCES)
    assert result.provider == ProviderKind.ANDROID_AGENT
    assert result.item_count == 3
    assert result.method == (
        "android_agent_inventory_complete+preprocessing_complete+selection_confirmed"
        "+android_agent_direct_manifest"
    )


@pytest.mark.unit
async def test_phase4_runner_preserves_explicit_partial_state(tmp_path: Path) -> None:
    payload = run_payload("partial", source_state="complete")
    payload["source_progress"]["document_tree"] = source_progress(
        "restricted",
        reason="document_tree_grant_required",
    )
    payload["partial_reasons"] = [
        {
            "source": "document_tree",
            "state": "restricted",
            "reason": "document_tree_grant_required",
        }
    ]
    final = InventoryRunV1.model_validate(payload)
    client = FakeInventoryClient(final)

    runner = Phase4AndroidAgentRunner(
        FakeBootstrap(),
        runtime_registry=FakeRegistry(),
        client_factory=lambda _port, _token: client,
        selection_repository=FakeSelectionRepository(),
        transfer=FakeTransfer(tmp_path),
    )
    result = await runner.acquire(acquisition_context())

    assert result.method == (
        "android_agent_inventory_partial+preprocessing_complete+selection_confirmed"
        "+android_agent_direct_manifest"
    )
    assert final.partial_reasons[0].reason == "document_tree_grant_required"


@pytest.mark.unit
async def test_phase5_runner_runs_and_reports_read_only_social_automation(
    tmp_path: Path,
) -> None:
    final = InventoryRunV1.model_validate(run_payload("complete", source_state="complete"))
    client = FakeInventoryClient(final)
    automation = FakeAutomation()

    runner = Phase5AndroidAgentRunner(
        FakeBootstrap(),
        runtime_registry=FakeRegistry(),
        client_factory=lambda _port, _token: client,
        automation=automation,
        target_packages=("com.instagram.android",),
        selection_repository=FakeSelectionRepository(),
        transfer=FakeTransfer(tmp_path),
    )

    await runner.acquire(acquisition_context(AcquisitionMode.QUICK))

    assert len(automation.calls) == 1
    assert automation.calls[0]["target_packages"] == ("com.instagram.android",)
    assert automation.calls[0]["session_token"] == "t" * 32
    assert automation.calls[0]["token_expires_at_epoch_ms"] > 0
    assert client.bootstrap_calls == 2
    assert [result.target_package for result in client.automation_results] == [
        "com.instagram.android"
    ]
    assert client.page_limits["sms_content_provider"] == 64
    assert client.page_limits["contacts_content_provider"] == 100
    assert client.page_limits["accessibility_visible_ui"] == 50
    assert client.page_limits["notification_listener"] == 50
    assert client.page_limits["media_store_image"] == 100


@pytest.mark.unit
async def test_phase7_runner_waits_for_optional_review_and_then_continues(
    tmp_path: Path,
) -> None:
    final = InventoryRunV1.model_validate(run_payload("complete", source_state="complete"))
    client = FakeInventoryClient(final)
    repository = FakeSelectionRepository()

    original_start = client.start_selection

    async def start_review(*args, **kwargs):
        response = await original_start(*args, **kwargs)
        client.selection_run = response.body.model_copy(
            update={"state": "awaiting_review", "confirmed_at": None}
        )
        return SimpleNamespace(body=client.selection_run)

    async def confirm_on_poll(*_args, **_kwargs):
        assert client.selection_run is not None
        client.selection_run = client.selection_run.model_copy(
            update={"state": "confirmed", "confirmed_at": TIMESTAMP}
        )
        return SimpleNamespace(body=client.selection_run)

    client.start_selection = start_review
    client.selection_status = confirm_on_poll

    runner = Phase5AndroidAgentRunner(
        FakeBootstrap(),
        runtime_registry=FakeRegistry(),
        client_factory=lambda _port, _token: client,
        selection_repository=repository,
        transfer=FakeTransfer(tmp_path),
    )

    result = await runner.acquire(
        acquisition_context(AcquisitionMode.QUICK, review_candidates=True)
    )

    assert result.method.endswith("+selection_confirmed+android_agent_direct_manifest")
    assert any(run.state == "awaiting_review" for run in repository.runs)
    assert repository.runs[-1].state == "confirmed"
