from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.acquisition.agent_client import AgentClient
from app.acquisition.gmail_service import GmailAcquisitionService, _render_email_html
from app.acquisition.contracts import AcquisitionResult, ProviderKind
from app.acquisition.runtime import agent_runtime_registry
from app.core import config
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode, DeviceType, Scenario
from app.services import acquisition as acquisition_service
from app.services.acquisition import index_staging
from app.services.analysis import analyze_session
from app.services.gallery import ACCESS_ALL, list_albums, list_items


async def _create_test_session(session_id: str, mode: str = "quick") -> None:
    now = utcnow()
    await db.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_by,
            review_candidates, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "android-test",
            "android",
            "Tes Gmail",
            mode,
            "lulus",
            "completed",
            json.dumps({"phase": "completed", "percent": 100, "message": "Selesai"}),
            json.dumps({}),
            "LULUS",
            None,
            None,
            0,
            now,
            now,
        ),
    )


@pytest.mark.asyncio
async def test_gmail_simulated_acquisition(client, tmp_path: Path) -> None:
    session_id = "session-gmail-sim-001"
    await _create_test_session(session_id)

    staging = tmp_path / session_id
    staging.mkdir(parents=True, exist_ok=True)

    gmail_svc = GmailAcquisitionService()
    count, records = await gmail_svc.acquire(
        session_id=session_id,
        staging=staging,
        mode=AcquisitionMode.QUICK,
        simulated=True,
    )

    assert count >= 3
    assert (staging / "email").is_dir()
    html_files = list((staging / "email").glob("*.html"))
    assert len(html_files) >= 3

    # Check database records
    crawl_rows = await db.fetchall(
        "SELECT * FROM crawl_records WHERE session_id = ? AND source_kind = 'email'",
        (session_id,),
    )
    assert len(crawl_rows) >= 3

    artifact_rows = await db.fetchall(
        "SELECT * FROM crawl_artifacts WHERE session_id = ? AND source_kind = 'email'",
        (session_id,),
    )
    assert len(artifact_rows) >= 3


@pytest.mark.asyncio
async def test_gmail_gallery_filters_and_unflagged_in_all(client, tmp_path: Path) -> None:
    session_id = "session-gmail-gallery-002"
    await _create_test_session(session_id)

    staging = tmp_path / session_id
    staging.mkdir(parents=True, exist_ok=True)

    gmail_svc = GmailAcquisitionService()
    count, _ = await gmail_svc.acquire(
        session_id=session_id,
        staging=staging,
        mode=AcquisitionMode.QUICK,
        simulated=True,
    )

    async def _nop(*args, **kwargs):
        pass

    # Index staging into files table
    indexed, _ = await index_staging(session_id, staging, _nop)
    assert indexed >= 3

    # Analyze session
    analyzed, findings, _, _ = await analyze_session(
        session_id, staging, AcquisitionMode.QUICK, _nop
    )
    assert analyzed >= 3

    # Test gallery albums listing
    albums = await list_albums(session_id, AcquisitionMode.QUICK)
    album_ids = {a.id: a for a in albums}

    # "Semua" (ACCESS_ALL) must include email items
    assert ACCESS_ALL in album_ids
    assert album_ids[ACCESS_ALL].count >= 3

    # "Email" origin album must exist with label "Email"
    assert "email" in album_ids
    assert album_ids["email"].label == "Email"
    assert album_ids["email"].count >= 3

    # Test listing items in "email" album
    email_items = await list_items(session_id, AcquisitionMode.QUICK, "email", 1, 10)
    assert email_items.total >= 3
    for item in email_items.items:
        assert item.album == "Email"
        assert item.album_key == "email"
        assert not item.preview_path.endswith(".json")
        assert item.preview_text

    # Test listing items in "all" album
    all_items = await list_items(session_id, AcquisitionMode.QUICK, ACCESS_ALL, 1, 10)
    assert all_items.total >= 3
    email_in_all = [item for item in all_items.items if item.album_key == "email"]
    assert len(email_in_all) >= 3


def test_email_html_preview_is_readable_and_does_not_execute_source_html() -> None:
    rendered = _render_email_html(
        subject="Pemberitahuan",
        sender="sender@example.test",
        to="user@example.test",
        date_str="2026-08-20",
        labels=["INBOX"],
        body_text="",
        body_html="<style>body{display:none}</style><script>alert('x')</script><p>Isi email aman</p>",
        attachments=["dokumen.pdf"],
    )
    assert "Isi email aman" in rendered
    assert "<script" not in rendered
    assert "alert('x')" not in rendered
    assert "body{display:none}" not in rendered
    assert "dokumen.pdf" in rendered


