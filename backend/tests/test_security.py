"""Security hardening regression tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.auth import BCRYPT_MARKER, hash_password, reset_login_rate_limits, verify_password
from app.services.reports import report_to_html


@pytest.mark.unit
def test_bcrypt_hash_and_verify():
    digest, salt = hash_password("Ops@2026")
    assert salt == BCRYPT_MARKER
    assert digest.startswith("$2")
    assert verify_password("Ops@2026", digest, salt)
    assert not verify_password("wrong", digest, salt)


@pytest.mark.unit
def test_legacy_sha256_still_verifies():
    import hashlib

    salt = "abcd1234"
    password = "legacy"
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    assert verify_password(password, digest, salt)
    assert not verify_password("nope", digest, salt)


@pytest.mark.unit
def test_report_html_escapes_xss():
    report = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "session": {
            "id": "sess-1",
            "label": '<img src=x onerror=alert(1)>',
            "device_id": "dev<script>",
            "device_type": "android",
            "mode": "quick",
            "acquisition_method": "sim",
            "recommendation": "LULUS",
        },
        "metrics": {
            "files": 1,
            "bytes": 10,
            "findings": 1,
            "timing": {"t_acquire_ms": 1, "t_analyze_ms": 1, "t_total_ms": 2},
        },
        "breakdown": {"by_category": {"<b>evil</b>": 1}},
        "findings": [
            {
                "label": "<script>alert(1)</script>",
                "category": "x",
                "source": "y",
                "path": "/tmp/\"onclick=alert(1)",
                "confidence": 0.9,
                "layer": "L1",
            }
        ],
    }
    html = report_to_html(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "&lt;img" in html
    assert "Ringkasan eksekutif" in html
    assert "Identitas peserta seleksi" in html
    assert "Laporan Hasil Analisis" in html

    printed = report_to_html(report, print_mode=True)
    assert "print-doc" in printed
    assert "window.print" in printed
    assert "Cetak / Simpan PDF" in printed
    assert "Pengesahan pimpinan" in printed
    assert "Data akun" not in printed  # section kosong disembunyikan


@pytest.mark.unit
def test_report_html_shortens_hash_paths_and_method_chain():
    report = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "session": {
            "id": "sess-1",
            "label": "Nella 02",
            "device_id": "dev-1",
            "device_type": "android",
            "mode": "quick",
            "acquisition_method": (
                "android_agent_inventory_partial+preprocessing_partial+"
                "selection_confirmed+android_agent_direct_manifest+"
                "android_recovery_quick_partial"
            ),
            "recommendation": "LULUS",
        },
        "metrics": {"files": 1, "bytes": 10, "findings": 1, "timing": {}},
        "breakdown": {"by_category": {}},
        "findings": [
            {
                "label": "799c80e00b477d50a4aeb9b88c71324a.jpg",
                "category": "konten_visual",
                "source": "image",
                "path": "/data/staging/abc/DCIM/799c80e00b477d50a4aeb9b88c71324a.jpg",
                "confidence": 0.9,
                "layer": "L4",
            }
        ],
    }
    html = report_to_html(report)
    assert "799c80e00b477d50a4aeb9b88c71324a.jpg" not in html
    assert "DCIM/Foto" in html
    assert "Transfer dari HP" in html
    assert "Recovery sampah Android" in html
    assert html.count(" + ") == 0


@pytest.mark.api
async def test_health_requires_auth(anon_client: AsyncClient):
    res = await anon_client.get("/api/v1/health")
    assert res.status_code == 401


@pytest.mark.api
async def test_ready_public(anon_client: AsyncClient):
    res = await anon_client.get("/api/v1/ready")
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "ok"
    assert "app" in body


@pytest.mark.api
async def test_report_rejects_query_token(anon_client: AsyncClient):
    login = await anon_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin@2026"},
    )
    token = login.json()["token"]
    res = await anon_client.get(
        "/api/v1/sessions/does-not-exist/report?format=json&access_token=" + token,
    )
    assert res.status_code == 401


@pytest.mark.api
async def test_login_rate_limit(anon_client: AsyncClient):
    reset_login_rate_limits()
    statuses = []
    for _ in range(10):
        res = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        statuses.append(res.status_code)
    assert 401 in statuses
    assert 429 in statuses
    assert statuses[-1] == 429
    reset_login_rate_limits()
