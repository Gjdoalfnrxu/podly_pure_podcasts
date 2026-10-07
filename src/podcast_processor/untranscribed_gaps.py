"""Extend ad cut windows over untranscribed audio that borders an ad.

Whisper can emit no segments at all for stretches of ad audio (music beds,
jingles, fast read-outs). Such a stretch is invisible to the classifier, so
the cut would otherwise start/stop at the first/last *transcribed* ad
segment and leave the untranscribed ad audio in the output.

Two steps, so that the gap length never feeds the separation merge or the
minimum-length filter (both must see only transcribed ad audio):
``gap_extended_windows`` records how far each ad group *could* be cut, and
``extend_outer_edges`` applies that only to the outer edges of the windows
that survive merging and filtering.

Content protection depends on boundary refinement: a side is only extended
when the cut edge still sits on the first/last ad segment's transcribed edge.
After an ad-labelled show intro the gap is often the theme tune (content);
it is kept only because the refiner moved that cut edge inward. With
refinement off, or when the refiner returns no window for a group, such a
gap is cut (up to the caps below). Keep ``enable_boundary_refinement`` on.
"""

import logging
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

from shared import defaults as DEFAULTS

# Same as the shortest stretch transcript gap-fill retries: whatever gap-fill
# leaves of a stretch it tried (partly filled, or nothing heard) still counts.
MIN_UNTRANSCRIBED_GAP_SECONDS = DEFAULTS.WHISPER_GAP_FILL_MIN_GAP_SECONDS
# Groq can return zero segments for a whole 6 MB chunk (~6 min). A gap longer
# than these caps is more likely a transcription dropout than an ad, so it is
# not extended at all (and a warning is logged).
MAX_INTERIOR_UNTRANSCRIBED_GAP_SECONDS = 60.0
MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS = 180.0

_EPS = 1e-6


class AdWindow(NamedTuple):
    """Transcribed ad window (start/end) and its gap-extended cut edges."""

    start: float
    end: float
    cut_start: float
    cut_end: float


def gap_extended_windows(
    groups: Iterable[Any],
    transcript_spans: Sequence[tuple[float, float]],
    *,
    audio_duration_seconds: float | None,
    post_id: Any = None,
    min_gap_seconds: float = MIN_UNTRANSCRIBED_GAP_SECONDS,
    max_interior_gap_seconds: float = MAX_INTERIOR_UNTRANSCRIBED_GAP_SECONDS,
    max_edge_gap_seconds: float = MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS,
    logger: logging.Logger | None = None,
) -> list[AdWindow]:
    """Return each ad group's window plus how far it may extend into gaps.

    ``transcript_spans`` must cover every transcript segment of the episode
    (ad and non-ad), so a gap is only ever untranscribed audio. A side is
    skipped when its cut edge was already moved inside the group by boundary
    refinement. The audio end is only treated as a boundary when
    ``audio_duration_seconds`` is known. Gaps to the audio start/end are capped
    at ``max_edge_gap_seconds``, gaps between transcript segments at
    ``max_interior_gap_seconds``.
    """
    log = logger or logging.getLogger(__name__)

    def within_cap(gap_start: float, gap_end: float, cap: float) -> bool:
        if gap_end - gap_start <= cap + _EPS:
            return True
        log.warning(
            "Post %s: untranscribed gap %.1f-%.1f (%.1fs) beside an ad exceeds "
            "the %.0fs cap; not extending the cut over it",
            post_id,
            gap_start,
            gap_end,
            gap_end - gap_start,
            cap,
        )
        return False

    windows: list[AdWindow] = []
    for group in groups:
        cut_start, cut_end = group.start_time, group.end_time
        if group.segments:
            first_start = min(s.start_time for s in group.segments)
            last_end = max(s.end_time for s in group.segments)

            if group.start_time <= first_start + _EPS:
                prevs = [e for s, e in transcript_spans if s < first_start - _EPS]
                prev_end = max(prevs, default=0.0)
                cap = max_interior_gap_seconds if prevs else max_edge_gap_seconds
                if first_start - prev_end >= min_gap_seconds and within_cap(
                    prev_end, first_start, cap
                ):
                    log.info(
                        "Post %s: extending ad cut start %.1f -> %.1f over "
                        "untranscribed gap",
                        post_id,
                        cut_start,
                        prev_end,
                    )
                    cut_start = prev_end

            if group.end_time >= last_end - _EPS:
                nexts = [s for s, e in transcript_spans if e > last_end + _EPS]
                next_start = min(nexts, default=audio_duration_seconds)
                cap = max_interior_gap_seconds if nexts else max_edge_gap_seconds
                if (
                    next_start is not None
                    and next_start - last_end >= min_gap_seconds
                    and within_cap(last_end, next_start, cap)
                ):
                    log.info(
                        "Post %s: extending ad cut end %.1f -> %.1f over "
                        "untranscribed gap",
                        post_id,
                        cut_end,
                        next_start,
                    )
                    cut_end = next_start

        windows.append(AdWindow(group.start_time, group.end_time, cut_start, cut_end))
    return windows


def extend_outer_edges(
    merged: Sequence[tuple[float, float]], windows: Sequence[AdWindow]
) -> list[tuple[float, float]]:
    """Widen merged/filtered cut windows by their members' gap extensions.

    ``merged`` must come from merging and filtering the *transcribed* windows,
    so each entry spans one or more whole ``windows``. Only the outer edges
    move; windows that now touch (both bordered the same gap) are coalesced.
    """
    out: list[tuple[float, float]] = []
    for start, end in sorted(merged):
        members = [
            w for w in windows if w.start >= start - _EPS and w.end <= end + _EPS
        ]
        new_start = min([start, *(w.cut_start for w in members)])
        new_end = max([end, *(w.cut_end for w in members)])
        if out and new_start <= out[-1][1] + _EPS:
            out[-1] = (out[-1][0], max(out[-1][1], new_end))
        else:
            out.append((new_start, new_end))
    return out
