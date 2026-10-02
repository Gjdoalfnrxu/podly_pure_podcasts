"""High-recall candidate chunks. Independent of production AdClassifier.

Sources (union, then pad ±1–2s and merge):

1. Always 0–90s preroll (clamped to duration when known).
2. Publisher markers as **positives only** (chapter titles / PSC chapters).
   Missing markers never imply ad-free.
3. Optional fingerprint near-dupe hits (repeated preroll-like audio).
4. Optional DSP silence-gap hits (ffmpeg silencedetect).
5. Optional DAI midroll probes when the enclosure host looks like DAI
   and the episode is long enough.
"""

from __future__ import annotations

from podcast_processor.experiments.auto_gold.constants import (
    DAI_PROBE_FRACTIONS,
    DAI_PROBE_HALF_WINDOW_SECONDS,
    DAI_PROBE_MIN_DURATION_SECONDS,
    DEFAULT_MERGE_GAP_SECONDS,
    DEFAULT_PAD_SECONDS,
    PREROLL_END_SECONDS,
    PREROLL_START_SECONDS,
    PUBLISHER_MARKER_FILTERS,
)
from podcast_processor.experiments.auto_gold.types import (
    CandidateChunk,
    EpisodeRef,
    PublisherMarker,
)


def clamp_span(
    start: float,
    end: float,
    duration: float | None,
) -> tuple[float, float] | None:
    lo = max(0.0, float(start))
    hi = float(end)
    if duration is not None:
        hi = min(hi, float(duration))
    if hi - lo < 0.5:
        return None
    return lo, hi


def always_preroll(duration: float | None) -> CandidateChunk:
    end = PREROLL_END_SECONDS
    if duration is not None:
        end = min(end, float(duration))
    return CandidateChunk(
        start=PREROLL_START_SECONDS,
        end=max(end, PREROLL_START_SECONDS + 0.5),
        sources=["preroll_always"],
        notes="High-recall: first 0–90s of every episode",
    )


def marker_is_publisher_positive(title: str) -> bool:
    lowered = title.strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in PUBLISHER_MARKER_FILTERS)


def candidates_from_publisher_markers(
    markers: list[PublisherMarker],
    duration: float | None,
) -> list[CandidateChunk]:
    """Positives only. Unmarked chapters are not treated as non-ads."""
    chunks: list[CandidateChunk] = []
    for marker in markers:
        if not marker_is_publisher_positive(marker.title):
            continue
        end = marker.end
        if end is None:
            end = marker.start + 60.0
        span = clamp_span(marker.start, end, duration)
        if span is None:
            continue
        chunks.append(
            CandidateChunk(
                start=span[0],
                end=span[1],
                sources=["publisher_marker"],
                notes=f"publisher marker: {marker.title!r} ({marker.source})",
                publisher_positive=True,
            )
        )
    return chunks


def dai_probe_candidates(
    duration: float | None,
    dai_likely: bool,
) -> list[CandidateChunk]:
    if not dai_likely or duration is None:
        return []
    if float(duration) + 1e-12 < DAI_PROBE_MIN_DURATION_SECONDS:
        return []
    chunks: list[CandidateChunk] = []
    half = DAI_PROBE_HALF_WINDOW_SECONDS
    for fraction in DAI_PROBE_FRACTIONS:
        center = float(duration) * fraction
        span = clamp_span(center - half, center + half, duration)
        if span is None:
            continue
        chunks.append(
            CandidateChunk(
                start=span[0],
                end=span[1],
                sources=["dai_probe"],
                notes=f"DAI-host midroll probe at {fraction:.0%} duration",
            )
        )
    return chunks


def merge_candidates(
    chunks: list[CandidateChunk],
    *,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    duration: float | None = None,
) -> list[CandidateChunk]:
    """Pad ±1–2s and merge overlaps / tiny gaps."""
    padded: list[CandidateChunk] = []
    for chunk in chunks:
        span = clamp_span(
            chunk.start - pad_seconds, chunk.end + pad_seconds, duration
        )
        if span is None:
            continue
        padded.append(
            CandidateChunk(
                start=span[0],
                end=span[1],
                sources=list(chunk.sources),
                notes=chunk.notes,
                publisher_positive=chunk.publisher_positive,
            )
        )
    if not padded:
        return []
    padded.sort(key=lambda c: (c.start, c.end))
    merged: list[CandidateChunk] = [padded[0]]
    for chunk in padded[1:]:
        current = merged[-1]
        if chunk.start <= current.end + merge_gap_seconds:
            sources: list[str] = []
            for name in [*current.sources, *chunk.sources]:
                if name not in sources:
                    sources.append(name)
            notes = current.notes
            if chunk.notes and chunk.notes not in notes:
                notes = f"{notes}; {chunk.notes}" if notes else chunk.notes
            merged[-1] = CandidateChunk(
                start=current.start,
                end=max(current.end, chunk.end),
                sources=sources,
                notes=notes,
                publisher_positive=current.publisher_positive
                or chunk.publisher_positive,
            )
        else:
            merged.append(chunk)
    return merged


def propose_candidates(
    episode: EpisodeRef,
    *,
    extra: list[CandidateChunk] | None = None,
    include_dai_probes: bool = True,
    pad_seconds: float = DEFAULT_PAD_SECONDS,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
) -> list[CandidateChunk]:
    """Union of high-recall sources. Never calls AdClassifier."""
    raw: list[CandidateChunk] = [always_preroll(episode.duration_seconds)]
    raw.extend(
        candidates_from_publisher_markers(
            episode.publisher_markers, episode.duration_seconds
        )
    )
    if include_dai_probes:
        raw.extend(dai_probe_candidates(episode.duration_seconds, episode.dai_likely))
    if extra:
        raw.extend(extra)
    return merge_candidates(
        raw,
        pad_seconds=pad_seconds,
        merge_gap_seconds=merge_gap_seconds,
        duration=episode.duration_seconds,
    )
