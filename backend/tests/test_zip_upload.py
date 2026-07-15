"""ZIP upload analysis (tanpa akuisisi USB)."""

from __future__ import annotations

import io
import zipfile

import pytest
from httpx import AsyncClient

from tests.conftest import wait_session


def _make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("DCIM/Camera/photo1.jpg", b"\xff\xd8\xff\xd9fakejpeg")
        zf.writestr("Download/note.txt", b"laporan makar rahasia rencana")
        zf.writestr("Movies/clip.mp4", b"ftypisomfake")
    return buf.getvalue()


@pytest.mark.api
@pytest.mark.acceptance
async def test_session_from_zip(client: AsyncClient):
    files = {"file": ("adb_dump.zip", _make_zip(), "application/zip")}
    data = {"mode": "quick", "label": "ZIP test"}
    res = await client.post("/api/v1/sessions/from-zip", files=files, data=data)
    assert res.status_code == 200, res.text
    sid = res.json()["id"]
    final = await wait_session(client, sid)
    assert final["status"] == "completed"
    assert final["progress"]["acquisition_method"] == "zip_upload"
    assert final["progress"]["files_pulled"] >= 2
    findings = (await client.get(f"/api/v1/sessions/{sid}/findings")).json()
    assert findings["total"] >= 1
    # "makar" in note.txt → finding pending, belum lulus
    assert final["recommendation"] == "MENUNGGU REVIEW"
    fid = findings["items"][0]["id"]
    await client.patch(f"/api/v1/findings/{fid}", json={"review_status": "confirmed"})
    after = (await client.get(f"/api/v1/sessions/{sid}")).json()
    assert after["recommendation"] == "TIDAK LULUS"

@pytest.mark.api
async def test_zip_rejects_non_zip(client: AsyncClient):
    files = {"file": ("notes.txt", b"hello", "text/plain")}
    res = await client.post("/api/v1/sessions/from-zip", files=files, data={"mode": "quick"})
    assert res.status_code == 400
