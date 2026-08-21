from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.acquisition.agent_client import SelectionMutationV1
from app.core.db import db, utcnow
from app.models.schemas import AcquisitionMode
from app.selection.contracts import (
    ModelSignalV1,
    SelectionCandidateV1,
    SelectionRunV1,
    SelectionTotalsV1,
)
from app.selection.policy import build_selection_policy, verify_policy_fingerprint
from app.selection.repository import selection_repository
from app.selection.service import selection_review_service
from app.services.lexicon import contains_phrase, keyword_match_terms

TIMESTAMP = "2026-07-17T03:00:00Z"
SESSION_ID = "session-selection-001"
CRAWL_ID = "crawl-selection-001"


@pytest.mark.unit
def test_policy_fingerprint_terms_and_word_boundaries_are_deterministic() -> None:
    first = build_selection_policy(AcquisitionMode.QUICK)
    second = build_selection_policy(AcquisitionMode.QUICK)

    assert first == second
    assert verify_policy_fingerprint(first)
    assert first.threshold_basis_points == 5_500
    assert first.maximum_candidates == 100_000
    assert first.policy_version == "siksik-selection-v5"
    assert build_selection_policy(AcquisitionMode.FULL).maximum_candidates == 1_000_000
    assert keyword_match_terms("anti pemerintah")[0] == "anti pemerintah"
    assert contains_phrase("seruan ANTI-Pemerintah!", "anti pemerintah")
    assert not contains_phrase("noscobom", "bom")


@pytest.mark.unit
async def test_candidate_api_revision_confirmation_and_owner_boundary(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await db.fetchone("SELECT id FROM users WHERE username = 'admin'")
    assert admin is not None
    await _insert_session(admin["id"])
    run = selection_run()
    candidates = [
        candidate("record-selection-001", 0.70, True),
        candidate("record-selection-002", 0.54, False),
    ]
    await selection_repository.begin_snapshot(run)
    await selection_repository.append_candidates(SESSION_ID, CRAWL_ID, candidates)
    await selection_repository.finish_snapshot(run)

    listed = await client.get(
        f"/api/v1/sessions/{SESSION_ID}/candidates",
        params={"page_size": 1, "selected": "true"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["record_id"] == "record-selection-001"

    operator_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "Ops@2026"},
    )
    operator_token = operator_login.json()["token"]
    forbidden = await client.get(
        f"/api/v1/sessions/{SESSION_ID}/candidates",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert forbidden.status_code == 403

    fake_agent = FakeSelectionAgent(run, candidates[1], admin["id"])

    async def fake_client(_session_id: str):
        return fake_agent

    monkeypatch.setattr(selection_review_service, "_client", fake_client)
    changed = await client.patch(
        f"/api/v1/sessions/{SESSION_ID}/candidates/record-selection-002",
        json={"expected_revision": 1, "override": "include"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["revision"] == 2
    assert changed.json()["candidate"]["selected"] is True
    assert changed.json()["candidate"]["human_override"] == "include"

    stale = await client.patch(
        f"/api/v1/sessions/{SESSION_ID}/candidates/record-selection-001",
        json={"expected_revision": 1, "override": "exclude"},
    )
    assert stale.status_code == 409

    confirmed = await client.post(
        f"/api/v1/sessions/{SESSION_ID}/candidates/confirm",
        json={"expected_revision": 2},
    )
    repeated = await client.post(
        f"/api/v1/sessions/{SESSION_ID}/candidates/confirm",
        json={"expected_revision": 2},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert repeated.status_code == 200, repeated.text
    assert confirmed.json() == repeated.json()
    assert fake_agent.confirm_calls == 1

    immutable = await client.patch(
        f"/api/v1/sessions/{SESSION_ID}/candidates/record-selection-001",
        json={"expected_revision": 2, "override": "exclude"},
    )
    assert immutable.status_code == 409


async def _insert_session(operator_id: str) -> None:
    now = utcnow()
    await db.execute(
        """
        INSERT INTO sessions (
            id, device_id, device_type, label, mode, scenario, status,
            progress_json, timing_json, recommendation, error, created_by,
            review_candidates, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SESSION_ID,
            "device-selection",
            "android",
            "Selection fixture",
            "quick",
            "lulus",
            "awaiting_review",
            json.dumps({"phase": "awaiting_review", "percent": 49, "message": "review"}),
            json.dumps({}),
            None,
            None,
            operator_id,
            1,
            now,
            now,
        ),
    )


def selection_run() -> SelectionRunV1:
    return SelectionRunV1(
        schema_version=1,
        crawl_id=CRAWL_ID,
        siksik_session_id=SESSION_ID,
        state="awaiting_review",
        policy_version="siksik-selection-v1",
        policy_fingerprint="a" * 64,
        revision=1,
        selection_fingerprint="b" * 64,
        review_candidates=True,
        totals=SelectionTotalsV1(
            total=2,
            evaluated=2,
            candidates=1,
            auto_selected=1,
            selected=1,
            below_threshold=1,
            selected_bytes=100,
        ),
        started_at=TIMESTAMP,
        updated_at=TIMESTAMP,
        frozen_at=TIMESTAMP,
        confirmed_at=None,
        failure_reason=None,
    )


def candidate(record_id: str, score: float, selected: bool) -> SelectionCandidateV1:
    return SelectionCandidateV1(
        record_id=record_id,
        source_kind="visible_ui" if selected else "notification",
        source_app="com.instagram.android",
        evidence_text="bukti terbatas",
        score=score,
        threshold=0.55,
        auto_selected=selected,
        selected=selected,
        matched_keywords=["fixture"] if selected else [],
        matched_rules=["keyword:fixture"] if selected else [],
        model_signals=[
            ModelSignalV1(signal="object:person", value="0.900", weight_basis_points=180)
        ]
        if selected
        else [],
        reasons=["threshold_met" if selected else "threshold_not_met"],
        human_override="none",
        operator_id=None,
        revision=1,
        decided_at=TIMESTAMP,
        duplicate_group_id=None,
        representative_record_id=None,
        size_bytes=100,
        thumbnail_available=False,
    )


class FakeSelectionAgent:
    def __init__(
        self,
        run: SelectionRunV1,
        below: SelectionCandidateV1,
        operator_id: str,
    ) -> None:
        self.run = run
        self.below = below
        self.operator_id = operator_id
        self.confirm_calls = 0

    async def mutate_selection_candidate(self, *_args, **kwargs):
        if kwargs["expected_revision"] != self.run.revision:
            from app.acquisition.errors import ErrorCategory, acquisition_error

            raise acquisition_error(ErrorCategory.CONFLICT, "Revision selection telah berubah.")
        self.run = self.run.model_copy(
            update={
                "revision": 2,
                "selection_fingerprint": "c" * 64,
                "totals": self.run.totals.model_copy(
                    update={"selected": 2, "selected_bytes": 200}
                ),
            }
        )
        updated = self.below.model_copy(
            update={
                "selected": True,
                "human_override": "include",
                "operator_id": self.operator_id,
                "revision": 2,
            }
        )
        return SimpleNamespace(
            body=SelectionMutationV1(
                schema_version=1,
                run=self.run,
                candidate=updated,
            )
        )

    async def confirm_selection(self, *_args, **kwargs):
        assert kwargs["expected_revision"] == 2
        self.confirm_calls += 1
        self.run = self.run.model_copy(
            update={"state": "confirmed", "confirmed_at": TIMESTAMP}
        )
        return SimpleNamespace(body=self.run)
