from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Severity(str, Enum):
    SAFE = "safe"
    SUGGESTIVE = "suggestive"
    EXPLICIT = "explicit"


SEVERITY_RANK = {
    Severity.SAFE: 0,
    Severity.SUGGESTIVE: 1,
    Severity.EXPLICIT: 2,
}


class Orientation(str, Enum):
    NONE = "none"
    HETEROSEXUAL = "heterosexual"
    GAY = "gay"
    LESBIAN = "lesbian"
    BISEXUAL = "bisexual"
    OTHER = "other"


class NudityLevel(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class Action(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class LgbtContext(BaseModel):
    """Konteks visual LGBT: bendera, pakaian, scene — dari pixel + deskripsi VLM."""

    present: bool = False
    flag_colors: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    clothing: list[str] = Field(default_factory=list)
    scene: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    orientation_hint: str = "none"

    @field_validator("flag_colors", "symbols", "clothing", "scene", "signals", mode="before")
    @classmethod
    def normalize_list(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v.lower().strip()] if v.strip() else []
        return [str(x).lower().strip() for x in v if str(x).strip()]


class IndonesianMemeContext(BaseModel):
    """Meme politik/sindiran Indonesia: figur publik + teks overlay."""

    present: bool = False
    is_meme: bool = False
    has_text_overlay: bool = False
    text_language: str = "unknown"
    overlay_text: list[str] = Field(default_factory=list)
    public_figures: list[str] = Field(default_factory=list)
    satire_type: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)

    @field_validator(
        "overlay_text", "public_figures", "satire_type", "topics", "signals",
        mode="before",
    )
    @classmethod
    def normalize_meme_list(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        return [str(x).strip() for x in v if str(x).strip()]


class FrameAnalysis(BaseModel):
    severity: Severity = Severity.SAFE
    nudity: NudityLevel = NudityLevel.NONE
    orientation: Orientation = Orientation.NONE
    lgbt: LgbtContext = Field(default_factory=LgbtContext)
    indonesian_meme: IndonesianMemeContext = Field(default_factory=IndonesianMemeContext)
    acts: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = ""
    prescreen_score: Optional[float] = None
    skipped_llm: bool = False

    @field_validator("acts", mode="before")
    @classmethod
    def normalize_acts(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v.lower().strip()] if v.strip() else []
        return [str(x).lower().strip() for x in v if str(x).strip()]


class MediaVerdict(BaseModel):
    path: str
    media_type: Literal["image", "video"]
    mode: str = "balanced"
    severity: Severity
    nudity: NudityLevel
    orientation: Orientation
    lgbt: LgbtContext = Field(default_factory=LgbtContext)
    indonesian_meme: IndonesianMemeContext = Field(default_factory=IndonesianMemeContext)
    acts: list[str]
    confidence: float
    action: Action = Action.ALLOW
    flagged: bool = False
    frame_count: int = 1
    frames_analyzed: int = 1
    prescreen_skipped: int = 0
    reason: str = ""
    latency_ms: Optional[float] = None
    cache_hit: bool = False
    frames: list[FrameAnalysis] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