@pytest.mark.asyncio
async def test_gmail_fallback_uses_bound_runtime_client(client, monkeypatch, tmp_path: Path) -> None:
    staging = tmp_path / "fallback"
    staging.mkdir()
    received: dict[str, str | None] = {}

    async def fake_provider(_self, _context):
        return AcquisitionResult(staging, 1, 10.0, "legacy", ProviderKind.ANDROID_LEGACY)

    async def fake_runtime(_session_id: str):
        return SimpleNamespace(
            session_id=_session_id,
            serial="android-live",
            forward_host_port=38471,
            token="a" * 32,
            token_expires_at="2099-01-01T00:00:00+00:00",
            google_account=None,
            google_token=None,
        )

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def list_google_accounts(self, _session_id: str, **_kwargs):
            return [SimpleNamespace(name="user@example.test")]

        async def get_google_auth_token(self, _session_id: str, _account: str, **_kwargs):
            return "gmail-token"

    async def fake_gmail(self, **kwargs):
        del self
        received["token"] = kwargs["token"]
        received["account"] = kwargs["account_name"]
        return 2, []

    monkeypatch.setattr(
        "app.acquisition.providers.registry.AcquisitionProviderRegistry.acquire",
        fake_provider,
    )
    async def fake_ensure(**kwargs):
        return "user@example.test", "gmail-token"

    monkeypatch.setattr(agent_runtime_registry, "get", fake_runtime)
    monkeypatch.setattr("app.acquisition.agent_client.AgentClient", FakeClient)
    monkeypatch.setattr("app.acquisition.gmail_oauth.ensure_gmail_oauth", fake_ensure)
    async def fake_bind(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.acquisition.runtime.agent_runtime_registry.bind", fake_bind)
    monkeypatch.setattr(GmailAcquisitionService, "acquire", fake_gmail)
    monkeypatch.setattr(config.settings, "android_agent_enabled", True)
    monkeypatch.setattr(config.settings, "android_recovery_enabled", False)
    monkeypatch.setattr(config.settings, "gmail_acquisition_enabled", True)

    async def skip_whatsapp(self, **_kwargs):
        del self
        return None

    monkeypatch.setattr(
        "app.acquisition.whatsapp_backup.WhatsAppBackupAcquisitionService.acquire",
        skip_whatsapp,
    )

    async def on_progress(*_args, **_kwargs):
        return None

    result = await acquisition_service.acquire_dispatch(
        session_id="session-gmail-fallback",
        device_id="android-live",
        device_type=DeviceType.ANDROID,
        simulated=False,
        mode=AcquisitionMode.QUICK,
        scenario=Scenario.LULUS,
        file_count=0,
        on_progress=on_progress,
    )
    assert received == {"token": "gmail-token", "account": "user@example.test"}
    assert result == (staging, 3, 10.0, "legacy+gmail_api")


@pytest.mark.asyncio
async def test_gmail_rest_api_mock(client, tmp_path: Path) -> None:
    session_id = "session-gmail-api-mock-003"
    await _create_test_session(session_id)

    staging = tmp_path / session_id
    staging.mkdir(parents=True, exist_ok=True)

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/messages?" in url_str or url_str.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"id": "msg_live_101", "threadId": "t101"},
                    ]
                },
            )
        if "/messages/msg_live_101/attachments/att_999" in url_str:
            raw_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
            return httpx.Response(
                200,
                json={"data": base64.urlsafe_b64encode(raw_png).decode()},
            )
        if "/messages/msg_live_101" in url_str:
            return httpx.Response(
                200,
                json={
                    "id": "msg_live_101",
                    "internalDate": "1711099425000",
                    "labelIds": ["INBOX", "STARRED"],
                    "snippet": "Pemberitahuan resmi hasil seleksi kompetensi digital.",
                    "payload": {
                        "mimeType": "multipart/mixed",
                        "headers": [
                            {"name": "Subject", "value": "Pengumuman Hasil Seleksi Digital"},
                            {"name": "From", "value": "panitia@seleksi.id"},
                            {"name": "To", "value": "kandidat@gmail.com"},
                            {"name": "Date", "value": "Fri, 22 Mar 2024 16:23:45 +0700"},
                        ],
                        "parts": [
                            {
                                "mimeType": "text/html",
                                "body": {
                                    "data": base64.urlsafe_b64encode(
                                        b"<h3>Selamat!</h3><p>Anda dinyatakan lulus seleksi administrasi.</p>"
                                    ).decode()
                                },
                            },
                            {
                                "mimeType": "image/png",
                                "filename": "sertifikat_kelulusan.png",
                                "body": {"attachmentId": "att_999", "size": 100},
                            },
                        ],
                    },
                },
            )
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(handler)

    gmail_svc = GmailAcquisitionService()

    async with httpx.AsyncClient(transport=mock_transport) as http_client:
        msg_data = (await http_client.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/msg_live_101")).json()
        saved = await gmail_svc._process_message(
            client=http_client,
            session_id=session_id,
            email_dir=staging / "email",
            msg_data=msg_data,
            account_name="kandidat@gmail.com",
        )

    assert len(saved) == 1
    assert (staging / "email" / "email_msg_live_101.html").is_file()
    assert (staging / "email" / "msg_live_101_sertifikat_kelulusan.png").is_file()


@pytest.mark.asyncio
async def test_agent_client_google_accounts_mock(monkeypatch) -> None:
    session_id = "session-agent-acc-mock-004"
    monkeypatch.setattr(
        "app.acquisition.agent_client.settings.gmail_client_id",
        "667923128803-mock-client.apps.googleusercontent.com",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        req_id = request.headers.get("x-request-id", "mock-req-id")
        headers = {"x-request-id": req_id}
        if "/v1/accounts/google/token" in url_str:
            body = json.loads(request.content.decode())
            captured.update(body)
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "session_id": session_id,
                    "account_name": body["account_name"],
                    "token": "ya29.mock_token_test_12345",
                    "scope": body.get("scope"),
                },
            )
        if "/v1/accounts/google" in url_str:
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "session_id": session_id,
                    "accounts": [
                        {"name": "test.user@gmail.com", "type": "com.google"},
                    ],
                },
            )
        return httpx.Response(404, headers=headers)

    mock_transport = httpx.MockTransport(handler)
    client = AgentClient(
        host_port=38471,
        token="a" * 32,
        transport=mock_transport,
    )

    accounts = await client.list_google_accounts(session_id)
    assert len(accounts) == 1
    assert accounts[0].name == "test.user@gmail.com"

    token = await client.get_google_auth_token(session_id, "test.user@gmail.com")
    assert token == "ya29.mock_token_test_12345"
    assert captured["client_id"] == "667923128803-mock-client.apps.googleusercontent.com"


