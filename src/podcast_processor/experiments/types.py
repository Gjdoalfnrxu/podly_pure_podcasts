from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Seconds = int | float


@dataclass(frozen=True)
class ScoutSegment:
    """TranscriptSegment-like row without a Flask/SQLAlchemy dependency."""

    sequence_num: int
    start_time: Seconds
    end_time: Seconds
    text: str

    def duration(self) -> float:
        return max(0.0, float(self.end_time) - float(self.start_time))


@dataclass(frozen=True)
class LabeledAd:
    start: Seconds
    end: Seconds
    kind: str = "unknown"
    notes: str = ""

    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))


@dataclass
class ScoutWindow:
    start_time: Seconds
    end_time: Seconds
    start_seq: int
    end_seq: int
    segment_indices: list[int]
    peak_score: float
    cue_types: list[str]
    segments: list[ScoutSegment] = field(default_factory=list)

    def duration(self) -> float:
        return max(0.0, float(self.end_time) - float(self.start_time))


@dataclass(frozen=True)
class AdSpan:
    start: Seconds
    end: Seconds
    confidence: float = 1.0


@dataclass
class ConfirmResult:
    is_ad: bool
    ad_spans: list[AdSpan]
    content_type: str | None = None
    confidence: float = 0.0
    cached: bool = False
    model: str = ""
    prompt_hash: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw_response: str = ""


@dataclass
class TokenEstimate:
    calls: int
    input_tokens: int
    output_tokens: int
    usd: float
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class EpisodeFixture:
    fixture_id: str
    title: str
    podcast_title: str
    podcast_topic: str
    duration_seconds: Seconds
    segments: list[ScoutSegment]
    labeled_ads: list[LabeledAd]
    notes: str = ""
