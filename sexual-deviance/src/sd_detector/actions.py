from __future__ import annotations

from dataclasses import dataclass

from .schema import Action, Severity


@dataclass
class ActionThresholds:
    block_explicit: float = 0.65
    review_suggestive: float = 0.55
    review_explicit: float = 0.40


def resolve_action(
    severity: Severity,
    confidence: float,
    thresholds: ActionThresholds | None = None,
) -> Action:
    t = thresholds or ActionThresholds()

    if severity == Severity.SAFE:
        return Action.ALLOW

    if severity == Severity.EXPLICIT:
        if confidence >= t.block_explicit:
            return Action.BLOCK
        if confidence >= t.review_explicit:
            return Action.REVIEW
        return Action.REVIEW

    # suggestive
    if confidence >= t.review_suggestive:
        return Action.REVIEW
    return Action.REVIEW
