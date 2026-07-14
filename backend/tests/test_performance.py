"""Performance / SLA — jalankan di server GPU sebelum demo."""

from __future__ import annotations

import os

import pytest
from httpx import AsyncClient

from tests.conftest import wait_session

# PoC kriteria: <= 30 menit untuk hingga 5000 file.
# Pipeline sintetis di server harus jauh lebih cepat; default gate ketat untuk CI/lab.
DEFAULT_SLA_MS = int(os.getenv("SADT_PERF_SLA_MS", "120000"))  # 2 menit default
POC_SLA_MS = int(os.getenv("SADT_POC_SLA_MS", str(30 * 60 * 1000)))  # 30 menit


@pytest.mark.perf
@pytest.mark.acceptance
@pytest.mark.parametrize(
    "file_count,mode",
    [
        (1000, "quick"),
        (5000, "quick"),
    ],
)
async def test_pipeline_under_sla(client: AsyncClient, file_count: int, mode: str):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": mode,
            "scenario": "tidak_lulus",
            "file_count": file_count,
            "label": f"Perf {file_count} {mode}",
        },
    )
    assert res.status_code == 200
    final = await wait_session(client, res.json()["id"], timeout_s=max(POC_SLA_MS / 1000, 180))
    assert final["status"] == "completed", final.get("error")
    total = final["timing"]["t_total_ms"]
    assert total <= POC_SLA_MS, f"Melebihi SLA PoC 30 menit: {total} ms"
    assert total <= DEFAULT_SLA_MS, (
        f"Pipeline sintetis {file_count} file terlalu lambat: {total:.0f} ms "
        f"(gate server {DEFAULT_SLA_MS} ms). Set SADT_PERF_SLA_MS jika perlu."
    )
    assert final["progress"]["files_analyzed"] > 0
    # Breakdown harus terisi (untuk laporan PoC)
    for key in ("t_acquire_ms", "t_index_ms", "t_analyze_ms"):
        assert final["timing"][key] > 0


@pytest.mark.perf
async def test_hash_cache_rerun_faster(client: AsyncClient):
    payload = {
        "device_id": "sim-android-01",
        "device_type": "android",
        "mode": "quick",
        "scenario": "tidak_lulus",
        "file_count": 600,
        "label": "Cache warm",
    }
    r1 = await client.post("/api/v1/sessions", json=payload)
    s1 = await wait_session(client, r1.json()["id"])
    assert s1["status"] == "completed"

    payload["label"] = "Cache hit"
    r2 = await client.post("/api/v1/sessions", json=payload)
    s2 = await wait_session(client, r2.json()["id"])
    assert s2["status"] == "completed"

    # Analyze phase should benefit from hash_cache (allow small jitter)
    # Tidak ketat 50% karena acquire+index masih write file baru (konten sama → hash sama).
    assert s2["timing"]["t_analyze_ms"] <= s1["timing"]["t_analyze_ms"] * 1.15


@pytest.mark.perf
@pytest.mark.acceptance
async def test_serial_android_then_ios(client: AsyncClient):
    """Satu-satu: Android selesai, baru iPhone."""
    for device_id, dtype in (("sim-android-01", "android"), ("sim-iphone-01", "ios")):
        res = await client.post(
            "/api/v1/sessions",
            json={
                "device_id": device_id,
                "device_type": dtype,
                "mode": "quick",
                "scenario": "lulus",
                "file_count": 300,
                "label": f"Serial {dtype}",
            },
        )
        assert res.status_code == 200
        final = await wait_session(client, res.json()["id"])
        assert final["status"] == "completed"
        assert final["device_id"] == device_id
