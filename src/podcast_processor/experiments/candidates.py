"""Experiment-only candidate detectors and cheap recovery probes.

Never imported by PodcastProcessor. Production CueDetector regexes stay
exactly as committed; these classes exist so the daily loop can score a
change without folding it.
"""

from __future__ import annotations

import re
from typing import Any

from podcast_processor.cue_detector import CueDetector
from podcast_processor.experiments.types import (
    EpisodeFixture,
    ScoutSegment,
    ScoutWindow,
)

# Production promo is r"\b(code|promo|save|discount)\s+\w+\b" which flags
# tech speech ("code review", "code path"). Candidate requires use-code /
# promo-code phrasing so bare "code <word>" is not a strong cue.
TIGHT_PROMO_PATTERN = re.compile(
    r"\b(?:use\s+code|(?:promo|discount)(?:\s+code)?|save)\s+\w+\b",
    re.I,
)

# Storytelling host-reads with no URL/CTA/phone. Scout-only; not production extras.
STORYTELLING_HOST_READ = re.compile(
    r"\b(?:story from a company|the company is|this chapter of the show is a story)\b",
    re.I,
)
STORYTELLING_WEIGHT = 1.0


class TightPromoCueDetector(CueDetector):
    """Same as CueDetector except promo requires use/promo/discount-code phrasing.

    2026-09-30 live (4 real eps): baseline scout windows 26 → TightPromo 15
    (-42.3%). Soft Skills tech-speech `code <word>` FPs drop; The Daily
    window counts stay the same. Experiment-only — do not copy
    TIGHT_PROMO_PATTERN into production CueDetector.promo_pattern.
    2026-10-02 H010: offline measured/no_win (residual/precision
    unchanged); stay experiment-only.
    """

    def __init__(self, include_scout_extras: bool = False) -> None:
        super().__init__(include_scout_extras=include_scout_extras)
        self.promo_pattern = TIGHT_PROMO_PATTERN


class StorytellingScoutDetector(CueDetector):
    """Production extras plus a cheap storytelling phrase, still not in CueDetector()."""

    def score(
        self, text: str, weights: dict[str, float] | None = None
    ) -> tuple[float, dict[str, bool]]:
        total, signals = super().score(text, weights=weights)
        if STORYTELLING_HOST_READ.search(text):
            signals = dict(signals)
            signals["storytelling_host_read"] = True
            total += STORYTELLING_WEIGHT
        else:
            signals = dict(signals)
            signals["storytelling_host_read"] = False
        return total, signals


def duration_gated_midroll_probe(
    episode: EpisodeFixture,
    windows: list[ScoutWindow],
    config: Any,
    *,
    min_duration_seconds: float = 900.0,
    fraction: float = 0.4,
    half_window_seconds: float = 20.0,
) -> list[ScoutWindow]:
    """Cue-sparse recovery that stays inside the +10% scout-token ε.

    H002's 60s midroll probe also fired on short ad-free episodes (4707
    scout tokens vs 3901, limit 4291). Gate by duration so 10-minute
    `ad_free_interview` is skipped and only longer cue-sparse host-reads
    get a tight window. Smaller than storytelling padding (4338 tokens).
    """
    if windows:
        return windows
    if float(episode.duration_seconds) + 1e-12 < min_duration_seconds:
        return windows
    return cheap_midroll_probe(
        episode,
        windows,
        config,
        fraction=fraction,
        half_window_seconds=half_window_seconds,
    )


def wider_duration_gated_midroll_probe(
    episode: EpisodeFixture,
    windows: list[ScoutWindow],
    config: Any,
    *,
    min_duration_seconds: float = 900.0,
    fraction: float = 0.4,
    half_window_seconds: float = 35.0,
) -> list[ScoutWindow]:
    """H009 folded 2026-10-02: 70s duration-gated window for cue-sparse time recall.

    H007's 40s window (half=20) hits the Away host-read but only covers
    480-500s of the 480-515s label (time recall 0.571). A 70s window
    centered at 40% duration covers the full labeled span while still
    skipping short ad-free interviews. Now eval DEFAULT_WINDOW_POSTPROCESS.
    """
    return duration_gated_midroll_probe(
        episode,
        windows,
        config,
        min_duration_seconds=min_duration_seconds,
        fraction=fraction,
        half_window_seconds=half_window_seconds,
    )


def cheap_midroll_probe(
    episode: EpisodeFixture,
    windows: list[ScoutWindow],
    config: Any,
    *,
    fraction: float = 0.4,
    half_window_seconds: float = 30.0,
) -> list[ScoutWindow]:
    """If scout found nothing, add one mid-episode confirm window.

    Recovers cue-sparse host-reads that sit near 40% duration without a
    full AdClassifier walk. Ad-free episodes still get a window (oracle
    confirm should drop it); token gates decide if that cost is acceptable.
    """
    del config
    if windows:
        return windows
    segments = episode.segments
    if not segments:
        return []
    center = float(episode.duration_seconds) * fraction
    start = center - half_window_seconds
    end = center + half_window_seconds
    indices = [
        idx
        for idx, segment in enumerate(segments)
        if segment.end_time > start and segment.start_time < end
    ]
    if not indices:
        # Fall back to a single segment nearest the center.
        nearest = min(
            range(len(segments)),
            key=lambda idx: abs(
                (segments[idx].start_time + segments[idx].end_time) / 2.0 - center
            ),
        )
        indices = [nearest]
    chosen: list[ScoutSegment] = [segments[i] for i in indices]
    return [
        ScoutWindow(
            start_time=chosen[0].start_time,
            end_time=chosen[-1].end_time,
            start_seq=chosen[0].sequence_num,
            end_seq=chosen[-1].sequence_num,
            segment_indices=list(indices),
            peak_score=0.0,
            cue_types=["cheap_midroll_probe"],
            segments=chosen,
        )
    ]
