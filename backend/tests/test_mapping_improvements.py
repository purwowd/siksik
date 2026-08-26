from __future__ import annotations

from app.acquisition.contact_identity import (
    annotate_contact_file_rows,
    canonical_phone,
    cluster_contact_ids,
    contact_cluster_keys,
)
from app.acquisition.device_identity import hints_from_document, merge_device_identity_hints
from app.acquisition.media_types import is_agent_self_capture
from app.services.gallery import is_gallery_media
from app.services.reports import report_to_html


def test_canonical_phone_collapses_indonesian_formats() -> None:
    assert canonical_phone("+62 (812) 34-56") == "+628123456"
    assert canonical_phone("08123456789") == "+628123456789"
    assert canonical_phone("628123456789") == "+628123456789"
    assert canonical_phone("+62 858-9934-3484") == "+6285899343484"


def test_contact_cluster_merges_shared_numbers() -> None:
    a = contact_cluster_keys(
        {"phones": [{"value": "0812-1111-1111"}, {"normalized_value": "+6281211111111"}]}
    )
    b = contact_cluster_keys({"phones": [{"value": "+62 812-1111-1111"}]})
    c = contact_cluster_keys({"phones": [{"value": "0813-0000-0000"}]})
    keep = cluster_contact_ids([("a", a), ("b", b), ("c", c)])
    assert keep["a"] == keep["b"]
    assert keep["c"] != keep["a"]


def test_annotate_contact_file_rows_flags_duplicates() -> None:
    def row(file_id: str, phones: list[str]) -> tuple:
        import json

        meta = {"contact_phones": phones, "display_name": file_id}
        return (file_id, "sid", "contact", f"contact/{file_id}.json", "application/json", 1, "", "pulled", 0, json.dumps(meta))

    rows = annotate_contact_file_rows(
        [
            row("keep", ["+6281211111111"]),
            row("dup", ["081211111111"]),
            row("other", ["+6281300000000"]),
        ]
    )
    metas = {item[0]: __import__("json").loads(item[9]) for item in rows}
    assert metas["dup"]["contact_duplicate"] is True
    assert metas["keep"]["contact_duplicate"] is False
    assert metas["other"]["contact_duplicate"] is False
    assert metas["dup"]["contact_keep_id"] == metas["keep"]["contact_keep_id"]


def test_cv_document_hints_name_phone_org() -> None:
    hint = hints_from_document(
        display_name="CV NELLA RACHMAWATI (1).pdf",
        normalized_text=(
            "Nella Rachmawati\nDocument Controller\n"
            "pengalaman sebagai admin document controller di PT Casuarina Harnessindo "
            "Pemalang selama 6 tahun\nContact\n0896-3856-2361\nrachmawatinella@gmail.com"
        ),
    )
    assert "Nella Rachmawati" in hint["names"]
    assert hint["phones"] == ["+6289638562361"]
    assert hint["emails"] == ["rachmawatinella@gmail.com"]
    assert hint["organization"] == "PT Casuarina Harnessindo Pemalang"
    merged = merge_device_identity_hints([hint])
    assert merged["names"] == ["Nella Rachmawati"]


def test_agent_self_capture_is_hidden_from_gallery() -> None:
    assert is_agent_self_capture("media_image/sadt_shot.png")
    assert is_agent_self_capture("x.bin", "sadt_shot.png")
    assert not is_agent_self_capture("media_image/photo.jpg", "IMG_001.jpg")
    assert not is_gallery_media(
        source="media_image",
        mime="image/png",
        path="media_image/sadt_shot.png",
        role="source_binary",
    )


def test_report_html_shows_device_name_hint() -> None:
    html = report_to_html(
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "session": {
                "id": "sess-1",
                "label": "Nella 02 · ASN-2026-002",
                "device_id": "dev",
                "device_type": "android",
                "mode": "quick",
                "acquisition_method": "android_agent_direct_manifest",
                "recommendation": "LULUS",
                "participant": {
                    "full_name": "Nella 02",
                    "registration_no": "ASN-2026-002",
                    "nik": None,
                    "organization": None,
                },
            },
            "device_identity": {
                "names": ["Nella Rachmawati"],
                "emails": ["rachmawatinella@gmail.com"],
                "phones": ["+6289638562361"],
                "organizations": ["PT Casuarina Harnessindo"],
                "nik_candidates": [],
                "sources": [
                    {
                        "name": "Nella Rachmawati",
                        "kind": "document",
                        "label": "CV NELLA RACHMAWATI (1).pdf",
                    }
                ],
            },
            "metrics": {
                "files": 10,
                "bytes": 100,
                "findings": 0,
                "contact_unique": 900,
                "contact_records": 1286,
                "sms_by_direction": {"received": 116, "sent": 0},
                "recovery": {"cache": 25, "trash": 0},
                "timing": {},
                "progress": {"recovery_state": "partial"},
            },
            "breakdown": {"by_category": {}, "by_source": {}},
            "findings": [],
        }
    )
    assert "Nama di perangkat" in html
    assert "Nella Rachmawati" in html
    assert "PT Casuarina Harnessindo (dari dokumen)" in html
    assert "900 unik · 1286 rekam" in html
    assert "116 masuk · 0 terkirim" in html
    assert "25 pratinjau cache" in html
