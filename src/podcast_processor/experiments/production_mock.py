"""Offline production-like AdClassifier path (no LLM, no Flask).

Mirrors the always-paid full-transcript walk used for token cost, then
labels ads with an oracle over the labeled fixture (quality ceiling of a
perfect LLM that sees every chunk) plus CueDetector neighbor expansion
matching AdClassifier.expand_neighbors_bulk (production detector, no scout
extras).
"""

from __future__ import annotations

from dataclasses import dataclass

from podcast_processor.cue_detector import CueDetector
from podcast_processor.experiments.bow_scout import overlap_seconds
from podcast_processor.experiments.cost_model import estimate_adclassifier_tokens
from podcast_processor.experiments.metrics import Span, merge_spans
from podcast_processor.experiments.types import (
    EpisodeFixture,
    LabeledAd,
    ScoutSegment,
    TokenEstimate,
)

NEIGHBOR_WINDOW = 5
NEIGHBOR_GAP_SECONDS = 10.0
MIN_LABEL_OVERLAP_SECONDS = 0.25


@dataclass(frozen=True)
class ProductionLikeResult:
    predicted: list[Span]
    tokens: TokenEstimate
    n_seed_segments: int
    n_expanded_segments: int
    neighbor_window: int


def _should_expand_neighbor(
    *,
    has_strong_cue: bool,
    is_transition: bool,
    gap_seconds: float,
    enable_boundary_refinement: bool,
) -> bool:
    """Match AdClassifier._should_expand_neighbor."""
    if not enable_boundary_refinement:
        return has_strong_cue
    if has_strong_cue or is_transition:
        return True
    return gap_seconds <= NEIGHBOR_GAP_SECONDS


def labeled_seed_indices(
    segments: list[ScoutSegment], ads: list[LabeledAd]
) -> list[int]:
    seeds: list[int] = []
    for idx, segment in enumerate(segments):
        if any(
            overlap_seconds(segment.start_time, segment.end_time, ad.start, ad.end)
            > MIN_LABEL_OVERLAP_SECONDS
            for ad in ads
        ):
            seeds.append(idx)
    return seeds


def expand_neighbor_indices(
    segments: list[ScoutSegment],
    seed_indices: list[int],
    *,
    window: int = NEIGHBOR_WINDOW,
    enable_boundary_refinement: bool = True,
    detector: CueDetector | None = None,
) -> list[int]:
    cue = detector or CueDetector()  # production defaults, extras off
    flagged = set(seed_indices)
    n = len(segments)
    for seed in seed_indices:
        seed_seg = segments[seed]
        for offset in range(-window, window + 1):
            if offset == 0:
                continue
            neighbor_idx = seed + offset
            if neighbor_idx < 0 or neighbor_idx >= n:
                continue
            neighbor = segments[neighbor_idx]
            signals = cue.analyze(neighbor.text)
            has_strong_cue = bool(
                signals.get("url")
                or signals.get("promo")
                or signals.get("phone")
                or signals.get("cta")
            )
            is_transition = bool(signals.get("transition"))
            gap_seconds = abs(float(neighbor.start_time) - float(seed_seg.start_time))
            if _should_expand_neighbor(
                has_strong_cue=has_strong_cue,
                is_transition=is_transition,
                gap_seconds=gap_seconds,
                enable_boundary_refinement=enable_boundary_refinement,
            ):
                flagged.add(neighbor_idx)
    return sorted(flagged)


def indices_to_spans(segments: list[ScoutSegment], indices: list[int]) -> list[Span]:
    if not indices or not segments:
        return []
    flagged = set(indices)
    spans: list[Span] = []
    current_start: float | None = None
    current_end: float | None = None
    for idx, segment in enumerate(segments):
        if idx in flagged:
            if current_start is None:
                current_start = float(segment.start_time)
            current_end = float(segment.end_time)
        elif current_start is not None and current_end is not None:
            spans.append((current_start, current_end))
            current_start = None
            current_end = None
    if current_start is not None and current_end is not None:
        spans.append((current_start, current_end))
    return merge_spans(spans)


def classify_production_like(
    episode: EpisodeFixture,
    *,
    neighbor_window: int = NEIGHBOR_WINDOW,
    enable_boundary_refinement: bool = True,
) -> ProductionLikeResult:
    """Oracle LLM labels (fixture ads) + production CueDetector neighbor expand."""
    seeds = labeled_seed_indices(episode.segments, episode.labeled_ads)
    expanded = expand_neighbor_indices(
        episode.segments,
        seeds,
        window=neighbor_window,
        enable_boundary_refinement=enable_boundary_refinement,
    )
    predicted = indices_to_spans(episode.segments, expanded)
    tokens = estimate_adclassifier_tokens(
        episode.segments, episode.podcast_title, episode.podcast_topic
    )
    return ProductionLikeResult(
        predicted=predicted,
        tokens=tokens,
        n_seed_segments=len(seeds),
        n_expanded_segments=len(expanded),
        neighbor_window=neighbor_window,
    )
