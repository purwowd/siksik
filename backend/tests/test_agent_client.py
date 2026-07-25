from __future__ import annotations

import json

import httpx
import pytest

from app.acquisition.agent_client import AgentClient, AgentClientConfig
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.models.schemas import AcquisitionMode
from app.selection.policy import build_selection_policy


def capability_payload() -> dict[str, object]:
    granted = {"state": "granted", "required_for_full": False}
    unavailable = {"state": "unavailable", "required_for_full": True}
    return {
        "schema_version": 1,
        "agent_version": "0.2.0",
        "agent_build_sha256": "a" * 64,
        "api_version": "1.0",
        "api_port": 38471,
        "android_api_level": 35,
        "package_name": "com.siksik.agent",
        "source_capabilities": {
            "media_image": granted,
            "visible_ui": unavailable,
        },
        "preprocessing_capabilities": {"ocr": unavailable},
        "feature_capabilities": {"loopback_api": granted},
        "permission_states": {"read_media_images": granted},
        "special_access_states": {"accessibility": unavailable},
        "available_storage_bytes": 1024,
        "active_session_id": "session-001",
    }


def response(request: httpx.Request, status: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"X-Request-ID": request.headers["X-Request-ID"]},
    )


def health_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "session_id": "session-001",
        "state": "active",
        "agent_version": "0.2.0",
        "agent_build_sha256": "a" * 64,
        "api_version": "1.0",
        "api_port": 38471,
    }


@pytest.mark.unit
async def test_client_is_loopback_only_and_propagates_request_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response(
            request,
            200,
            capability_payload(),
        )

    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    result = await client.capabilities(request_id="request-client-001")

    assert result.request_id == "request-client-001"
    assert seen[0].url == httpx.URL("http://127.0.0.1:43210/v1/capabilities")
    assert seen[0].headers["X-Request-ID"] == "request-client-001"
    assert seen[0].headers["Authorization"] == "Bearer " + "t" * 32


@pytest.mark.unit
async def test_bootstrap_retries_one_stale_auth_response() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return response(
                request,
                401,
                {
                    "error": {
                        "code": "companion_auth_invalid",
                        "message": "stale runtime",
                        "retryable": False,
                        "request_id": request.headers["X-Request-ID"],
                    }
                },
            )
        return response(
            request,
            201,
            {"session_id": "session-001", "api_version": "1.0", "state": "active"},
        )

    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    result = await client.bootstrap("session-001", "1.0", request_id="request-client-002")
    assert result.body.state == "active"
    assert calls == 2


@pytest.mark.unit
async def test_bootstrap_sends_versioned_selection_policy_without_token_leak() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return response(
            request,
            201,
            {"session_id": "session-001", "api_version": "1.0", "state": "active"},
        )

    policy = build_selection_policy(AcquisitionMode.QUICK)
    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    await client.bootstrap(
        "session-001",
        "1.0",
        selection_policy=policy,
        review_candidates=True,
        request_id="request-client-selection",
    )

    assert seen[0]["selection_policy"]["policy_fingerprint"] == policy.policy_fingerprint
    assert seen[0]["review_candidates"] is True
    assert "token" not in str(seen[0]).lower()


@pytest.mark.unit
async def test_client_retries_timeout_with_bound() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fixture timeout", request=request)

    client = AgentClient(
        43210,
        "t" * 32,
        config=AgentClientConfig(timeout_seconds=0.1, max_attempts=2),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AcquisitionError) as captured:
        await client.capabilities(request_id="request-client-003")
    assert captured.value.category == ErrorCategory.AGENT_UNREACHABLE
    assert captured.value.retryable is True
    assert calls == 2


@pytest.mark.unit
async def test_client_rejects_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"X-Request-ID": request.headers["X-Request-ID"]},
        )

    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    with pytest.raises(AcquisitionError) as captured:
        await client.capabilities(request_id="request-client-004")
    assert captured.value.category == ErrorCategory.AGENT_INVALID_RESPONSE


@pytest.mark.unit
async def test_client_rejects_extra_response_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = capability_payload()
        payload["unexpected"] = True
        return response(
            request,
            200,
            payload,
        )

    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    with pytest.raises(AcquisitionError) as captured:
        await client.capabilities(request_id="request-client-005")
    assert captured.value.category == ErrorCategory.AGENT_INVALID_RESPONSE


@pytest.mark.unit
async def test_health_and_stop_use_authenticated_typed_routes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/health":
            return response(request, 200, health_payload())
        return response(
            request,
            200,
            {"session_id": "session-001", "api_version": "1.0", "state": "closed"},
        )

    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    health = await client.health(request_id="request-client-006")
    stopped = await client.stop("session-001", request_id="request-client-007")

    assert health.body.session_id == "session-001"
    assert stopped.body.state == "closed"
    assert [(item.method, item.url.path) for item in seen] == [
        ("GET", "/v1/health"),
        ("POST", "/v1/sessions/session-001/stop"),
    ]
    assert all(item.headers["Authorization"] == "Bearer " + "t" * 32 for item in seen)


@pytest.mark.unit
async def test_health_rejects_wrong_session_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = health_payload()
        payload["state"] = "ready"
        return response(request, 200, payload)

    client = AgentClient(43210, "t" * 32, transport=httpx.MockTransport(handler))
    with pytest.raises(AcquisitionError) as captured:
        await client.health(request_id="request-client-008")
    assert captured.value.category == ErrorCategory.AGENT_INVALID_RESPONSE
