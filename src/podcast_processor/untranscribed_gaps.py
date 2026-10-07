"""Extend ad cut windows over untranscribed audio that borders an ad.

Whisper can emit no segments at all for stretches of ad audio (music beds,
jingles, fast read-outs). Such a stretch is invisible to the classifier, so
the cut would otherwise start/stop at the first/last *transcribed* ad
segment and leave the untranscribed ad audio in the output.
"""

import logging
from collections.abc import Iterable, Sequence
from typing import Any

MIN_UNTRANSCRIBED_GAP_SECONDS = 5.0

_EPS = 1e-6


def extend_groups_into_untranscribed_gaps(
    groups: Iterable[Any],
    transcript_spans: Sequence[tuple[float, float]],
    *,
    audio_duration_seconds: float | None,
    min_gap_seconds: float = MIN_UNTRANSCRIBED_GAP_SECONDS,
    logger: logging.Logger | None = None,
) -> None:
    """Widen each ad group's cut window over adjacent untranscribed gaps.

    ``transcript_spans`` must cover every transcript segment of the episode
    (ad and non-ad), so a gap is only ever untranscribed audio and the
    extension can never reach into transcribed speech. A side is skipped when
    its cut edge was already moved inside the group (boundary refinement
    decided the first/last ad segment starts/ends with content). The audio
    end is only treated as a boundary when ``audio_duration_seconds`` is known.
    """
    for group in groups:
        if not group.segments:
            continue
        first_start = min(s.start_time for s in group.segments)
        last_end = max(s.end_time for s in group.segments)

        if group.start_time <= first_start + _EPS:
            prev_end = max(
                (e for s, e in transcript_spans if s < first_start - _EPS),
                default=0.0,
            )
            if first_start - prev_end >= min_gap_seconds:
                if logger:
                    logger.info(
                        "Extending ad cut start %.1f -> %.1f over untranscribed gap",
                        group.start_time,
                        prev_end,
                    )
                group.start_time = prev_end

        if group.end_time >= last_end - _EPS:
            next_start = min(
                (s for s, e in transcript_spans if e > last_end + _EPS),
                default=audio_duration_seconds,
            )
            if next_start is not None and next_start - last_end >= min_gap_seconds:
                if logger:
                    logger.info(
                        "Extending ad cut end %.1f -> %.1f over untranscribed gap",
                        group.end_time,
                        next_start,
                    )
                group.end_time = next_start
