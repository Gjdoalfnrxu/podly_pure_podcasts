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
    (−42.3%). Soft Skills tech-speech `code <word>` FPs drop; The Daily
    window counts stay the same. Experiment-only — do not copy
    TIGHT_PROMO_PATTERN into production CueDetector until Soft Skills-style
    goldens are promoted and offline confidence metrics move.
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
