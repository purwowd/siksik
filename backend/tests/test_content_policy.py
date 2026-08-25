from __future__ import annotations

import pytest

from app.core import config
from app.services import content_policy, content_visual


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Acara LGBT Pride Month dan bendera transgender", {"lgbt_content"}),
        ("Kampanye pemilu: coblos paslon nomor dua", {"political_campaign"}),
        ("Demo mahasiswa dan aksi unjuk rasa di depan gedung", {"demonstration"}),
        ("Ayo serbu dan bakar gedung pemerintah malam ini", {"incitement"}),
        ("Materi propaganda ekstremis ISIS dan ajakan baiat ISIS", {"extremism"}),
        ("Basmi kaum gay, mereka tidak layak hidup", {"lgbt_content", "hate_speech"}),
        ("Presiden itu diktator tolol", {"political_insult"}),
    ],
)
def test_explicit_text_categories(text: str, expected: set[str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "content_text_model", "")

    findings = content_policy.findings_from_text(
        text,
        backend="test-ocr",
        image_context=False,
    )

    assert expected <= {finding["category"] for finding in findings}


@pytest.mark.unit
def test_image_political_text_can_emit_meme_and_insult(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "content_text_model", "")

    findings = content_policy.findings_from_text(
        "MEME: Presiden boneka asing dan diktator",
        backend="test-ocr",
        image_context=True,
    )

    assert {finding["category"] for finding in findings} >= {
        "political_meme",
        "political_insult",
    }


@pytest.mark.unit
def test_cross_detector_fusion_keeps_one_finding_and_combines_evidence():
    merged = content_policy.merge_content_findings(
        [
            {
                "category": "incitement",
                "label": "Incitement / ajakan provokatif",
                "confidence": 0.86,
                "layer_origin": "L3",
                "evidence": "[easyocr] ayo serbu gedung pemerintah",
            },
            {
                "category": "incitement_violent",
                "label": "Qwen candidate",
                "confidence": 0.91,
                "layer_origin": "L3",
                "evidence": "[qwen2.5-vl] ajakan menyerbu gedung",
            },
        ]
    )

    assert len(merged) == 1
    assert merged[0]["category"] == "incitement"
    assert merged[0]["label"] == "Incitement / ajakan provokatif"
    assert merged[0]["confidence"] == 0.91
    assert "easyocr" in merged[0]["evidence"]
    assert "qwen2.5-vl" in merged[0]["evidence"]


@pytest.mark.unit
def test_fusion_keeps_distinct_categories():
    merged = content_policy.merge_content_findings(
        [
            {
                "category": "lgbt_content",
                "label": "LGBT text/flag",
                "confidence": 0.8,
                "layer_origin": "L3",
                "evidence": "flag",
            },
            {
                "category": "hate_speech",
                "label": "Ujaran kebencian",
                "confidence": 0.9,
                "layer_origin": "L3",
                "evidence": "hate",
            },
        ]
    )

    assert {finding["category"] for finding in merged} == {"lgbt_content", "hate_speech"}


@pytest.mark.unit
def test_fusion_does_not_collapse_legacy_findings_with_distinct_evidence():
    merged = content_policy.merge_content_findings(
        [
            {
                "category": "narkotika",
                "label": "Indikasi: narkoba",
                "confidence": 0.8,
                "layer_origin": "L1",
                "evidence": "pesan pertama",
            },
            {
                "category": "narkotika",
                "label": "Indikasi: narkoba",
                "confidence": 0.8,
                "layer_origin": "L1",
                "evidence": "pesan kedua",
            },
        ]
    )

    assert [finding["evidence"] for finding in merged] == [
        "pesan pertama",
        "pesan kedua",
    ]


@pytest.mark.unit
def test_visual_pairs_are_multilabel_and_use_hard_negative(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(config.settings, "content_detection_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.7)
    # Five positive/negative pairs. LGBT and demonstration have strong positive deltas.
    monkeypatch.setattr(
        content_visual,
        "_score",
        lambda _path, _prompts: (
            [3.0, 0.0, 0.0, 2.0, 0.0, 2.0, 3.0, 0.0, 0.0, 3.0],
            "fake-visual",
        ),
    )
    image = tmp_path / "visual.png"
    image.write_bytes(b"not-read-by-mock")

    findings = content_visual.analyze_image(image)

    assert {finding["category"] for finding in findings} == {
        "lgbt_content",
        "demonstration",
    }
