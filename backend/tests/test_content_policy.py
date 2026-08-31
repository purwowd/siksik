from __future__ import annotations

import pytest

from app.core import config
from app.services import content_policy, content_visual


def _visual_bank_logits(pair_values: list[float]) -> list[float]:
    """Expand [positive, negative, ...] into the current prompt-bank layout."""
    values: list[float] = []
    pairs = list(zip(pair_values[::2], pair_values[1::2], strict=True))
    for (_category, positives, negatives), (positive, negative) in zip(
        content_visual._PROMPT_BANKS,
        pairs,
        strict=True,
    ):
        values.extend([positive] * len(positives))
        values.extend([negative] * len(negatives))
    manipulated_positives, manipulated_negatives = (
        content_visual._MANIPULATED_POLITICAL_MEME_PROMPTS
    )
    values.extend([0.0] * (len(manipulated_positives) + len(manipulated_negatives)))
    satire_positives, satire_negatives = (
        content_visual._EXPLICIT_POLITICAL_SATIRE_PROMPTS
    )
    values.extend([0.0] * (len(satire_positives) + len(satire_negatives)))
    return values


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Acara LGBT Pride Month dan bendera transgender", {"lgbt_content"}),
        ("Kampanye pemilu: coblos paslon nomor dua", {"political_campaign"}),
        ("Demo mahasiswa dan aksi unjuk rasa di depan gedung", {"demonstration"}),
        ("HAPUS KKN! Kami selalu ingat Nawacita", {"demonstration"}),
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
@pytest.mark.parametrize(
    "text",
    [
        "Habis asap terbitlah sawit",
        "Katanya subsidi untuk rakyat, ternyata pajak yang naik",
        "Janji jalan mulus, realita infrastruktur rusak",
        "Dulu hutan, sekarang tambang",
        "Kalau anggaran pendidikan besar, kenapa sekolah masih rusak?",
        "Indonesia Emas atau Indonesia Cemas #INDONESIACEMAS",
        "#INDONESIACEMAS bersama bendera merah putih",
    ],
)
def test_general_public_policy_satire_structures(text: str):
    findings = content_policy.findings_from_text(
        text,
        backend="test-ocr",
        image_context=True,
    )

    assert "political_meme" in {item["category"] for item in findings}


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "Habis makan terbitlah rasa kenyang",
        "Laporan kebijakan pajak diterbitkan hari ini",
        "Sebelum dan sesudah menggunakan produk perawatan kulit",
    ],
)
def test_non_policy_or_factual_contrast_is_not_political_satire(text: str):
    findings = content_policy.findings_from_text(
        text,
        backend="test-ocr",
        image_context=True,
    )

    assert "political_meme" not in {item["category"] for item in findings}


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
            _visual_bank_logits(
                [3.0, 0.0, 0.0, 2.0, 0.0, 2.0, 3.0, 0.0, 0.0, 3.0]
            ),
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


@pytest.mark.unit
def test_visual_candidate_requires_independent_confirmation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "content_visual_require_confirmation", True)
    candidate = {
        "category": "political_campaign",
        "label": "Kampanye politik",
        "confidence": 0.94,
        "layer_origin": "L3",
        "evidence": "[visual-candidate:clip] application screenshot",
    }

    assert content_policy.confirm_visual_candidates([candidate], []) == []
    assert content_policy.confirm_visual_candidates(
        [candidate],
        [],
        reasoning_verdict="safe",
    ) == []

    qwen = {
        "category": "political_campaign",
        "label": "Kampanye politik",
        "confidence": 0.88,
        "layer_origin": "L3",
        "evidence": "[qwen] nomor paslon dan ajakan memilih",
    }
    assert content_policy.confirm_visual_candidates(
        [candidate],
        [qwen],
        reasoning_verdict="flagged",
    ) == [candidate]


@pytest.mark.unit
def test_explicit_flag_fast_path_survives_qwen_safe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config.settings, "content_visual_require_confirmation", True)
    explicit = {
        "category": "lgbt_content",
        "label": "LGBT text/flag",
        "confidence": 0.94,
        "layer_origin": "L3",
        "visual_confirmation": "explicit_flag",
        "evidence": "[visual-candidate:clip] pair=.99 share=.80 stripe=.95",
    }
    ambiguous = {
        "category": "political_campaign",
        "label": "Kampanye politik",
        "confidence": 0.90,
        "layer_origin": "L3",
        "visual_confirmation": "ambiguous",
        "evidence": "[visual-candidate:clip] poster",
    }

    assert content_policy.confirm_visual_candidates(
        [explicit, ambiguous],
        [],
        reasoning_verdict="safe",
    ) == [explicit]
    assert content_policy.visual_candidates_requiring_reasoning(
        [explicit, ambiguous]
    ) == [ambiguous]