@pytest.mark.asyncio
async def test_gmail_canonical_json_has_source_created_at(client, tmp_path: Path) -> None:
    session_id = "session-gmail-source-time-005"
    await _create_test_session(session_id)

    staging = tmp_path / session_id
    staging.mkdir(parents=True, exist_ok=True)

    gmail_svc = GmailAcquisitionService()
    await gmail_svc.acquire(
        session_id=session_id,
        staging=staging,
        mode=AcquisitionMode.QUICK,
        simulated=True,
    )

    row = await db.fetchone(
        "SELECT canonical_json FROM crawl_records WHERE session_id = ? AND source_kind = 'email' LIMIT 1",
        (session_id,),
    )
    assert row is not None
    payload = json.loads(row["canonical_json"])
    assert payload["source_created_at"]
    assert payload["source_modified_at"]
    assert payload["metadata"]["date_header"]


@pytest.mark.asyncio
async def test_gmail_acquire_uses_session_reference_for_time_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_id = "session-gmail-reference-006"
    staging = tmp_path / session_id
    staging.mkdir(parents=True, exist_ok=True)
    reference = datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc)
    captured: dict[str, str] = {}

    class MockAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args
            return False

        async def get(self, url: str, **kwargs):
            if "/messages" in url:
                captured["query"] = kwargs.get("params", {}).get("q", "")
            return httpx.Response(200, json={"messages": []})

    monkeypatch.setattr("app.acquisition.gmail_service.httpx.AsyncClient", MockAsyncClient)
    gmail_svc = GmailAcquisitionService()
    count, _ = await gmail_svc.acquire(
        session_id=session_id,
        staging=staging,
        mode=AcquisitionMode.QUICK,
        token="ya29.test",
        account_name="user@gmail.com",
        reference=reference,
    )
    assert count == 0
    assert captured["query"] == "after:2025/11/15"


@pytest.mark.asyncio
async def test_ensure_gmail_oauth_publishes_awaiting_access(client, monkeypatch) -> None:
    from app.acquisition.errors import AcquisitionError
    from app.acquisition.gmail_oauth import ensure_gmail_oauth
    from app.models.schemas import SessionStatus

    messages: list[tuple[SessionStatus, str]] = []

    async def on_progress(phase, _percent, message, **_kwargs):
        messages.append((phase, message))

    class FakeClient:
        async def list_google_accounts(self, *_a, **_k):
            return [SimpleNamespace(name="user@gmail.com")]

        async def get_google_auth_token(self, *_a, **_k):
            return "ya29.live"

    monkeypatch.setattr("app.acquisition.gmail_oauth.settings.gmail_client_id", "client-id")

    async def no_peek(*_a, **_k):
        return None

    async def no_fetch(*_a, **kwargs):
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            await on_progress(
                SessionStatus.AWAITING_ACCESS,
                52.0,
                "Menunggu otorisasi Gmail di perangkat",
            )
        return None

    monkeypatch.setattr("app.acquisition.gmail_oauth.peek_gmail_oauth_token", no_peek)
    monkeypatch.setattr("app.acquisition.gmail_oauth.fetch_gmail_oauth_token", no_fetch)
    with pytest.raises(AcquisitionError) as exc:
        await ensure_gmail_oauth(
            client=FakeClient(),
            session_id="session-oauth-gate",
            serial="device-1",
            adb=None,
            on_progress=on_progress,
        )
    assert exc.value.category.value == "awaiting_user"
    assert any(phase == SessionStatus.AWAITING_ACCESS for phase, _ in messages)
