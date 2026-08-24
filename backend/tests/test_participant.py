"""Identitas peserta seleksi — wajib di start + bisa diubah setelahnya."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.db import db
from tests.conftest import participant_payload, wait_session


@pytest.mark.api
async def test_start_requires_participant(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 20,
        },
    )
    assert res.status_code == 422


@pytest.mark.api
async def test_nik_must_be_16_digits(client: AsyncClient):
    res = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 20,
            "participant": participant_payload(nik="123"),
        },
    )
    assert res.status_code == 422


@pytest.mark.api
async def test_duplicate_registration_same_day(client: AsyncClient):
    payload = {
        "device_id": "sim-android-01",
        "device_type": "android",
        "mode": "quick",
        "scenario": "lulus",
        "file_count": 20,
        "participant": participant_payload(registration_no="DUP-001"),
    }
    first = await client.post("/api/v1/sessions", json=payload)
    assert first.status_code == 200
    await wait_session(client, first.json()["id"])

    second = await client.post(
        "/api/v1/sessions",
        json={
            **payload,
            "participant": participant_payload(registration_no="dup-001"),
        },
    )
    assert second.status_code == 409
    assert "no. peserta" in second.json()["detail"].lower()


@pytest.mark.api
async def test_failed_session_does_not_block_retry(client: AsyncClient):
    payload = {
        "device_id": "sim-android-01",
        "device_type": "android",
        "mode": "quick",
        "scenario": "lulus",
        "file_count": 20,
        "participant": participant_payload(registration_no="RETRY-001"),
    }
    first = await client.post("/api/v1/sessions", json=payload)
    assert first.status_code == 200
    sid = first.json()["id"]
    await wait_session(client, sid)
    await db.execute("UPDATE sessions SET status = 'failed' WHERE id = ?", (sid,))

    retry = await client.post("/api/v1/sessions", json=payload)
    assert retry.status_code == 200, retry.text


@pytest.mark.api
async def test_update_participant(client: AsyncClient):
    start = await client.post(
        "/api/v1/sessions",
        json={
            "device_id": "sim-android-01",
            "device_type": "android",
            "mode": "quick",
            "scenario": "lulus",
            "file_count": 20,
            "participant": participant_payload(full_name="Nama Lama", registration_no="UPD-001"),
        },
    )
    assert start.status_code == 200
    sid = start.json()["id"]
    await wait_session(client, sid)

    patched = await client.patch(
        f"/api/v1/sessions/{sid}/participant",
        json={
            "participant": {
                "full_name": "Nama Baru",
                "registration_no": "UPD-001",
                "nik": "3201010101900001",
                "organization": "Pemda Demo",
            }
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["participant"]["full_name"] == "Nama Baru"
    assert body["participant"]["nik"] == "3201010101900001"
    assert body["label"] == "Nama Baru · UPD-001"
