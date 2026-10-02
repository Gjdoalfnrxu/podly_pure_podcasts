"""Dataclasses for the auto-gold pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ShowSpec:
    show_id: str
    title: str
    publisher: str
    genre: str
    ad_mechanism: str
    rss_url: str
    notes: str = ""


@dataclass
class PublisherMarker:
    start: float
    end: float | None
    title: str
    source: str


@dataclass
class EpisodeRef:
    show: ShowSpec
    episode_title: str
    audio_url: str | None
    duration_seconds: float | None
    guid: str
    published: str | None
    dai_likely: bool
    publisher_markers: list[PublisherMarker] = field(default_factory=list)
    rss_ok: bool = True
    error: str | None = None
    raw_rss_path: Path | None = None


@dataclass
class CandidateChunk:
    start: float
    end: float
    sources: list[str]
    notes: str = ""
    publisher_positive: bool = False

    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration(), 3),
            "sources": list(self.sources),
            "notes": self.notes,
            "publisher_positive": self.publisher_positive,
        }


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass
class ChunkTranscript:
    chunk: CandidateChunk
    audio_path: Path | None
    skipped: bool
    skip_reason: str | None
    backend: str
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.as_dict(),
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "backend": self.backend,
            "text": self.text,
            "segments": [seg.as_dict() for seg in self.segments],
        }


@dataclass
class JudgeLabel:
    is_ad: bool
    ad_spans: list[dict[str, Any]]
    content_type: str
    confidence: float
    skipped: bool
    skip_reason: str | None
    model: str
    raw_response: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_ad": self.is_ad,
            "ad_spans": self.ad_spans,
            "content_type": self.content_type,
            "confidence": self.confidence,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "model": self.model,
            "raw_response": self.raw_response,
        }


@dataclass
class ShowResult:
    show: ShowSpec
    episode: EpisodeRef | None
    audio_path: Path | None
    candidates: list[CandidateChunk]
    transcripts: list[ChunkTranscript]
    labels: list[JudgeLabel]
    blocked: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "show_id": self.show.show_id,
            "title": self.show.title,
            "genre": self.show.genre,
            "ad_mechanism": self.show.ad_mechanism,
            "rss_url": self.show.rss_url,
            "episode_title": self.episode.episode_title if self.episode else None,
            "audio_url": self.episode.audio_url if self.episode else None,
            "duration_seconds": self.episode.duration_seconds if self.episode else None,
            "dai_likely": self.episode.dai_likely if self.episode else False,
            "audio_path": str(self.audio_path) if self.audio_path else None,
            "n_candidates": len(self.candidates),
            "candidates": [c.as_dict() for c in self.candidates],
            "n_transcripts": len(self.transcripts),
            "transcripts": [t.as_dict() for t in self.transcripts],
            "labels": [lab.as_dict() for lab in self.labels],
            "blocked": list(self.blocked),
            "error": self.episode.error if self.episode else "no episode",
        }


@dataclass
class PipelineResult:
    gold_family: str
    sampling_unit: str
    whisper_backend: str
    judge_mode: str
    groq_key_present_but_unused: bool
    gemini_key_present: bool
    production_flag_enable_bow_scout_gemini_confirm: bool
    shows: list[ShowResult]
    blocked_steps: list[str]
    notes: list[str]
    output_dir: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "gold_family": self.gold_family,
            "sampling_unit": self.sampling_unit,
            "whisper_backend": self.whisper_backend,
            "judge_mode": self.judge_mode,
            "groq_key_present_but_unused": self.groq_key_present_but_unused,
            "gemini_key_present": self.gemini_key_present,
            "production_flag_enable_bow_scout_gemini_confirm": (
                self.production_flag_enable_bow_scout_gemini_confirm
            ),
            "n_shows": len(self.shows),
            "genres": sorted({row.show.genre for row in self.shows}),
            "blocked_steps": list(self.blocked_steps),
            "notes": list(self.notes),
            "shows": [row.as_dict() for row in self.shows],
        }
