"""Pipeline accuracy — sensitifitas / false positive (kriteria PoC)."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import wait_session


def _count_planted(staging: Path) -> int:
    return sum(1 for _ in staging.rglob("*.risk"))


@pytest.mark.api
@pytest.mark.acceptance
async def test_sensitivity_planted_findings(client: AsyncClient):
    """Mesin harus mendeteksi >= 90% skenario planted (sensitivitas PoC)."""
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "tidak_lulus",
            "file_count": 500,
            "label": "Sensitivity",
        },
    )
    assert res.status_code == 200
    sid = res.json()["id"]
    final = await wait_session(client, sid)
    assert final["status"] == "completed"

    staging = settings.staging_dir / sid
    planted = _count_planted(staging)
    assert planted > 0

    findings = (await client.get(f"/api/v1/sessions/{sid}/findings?page_size=500")).json()
    # Setiap file planted idealnya >=1 finding; hitungkan file unik yang punya finding
    found_paths = {f["path"] for f in findings["items"]}
    planted_files = {p.name for p in staging.rglob("*.risk")}
    # .risk sidecar name = "{original}.risk" → original = stem with possible multi suffix
    matched = 0
    for risk_name in planted_files:
        original = risk_name[: -len(".risk")] if risk_name.endswith(".risk") else risk_name
        if any(Path(p).name == original for p in found_paths):
            matched += 1

    sensitivity = matched / planted
    assert sensitivity >= 0.90, f"Sensitivitas {sensitivity:.2%} < 90% (matched={matched}, planted={planted})"


@pytest.mark.api
@pytest.mark.acceptance
async def test_specificity_lulus_false_positive(client: AsyncClient):
    """Skenario LULUS: false positive tidak boleh > 5% dari total file dianalisis."""
    file_count = 400
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-iphone-01",
            "device_type": "ios",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": file_count,
            "label": "Specificity",
        },
    )
    final = await wait_session(client, res.json()["id"])
    assert final["status"] == "completed"
    analyzed = max(final["progress"]["files_analyzed"], 1)
    fp = final["progress"]["findings_count"]
    fp_rate = fp / analyzed
    assert fp_rate <= 0.05, f"False positive rate {fp_rate:.2%} > 5% (fp={fp}, analyzed={analyzed})"
    assert final["recommendation"] == "LULUS"
