from __future__ import annotations

import hashlib
import json

from app.core.config import settings
from app.models.schemas import AcquisitionMode
from app.selection.contracts import KeywordPolicyV1, SelectionPolicyV1
from app.services.lexicon import category_for_keyword, keyword_match_terms, normalize_text

POLICY_VERSION = "siksik-selection-v5"


def _keyword_corpus() -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in (
        *settings.risk_keywords,
        *settings.video_risk_keywords,
        *settings.meme_hate_keywords,
    ):
        normalized = normalize_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return sorted(output)


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_selection_policy(mode: AcquisitionMode) -> SelectionPolicyV1:
    keywords = [
        KeywordPolicyV1(
            keyword=keyword,
            category=category_for_keyword(keyword),
            match_terms=keyword_match_terms(keyword),
            weight_basis_points=4_000,
        )
        for keyword in _keyword_corpus()
    ]
    values: dict[str, object] = {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "keywords": [value.model_dump(mode="json") for value in keywords],
        "source_weights_basis_points": {
            "media_image": 300,
            "media_video": 300,
            "media_audio": 100,
            "document": 400,
            "sms": 600,
            "contact": 0,
            "visible_ui": 700,
            "notification": 500,
        },
        "text_signal_weights_basis_points": {
            "ocr": 1_000,
            "document_text": 1_100,
            "sms": 900,
            "visible_ui": 1_000,
            "notification": 1_000,
        },
        "face_weight_basis_points": 400,
        "object_label_weights_basis_points": {
            "knife": 1_500,
            "scissors": 600,
            "person": 200,
        },
        "required_social_scopes": [
            "own_profile",
            "own_posts",
            "own_tweets",
            "own_story_archive",
            "own_comments",
            "own_replies",
        ],
        "duplicate_representative_policy": "representative_only",
        "threshold_basis_points": 5_500,
        "maximum_candidates": 100_000 if mode == AcquisitionMode.QUICK else 1_000_000,
        "maximum_bytes": (
            1024 * 1024 * 1024 if mode == AcquisitionMode.QUICK else 8 * 1024 * 1024 * 1024
        ),
    }
    return SelectionPolicyV1.model_validate(
        {**values, "policy_fingerprint": _fingerprint(values)}
    )


def verify_policy_fingerprint(policy: SelectionPolicyV1) -> bool:
    payload = policy.model_dump(mode="json", exclude={"policy_fingerprint"})
    return _fingerprint(payload) == policy.policy_fingerprint
