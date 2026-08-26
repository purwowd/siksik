from __future__ import annotations

from app.acquisition.source_app_hints import (
    infer_source_app,
    inferred_album_label,
    package_label,
)
from app.services.analysis import finding_attachment_row, media_siblings_by_record
from app.services.reports import (
    _record_social_scope,
    _record_source_app,
    _social_account_heading,
    _social_item_preview,
)


def test_infer_threads_and_whatsapp_from_gallery_hints() -> None:
    assert (
        infer_source_app(
            display_name="Screenshot_2026-07-01-22-37-55-172_com.instagram.barcelona.jpg",
            directory_hint="DCIM/Screenshots",
        )
        == "com.instagram.barcelona"
    )
    assert inferred_album_label(
        display_name="Screenshot_2026-08-18-21-26-51-586_com.facebook.katana.jpg",
        directory_hint="DCIM/Screenshots",
        path="media_image/x.jpg",
    ) == "Facebook (screenshot)"
    assert (
        infer_source_app(
            display_name="IMG-20260628-WA0001.jpg",
            directory_hint="Pictures/WhatsApp",
        )
        == "com.whatsapp"
    )
    assert package_label("com.whatsapp") == "WhatsApp"


def test_infer_ignores_bank_and_carrier_screenshots() -> None:
    assert (
        infer_source_app(
            display_name="Screenshot_2026-06-11-19-49-02-681_com.bca.jpg",
            directory_hint="DCIM/Screenshots",
        )
        is None
    )
    assert (
        infer_source_app(
            display_name="Screenshot_2026-06-02-08-16-26-693_com.smartfren.jpg",
        )
        is None
    )


def test_report_maps_gallery_trace_to_device_media() -> None:
    record = {
        "source_kind": "media_image",
        "source_app": None,
        "metadata": {
            "display_name": "Screenshot_2026-07-01-22-37-55-172_com.instagram.barcelona.jpg",
            "directory_hint": "DCIM/Screenshots",
        },
        "normalized_text": "LOWONGAN KERJA",
    }
    package = _record_source_app(record)
    assert package == "com.instagram.barcelona"
    assert _record_social_scope(record, package) == "device_media"
    assert "barcelona" in (_social_item_preview("device_media", record) or "")
    assert _social_account_heading(
        {"platform": "Threads", "display_name": "Jejak di galeri HP", "username": None}
    ) == "Threads · Jejak di galeri HP"


def test_finding_attachment_prefers_source_binary() -> None:
    record = {
        "id": "json-1",
        "source": "media_image",
        "path": "media_image/record_abc.siksik-record.json",
        "mime": "application/vnd.siksik.crawl-record+json",
        "meta_json": '{"crawl_record_id":"record_abc","crawl_artifact_role":"canonical_record"}',
    }
    binary = {
        "id": "jpg-1",
        "source": "media_image",
        "path": "media_image/record_abc__artifact_record_abc_bin.jpg",
        "mime": "image/jpeg",
        "meta_json": '{"crawl_record_id":"record_abc","crawl_artifact_role":"source_binary"}',
    }
    siblings = media_siblings_by_record([record, binary])
    target = finding_attachment_row(record, siblings)
    assert target["id"] == "jpg-1"
    assert target["path"].endswith(".jpg")
