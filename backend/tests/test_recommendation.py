"""Tiga status rekomendasi: LULUS, MENUNGGU REVIEW, TIDAK LULUS."""

from __future__ import annotations

import pytest

from app.services.recommendation import (
    REC_LULUS,
    REC_MENUNGGU_REVIEW,
    REC_TIDAK_LULUS,
    recommendation_from_confirmed,
    recommendation_from_counts,
)


@pytest.mark.unit
def test_recommendation_three_states():
    assert recommendation_from_counts(confirmed=0, pending=0) == REC_LULUS
    assert recommendation_from_counts(confirmed=0, pending=3) == REC_MENUNGGU_REVIEW
    assert recommendation_from_counts(confirmed=1, pending=0) == REC_TIDAK_LULUS
    assert recommendation_from_counts(confirmed=2, pending=5) == REC_TIDAK_LULUS


@pytest.mark.unit
def test_recommendation_from_confirmed_compat():
    assert recommendation_from_confirmed(0) == REC_LULUS
    assert recommendation_from_confirmed(0, pending=1) == REC_MENUNGGU_REVIEW
    assert recommendation_from_confirmed(1) == REC_TIDAK_LULUS
