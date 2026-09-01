from __future__ import annotations

from typing import Optional

from .actions import ActionThresholds, resolve_action
from .indonesian_meme import merge_meme_contexts
from .lgbt import merge_lgbt_contexts
from .schema import (
    Action,
    FrameAnalysis,
    IndonesianMemeContext,
    LgbtContext,
    MediaVerdict,
    NudityLevel,
    Orientation,
    SEVERITY_RANK,
    Severity,
)

NUDITY_RANK = {
    NudityLevel.NONE: 0,
    NudityLevel.PARTIAL: 1,
    NudityLevel.FULL: 2,
}


def merge_frame(
    llm: FrameAnalysis,
    nudenet_severity: Optional[Severity] = None,
    nudenet_nudity: Optional[NudityLevel] = None,
    nudenet_labels: Optional[list] = None,
) -> FrameAnalysis:
    """Gabung hasil LLM + NudeNet — ambil severity/nudity tertinggi."""
    out = llm.model_copy()
    if nudenet_severity and SEVERITY_RANK[nudenet_severity] > SEVERITY_RANK[out.severity]:
        out.severity = nudenet_severity
    if nudenet_nudity and NUDITY_RANK[nudenet_nudity] > NUDITY_RANK[out.nudity]:
        out.nudity = nudenet_nudity
    if nudenet_labels:
        for label in nudenet_labels[:3]:
            tag = label.lower().replace("_exposed", "").replace("_", " ")
            if tag not in out.acts:
                out.acts.append("nudity")
                break
    return out


def aggregate_frames(
    path: str,
    media_type: str,
    frames: list[FrameAnalysis],
    frame_count: int,
    mode: str = "balanced",
    action_thresholds: ActionThresholds | None = None,
    latency_ms: Optional[float] = None,
    cache_hit: bool = False,
) -> MediaVerdict:
    if not frames:
        return MediaVerdict(
            path=path,
            media_type=media_type,  # type: ignore[arg-type]
            mode=mode,
            severity=Severity.SAFE,
            nudity=NudityLevel.NONE,
            orientation=Orientation.NONE,
            lgbt=LgbtContext(),
            indonesian_meme=IndonesianMemeContext(),
            acts=[],
            confidence=1.0,
            action=Action.ALLOW,
            flagged=False,
            frame_count=frame_count,
            frames_analyzed=0,
            reason="No frames analyzed",
            latency_ms=latency_ms,
            cache_hit=cache_hit,
        )

    prescreen_skipped = sum(1 for f in frames if f.skipped_llm)
    worst = max(frames, key=lambda f: (SEVERITY_RANK[f.severity], f.confidence))
    worst_nudity = max(frames, key=lambda f: NUDITY_RANK[f.nudity])

    flagged_frames = [f for f in frames if f.severity != Severity.SAFE]
    orientation = Orientation.NONE
    if flagged_frames:
        orient_frame = max(
            flagged_frames,
            key=lambda f: (SEVERITY_RANK[f.severity], f.confidence),
        )
        orientation = orient_frame.orientation

    acts: set[str] = set()
    for f in frames:
        acts.update(f.acts)

    lgbt = merge_lgbt_contexts(f.lgbt for f in frames)
    indonesian_meme = merge_meme_contexts(f.indonesian_meme for f in frames)

    action = resolve_action(worst.severity, worst.confidence, action_thresholds)
    flagged = action != Action.ALLOW
    reasons = [f.reason for f in frames if f.reason and f.severity == worst.severity]
    reason = reasons[0] if reasons else worst.reason

    return MediaVerdict(
        path=path,
        media_type=media_type,  # type: ignore[arg-type]
        mode=mode,
        severity=worst.severity,
        nudity=worst_nudity.nudity,
        orientation=orientation,
        lgbt=lgbt,
        indonesian_meme=indonesian_meme,
        acts=sorted(acts),
        confidence=worst.confidence,
        action=action,
        flagged=flagged,
        frame_count=frame_count,
        frames_analyzed=len(frames),
        prescreen_skipped=prescreen_skipped,
        reason=reason,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        frames=frames,
    )