@pytest.mark.unit
def test_manipulated_political_meme_uses_fast_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(config.settings, "content_detection_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_fast_path_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.7)
    monkeypatch.setattr(config.settings, "content_visual_min_share", 0.12)
    monkeypatch.setattr(config.settings, "content_visual_strong_threshold", 0.82)
    monkeypatch.setattr(
        config.settings,
        "content_visual_fast_manipulated_meme_threshold",
        0.98,
    )

    def fake_score(_path, _prompts):
        values = _visual_bank_logits(
            [0.0, 3.0, 8.0, 0.0, 0.0, 3.0, 0.0, 3.0, 0.0, 3.0]
        )
        positives, negatives = content_visual._MANIPULATED_POLITICAL_MEME_PROMPTS
        satire_positives, satire_negatives = (
            content_visual._EXPLICIT_POLITICAL_SATIRE_PROMPTS
        )
        end = len(values) - len(satire_positives) - len(satire_negatives)
        start = end - len(positives) - len(negatives)
        values[start:end] = (
            [8.0] * len(positives) + [0.0] * len(negatives)
        )
        return values, "fake-visual"

    monkeypatch.setattr(content_visual, "_score", fake_score)
    image = tmp_path / "edited-politician.jpg"
    image.write_bytes(b"mock")

    findings = content_visual.analyze_image(image)

    meme = next(item for item in findings if item["category"] == "political_meme")
    assert meme["visual_confirmation"] == "explicit_manipulated_political_meme"
    assert content_policy.confirm_visual_candidates(
        [meme],
        [],
        reasoning_verdict="safe",
    ) == [meme]


@pytest.mark.unit
def test_explicit_policy_satire_uses_fast_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(config.settings, "content_detection_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_fast_path_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.7)
    monkeypatch.setattr(config.settings, "content_visual_min_share", 0.12)
    monkeypatch.setattr(config.settings, "content_visual_strong_threshold", 0.82)
    monkeypatch.setattr(
        config.settings,
        "content_visual_fast_satire_meme_threshold",
        0.995,
    )

    def fake_score(_path, _prompts):
        values = _visual_bank_logits(
            [0.0, 3.0, 8.0, 0.0, 0.0, 3.0, 0.0, 3.0, 0.0, 3.0]
        )
        positives, negatives = content_visual._EXPLICIT_POLITICAL_SATIRE_PROMPTS
        values[-(len(positives) + len(negatives)) :] = (
            [8.0] * len(positives) + [0.0] * len(negatives)
        )
        return values, "fake-visual"

    monkeypatch.setattr(content_visual, "_score", fake_score)
    image = tmp_path / "environmental-policy-satire.jpg"
    image.write_bytes(b"mock")

    findings = content_visual.analyze_image(image)

    meme = next(item for item in findings if item["category"] == "political_meme")
    assert meme["visual_confirmation"] == "explicit_political_satire"
    assert content_policy.confirm_visual_candidates([meme], []) == [meme]


@pytest.mark.unit
def test_strong_striped_pride_flag_uses_fast_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    monkeypatch.setattr(config.settings, "content_detection_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_fast_path_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.7)
    monkeypatch.setattr(config.settings, "content_visual_min_share", 0.12)
    monkeypatch.setattr(config.settings, "content_visual_strong_threshold", 0.97)
    monkeypatch.setattr(config.settings, "content_visual_strong_min_share", 0.25)
    monkeypatch.setattr(config.settings, "content_visual_flag_stripe_threshold", 0.55)
    monkeypatch.setattr(
        content_visual,
        "_score",
        lambda _path, _prompts: (
            _visual_bank_logits(
                [8.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            ),
            "fake-visual",
        ),
    )
    image = tmp_path / "pride-flag.png"
    canvas = Image.new("RGB", (600, 360), "white")
    draw = ImageDraw.Draw(canvas)
    colors = ["#e40303", "#ff8c00", "#ffed00", "#008026", "#004dff", "#750787"]
    for index, color in enumerate(colors):
        draw.rectangle((0, index * 60, 600, (index + 1) * 60), fill=color)
    canvas.save(image)

    findings = content_visual.analyze_image(image)

    pride = next(item for item in findings if item["category"] == "lgbt_content")
    assert pride["visual_confirmation"] == "explicit_flag"
    assert "stripe=" in pride["evidence"]


@pytest.mark.unit
def test_explicit_safe_text_adjudication_drops_contextual_taxonomy_only():
    contextual = {
        "category": "anti_pemerintah",
        "label": "Indikasi: makar",
        "confidence": 0.85,
        "layer_origin": "L2",
        "evidence": "Berita membahas persidangan perkara makar tahun lalu",
    }
    legacy_drug = {
        "category": "narkoba",
        "label": "Indikasi: narkoba",
        "confidence": 0.8,
        "layer_origin": "L1",
        "evidence": "narkoba",
    }

    assert content_policy.apply_text_adjudication(
        [contextual, legacy_drug],
        reasoning_verdict="safe",
    ) == [legacy_drug]


@pytest.mark.unit
def test_visual_share_gate_rejects_pair_only_false_positive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(config.settings, "content_detection_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.7)
    monkeypatch.setattr(config.settings, "content_visual_min_share", 0.12)
    # Campaign wins its local pair, but many unrelated prompts score much
    # higher. It must not be promoted into the candidate set.
    monkeypatch.setattr(
        content_visual,
        "_score",
        lambda _path, _prompts: (
            _visual_bank_logits(
                [8.0, 0.0, 8.0, 0.0, 2.0, 0.0, 8.0, 0.0, 8.0, 0.0]
            ),
            "fake-visual",
        ),
    )
    image = tmp_path / "ordinary-screen.png"
    image.write_bytes(b"mock")

    findings = content_visual.analyze_image(image)

    assert "political_campaign" not in {
        finding["category"] for finding in findings
    }


@pytest.mark.unit
def test_gray_application_ui_cannot_be_a_pride_flag_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    pytest.importorskip("PIL")
    from PIL import Image

    monkeypatch.setattr(config.settings, "content_detection_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_enabled", True)
    monkeypatch.setattr(config.settings, "content_visual_threshold", 0.7)
    monkeypatch.setattr(config.settings, "content_visual_min_share", 0.12)
    monkeypatch.setattr(
        content_visual,
        "_score",
        lambda _path, _prompts: (
            _visual_bank_logits(
                [8.0, 0.0, 0.0, 8.0, 0.0, 8.0, 0.0, 8.0, 0.0, 8.0]
            ),
            "fake-visual",
        ),
    )
    image = tmp_path / "gray-settings-screen.png"
    Image.new("RGB", (640, 960), (235, 238, 242)).save(image)

    findings = content_visual.analyze_image(image)

    assert "lgbt_content" not in {
        finding["category"] for finding in findings
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "Berita persidangan tersangka yang mengajak massa membakar gedung.",
        "Kami mengecam dan menolak ajakan untuk membunuh kelompok tersebut.",
        "Penelitian sejarah membahas propaganda ISIS dan proses rekrutmen lama.",
        "Presiden disebut diktator dalam kutipan laporan persidangan.",
        "Pemilu akan berlangsung bulan depan tanpa ajakan memilih calon tertentu.",
    ],
)
def test_neutral_reporting_negation_and_generic_election_are_not_flagged(text: str):
    findings = content_policy.findings_from_text(
        text,
        backend="test-ocr",
        image_context=False,
    )

    assert findings == []


@pytest.mark.unit
def test_direct_calls_and_extremist_support_remain_detectable():
    findings = content_policy.findings_from_text(
        "Ayo segera serbu gedung itu. Sebarkan propaganda dan bergabung dengan ISIS.",
        backend="test-ocr",
        image_context=True,
    )

    assert {item["category"] for item in findings} >= {"incitement", "extremism"}


@pytest.mark.unit
def test_unrelated_group_and_insult_are_not_combined_across_long_document():
    text = "Komunitas muslim mengadakan bakti sosial. " + ("informasi umum " * 30) + "pejabat itu bodoh"

    findings = content_policy.findings_from_text(text, backend="test-ocr")

    assert "hate_speech" not in {item["category"] for item in findings}
