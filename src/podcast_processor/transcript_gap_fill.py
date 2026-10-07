"""Re-transcribe stretches of audio the primary transcription skipped.

Whisper (local or hosted) can emit no segments at all for a stretch of clear
speech: decoding conditioned on earlier text drifts, a timestamp jump skips
ahead, or a chunk comes back short. Audio no segment covers is never shown to
the ad classifier, so an ad read there stays in the episode.

This pass runs after the primary transcription and before the segments are
stored. It finds every uncovered stretch (including before the first and after
the last segment, up to the real audio duration), cuts each one out with a
little padding, and transcribes it on its own with local whisper. A short clip
gives whisper a fresh context, which recovers speech even when the primary
transcriber was the same local model. Recovered segments are offset back to
episode time, filtered for decoder junk (short low-confidence fragments
would otherwise shrink an untranscribed stretch below the ad-cut backstop in
``untranscribed_gaps``), trimmed so they never overlap what is already
transcribed, and merged in time order. Every dropped segment is logged. Whatever is still uncovered is retried unpadded from
where recovered speech stops, because a clip opening mid-sentence can make
whisper skip the rest of its 30s window the same way the primary pass did.
"""

from __future__ import annotations

import bisect
import gc
import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, NamedTuple, Protocol, runtime_checkable

import ffmpeg
import numpy as np

from podcast_processor.audio import get_audio_duration_ms
from podcast_processor.transcribe import Segment
from shared import defaults as DEFAULTS
from shared.config import Config, LocalWhisperConfig, TestWhisperConfig

SAMPLE_RATE = 16000
# A recovered segment that lies mostly inside already-transcribed audio is the
# padding being re-read, not new speech.
_MIN_UNCOVERED_FRACTION = 0.5
_MIN_SEGMENT_SECONDS = 0.05
_HAS_WORD = re.compile(r"\w")
_WORD = re.compile(r"[\w']+")
# Character similarity above which a recovered phrase counts as a repeat of the
# neighbouring segment's edge words ("God bless." vs "Dog bless." is 0.78).
_DUPLICATE_RATIO = 0.75
# Only recovered segments starting/ending this close to a primary neighbour
# (beyond the padding) are checked as re-reads of it; further in, a repeat of
# the neighbour's words is the ad repeating its own copy.
_REPEAT_SLACK_SECONDS = 1.5
# Whisper's own failed-decode thresholds (logprob_threshold,
# compression_ratio_threshold), applied per segment since there is no fallback.
_MIN_AVG_LOGPROB = -1.0
_MAX_COMPRESSION_RATIO = 2.4
# Junk filters, tuned on real base.en output for post 1025 (stats are per 30s
# decode, shared by every segment in it). Every 1-2 word fragment there was a
# mis-heard tail of a primary segment ("Yes." lp -0.95, "Good." -0.67, "F***"
# -0.87 with no-speech 0.58); real recovered ad decodes ran -0.29 to -0.57
# with no-speech up to 0.57. A kept fragment matters: it shrinks an
# untranscribed stretch below the cut backstop beside an ad.
_NO_SPEECH_PROB = 0.6  # whisper's no_speech_threshold
_CONFIDENT_LOGPROB = -0.6
_FRAGMENT_MAX_WORDS = 2
_RETRY_PASSES = 2


@runtime_checkable
class WhisperModel(Protocol):
    def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]: ...


ModelLoader = Callable[[str], WhisperModel]
AudioLoader = Callable[[str, float, float], np.ndarray]


class Dropped(NamedTuple):
    segment: Segment
    reason: str


class MergeResult(NamedTuple):
    merged: list[Segment]
    added: list[Segment]
    dropped: list[Dropped]


class Window(NamedTuple):
    """An audio slice to re-transcribe; ``start``/``end`` include padding."""

    gap_start: float
    gap_end: float
    start: float
    end: float


@dataclass(frozen=True)
class GapFillSettings:
    model_name: str
    min_gap_seconds: float = DEFAULTS.WHISPER_GAP_FILL_MIN_GAP_SECONDS
    padding_seconds: float = DEFAULTS.WHISPER_GAP_FILL_PADDING_SECONDS
    max_window_seconds: float = DEFAULTS.WHISPER_GAP_FILL_MAX_WINDOW_SECONDS
    language: str = "en"


def gap_fill_settings_from_config(config: Config) -> GapFillSettings | None:
    """Return the effective gap-fill settings, or None when the pass is off.

    The model is the explicit ``whisper_gap_fill_model`` if set, else the
    effective (env-resolved) local whisper model when local whisper is the
    primary transcriber, else the local default.
    """
    if not config.whisper_gap_fill_enabled:
        return None
    if isinstance(config.whisper, TestWhisperConfig):
        return None
    model_name = config.whisper_gap_fill_model
    if not model_name and isinstance(config.whisper, LocalWhisperConfig):
        model_name = config.whisper.model
    language = getattr(config.whisper, "language", None) or "en"
    return GapFillSettings(
        model_name=model_name or DEFAULTS.WHISPER_LOCAL_MODEL,
        min_gap_seconds=config.whisper_gap_fill_min_gap_seconds,
        padding_seconds=config.whisper_gap_fill_padding_seconds,
        max_window_seconds=config.whisper_gap_fill_max_window_seconds,
        language=language,
    )


def _covered_spans(segments: Sequence[Segment]) -> list[tuple[float, float]]:
    """Union of segment time ranges, sorted."""
    spans: list[tuple[float, float]] = []
    for seg in sorted(segments, key=lambda s: (s.start, s.end)):
        start, end = seg.start, max(seg.start, seg.end)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def find_gaps(
    segments: Sequence[Segment],
    audio_duration_seconds: float | None,
    min_gap_seconds: float,
) -> list[tuple[float, float]]:
    """Uncovered stretches of at least ``min_gap_seconds``.

    Includes the stretch before the first segment and, when the duration is
    known, after the last one. With no segments the whole file is one gap.
    """
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in _covered_spans(segments):
        if start - cursor >= min_gap_seconds:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if (
        audio_duration_seconds is not None
        and audio_duration_seconds - cursor >= min_gap_seconds
    ):
        gaps.append((cursor, audio_duration_seconds))
    return gaps


def plan_windows(
    gaps: Sequence[tuple[float, float]],
    audio_duration_seconds: float | None,
    padding_seconds: float,
    max_window_seconds: float,
) -> list[Window]:
    """Split each gap into padded windows no longer than ``max_window_seconds``.

    Padding is clamped to the audio bounds. Neighbouring windows of one long
    gap overlap by the padding; the merge step drops the duplicate speech.
    """
    upper = audio_duration_seconds if audio_duration_seconds is not None else math.inf
    step = max(max_window_seconds - 2 * padding_seconds, 1.0)
    windows: list[Window] = []
    for gap_start, gap_end in gaps:
        pieces = max(1, math.ceil((gap_end - gap_start) / step))
        length = (gap_end - gap_start) / pieces
        for i in range(pieces):
            piece_start = gap_start + i * length
            piece_end = gap_end if i == pieces - 1 else piece_start + length
            windows.append(
                Window(
                    gap_start=gap_start,
                    gap_end=gap_end,
                    start=max(0.0, piece_start - padding_seconds),
                    end=min(upper, piece_end + padding_seconds),
                )
            )
    return windows


def _uncovered_parts(
    start: float, end: float, covered: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    parts: list[tuple[float, float]] = []
    cursor = start
    for c_start, c_end in covered:
        if c_end <= cursor:
            continue
        if c_start >= end:
            break
        if c_start > cursor:
            parts.append((cursor, c_start))
        cursor = max(cursor, c_end)
    if cursor < end:
        parts.append((cursor, end))
    return parts


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _repeats_neighbour(
    text: str, before: Segment | None, after: Segment | None
) -> bool:
    """True when ``text`` re-reads the end of ``before`` or start of ``after``.

    Whisper's segment end times run early, so a short "gap" after a segment
    often holds the tail of that same sentence; the padded clip then
    transcribes it a second time.
    """
    words = _words(text)
    if not words:
        return True
    phrase = " ".join(words)
    n = len(words)
    for neighbour, edge in ((before, slice(-n, None)), (after, slice(0, n))):
        if neighbour is None:
            continue
        near = " ".join(_words(neighbour.text)[edge])
        if near and SequenceMatcher(None, phrase, near).ratio() >= _DUPLICATE_RATIO:
            return True
    return False


def merge_recovered(
    existing: Sequence[Segment],
    recovered: Sequence[Segment],
    *,
    primary: Sequence[Segment] | None = None,
    repeat_edge_seconds: float = (
        DEFAULTS.WHISPER_GAP_FILL_PADDING_SECONDS + _REPEAT_SLACK_SECONDS
    ),
) -> MergeResult:
    """Merge recovered segments into ``existing`` without overlap or repeats.

    A recovered segment mostly inside already-covered time (existing or an
    earlier accepted recovery) is dropped; otherwise its times are trimmed to
    its longest uncovered stretch. Its text is kept whole: there are no word
    timings to cut it by, and extra ad words only help the classifier.

    A segment starting within ``repeat_edge_seconds`` of the end of the
    ``primary`` segment before it (default: ``existing``), or ending that close
    to the start of the one after, is dropped when it repeats that
    neighbour's edge words: it is the padding re-read. Further into the gap,
    and between recovered segments, repeats are kept, as ad copy repeats.
    """
    merged = sorted(existing, key=lambda s: (s.start, s.end))
    originals = sorted(
        primary if primary is not None else existing, key=lambda s: s.start
    )
    original_starts = [o.start for o in originals]
    added: list[Segment] = []
    dropped: list[Dropped] = []
    for seg in sorted(recovered, key=lambda s: (s.start, s.end)):
        duration = seg.end - seg.start
        if duration < _MIN_SEGMENT_SECONDS:
            dropped.append(Dropped(seg, "too short"))
            continue
        parts = _uncovered_parts(seg.start, seg.end, _covered_spans(merged))
        free = sum(e - s for s, e in parts)
        if free < duration * _MIN_UNCOVERED_FRACTION:
            dropped.append(Dropped(seg, "already transcribed"))
            continue
        start, end = max(parts, key=lambda p: p[1] - p[0])
        if end - start < _MIN_SEGMENT_SECONDS:
            dropped.append(Dropped(seg, "already transcribed"))
            continue
        o_idx = bisect.bisect_left(original_starts, start)
        before = originals[o_idx - 1] if o_idx > 0 else None
        after = originals[o_idx] if o_idx < len(originals) else None
        if before is not None and seg.start - before.end > repeat_edge_seconds:
            before = None
        if after is not None and after.start - seg.end > repeat_edge_seconds:
            after = None
        if _repeats_neighbour(seg.text, before, after):
            dropped.append(Dropped(seg, "repeats neighbour"))
            continue
        kept = Segment(start=start, end=end, text=seg.text)
        merged.insert(bisect.bisect_left([m.start for m in merged], start), kept)
        added.append(kept)
    return MergeResult(merged, added, dropped)


def rejection_reason(raw: Mapping[str, Any]) -> str | None:
    """Why a raw whisper segment from a gap window is junk, or None if not."""
    text = str(raw.get("text", ""))
    if not _HAS_WORD.search(text):
        return "no words"
    logprob = float(raw.get("avg_logprob", 0.0))
    no_speech = float(raw.get("no_speech_prob", 0.0))
    if logprob < _MIN_AVG_LOGPROB:
        return "low confidence"
    if float(raw.get("compression_ratio", 0.0)) > _MAX_COMPRESSION_RATIO:
        return "repetitive"
    if no_speech > _NO_SPEECH_PROB and logprob < _CONFIDENT_LOGPROB:
        return "likely no speech"
    if len(_words(text)) <= _FRAGMENT_MAX_WORDS and (
        no_speech > _NO_SPEECH_PROB or logprob < _CONFIDENT_LOGPROB
    ):
        return "weak fragment"
    return None


def load_audio_window(path: str, start: float, duration: float) -> np.ndarray:
    """Decode ``duration`` seconds from ``start`` as 16 kHz mono float32."""
    out, _ = (
        ffmpeg.input(path, ss=start, t=duration)
        .output("pipe:", format="s16le", acodec="pcm_s16le", ac=1, ar=SAMPLE_RATE)
        .run(capture_stdout=True, capture_stderr=True)
    )
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768.0


def load_whisper_model(name: str) -> WhisperModel:
    import whisper  # deferred: heavy import, CUDA probing

    return whisper.load_model(name=name)


class WhisperGapFiller:
    """Fill untranscribed stretches with local whisper, one model per pass."""

    def __init__(
        self,
        logger: logging.Logger,
        settings: GapFillSettings,
        model_loader: ModelLoader | None = None,
        audio_loader: AudioLoader | None = None,
        duration_probe: Callable[[str], int | None] | None = None,
    ):
        self.logger = logger
        self.settings = settings
        self.model_loader = model_loader or load_whisper_model
        self.audio_loader = audio_loader or load_audio_window
        self.duration_probe = duration_probe or get_audio_duration_ms

    def fill(
        self, post_id: Any, audio_path: str, segments: Sequence[Segment]
    ) -> list[Segment]:
        """Return ``segments`` plus recovered speech, sorted by start time.

        Never raises: on any failure the primary segments are returned as-is.
        """
        try:
            return self._fill(post_id, audio_path, segments)
        except Exception:  # noqa: BLE001 - gap-fill is best-effort
            self.logger.warning(
                "Post %s: transcript gap-fill failed; keeping primary transcript",
                post_id,
                exc_info=True,
            )
            return sorted(segments, key=lambda s: (s.start, s.end))

    def _fill(
        self, post_id: Any, audio_path: str, segments: Sequence[Segment]
    ) -> list[Segment]:
        s = self.settings
        duration_ms = self.duration_probe(audio_path)
        duration = duration_ms / 1000.0 if duration_ms is not None else None
        if duration is None:
            self.logger.warning(
                "Post %s: audio duration unknown; gap-fill skips the end-of-audio gap",
                post_id,
            )
        gaps = find_gaps(segments, duration, s.min_gap_seconds)
        if not gaps:
            self.logger.info(
                "Post %s: gap-fill found no untranscribed gaps >= %.1fs",
                post_id,
                s.min_gap_seconds,
            )
            return sorted(segments, key=lambda seg: (seg.start, seg.end))

        started = time.time()
        primary = sorted(segments, key=lambda seg: (seg.start, seg.end))
        merged = primary
        added: list[Segment] = []
        # Speech whisper emitted but that was dropped as junk or a re-read.
        # Not stored, but retries start after it like after kept speech.
        heard: list[Segment] = []
        drops: Counter[str] = Counter()
        attempted: set[tuple[float, float]] = set()
        windows_run = 0
        windows_failed = 0
        seconds_run = 0.0
        model = self.model_loader(s.model_name)
        try:
            # Later passes retry what is still uncovered, unpadded and starting
            # where the last emitted speech ended, kept or dropped: a clip that
            # opens on the tail of a sentence can make whisper emit that
            # fragment and then jump the rest of its 30s window. Windows are never re-run with
            # the same bounds, so a silent stretch costs at most two attempts.
            paddings = (s.padding_seconds, *([0.0] * _RETRY_PASSES))
            for pass_no, padding in enumerate(paddings, start=1):
                remaining = find_gaps([*merged, *heard], duration, s.min_gap_seconds)
                planned = plan_windows(
                    remaining, duration, padding, s.max_window_seconds
                )
                for window in planned:
                    key = (round(window.start, 2), round(window.end, 2))
                    if key in attempted:
                        continue
                    attempted.add(key)
                    windows_run += 1
                    seconds_run += window.end - window.start
                    # One bad window (e.g. a corrupt frame ffmpeg cannot
                    # decode) must not discard what other windows recovered.
                    try:
                        found, rejected = self._transcribe_window(
                            model, audio_path, window
                        )
                        result = merge_recovered(
                            merged,
                            found,
                            primary=primary,
                            repeat_edge_seconds=s.padding_seconds
                            + _REPEAT_SLACK_SECONDS,
                        )
                    except Exception:  # noqa: BLE001 - keep other windows
                        windows_failed += 1
                        self.logger.warning(
                            "Post %s: gap-fill pass %d window %.1f-%.1f failed; "
                            "keeping speech recovered so far",
                            post_id,
                            pass_no,
                            window.start,
                            window.end,
                            exc_info=True,
                        )
                        continue
                    merged = result.merged
                    added.extend(result.added)
                    for dropped in (*rejected, *result.dropped):
                        if dropped.reason != "already transcribed":
                            heard.append(dropped.segment)
                        drops[dropped.reason] += 1
                        self.logger.info(
                            "Post %s: gap-fill dropped %.2f-%.2f (%s): %r",
                            post_id,
                            dropped.segment.start,
                            dropped.segment.end,
                            dropped.reason,
                            dropped.segment.text,
                        )
                    if not result.added:
                        self.logger.info(
                            "Post %s: gap-fill pass %d window %.1f-%.1f (gap "
                            "%.1f-%.1f) yielded no new speech",
                            post_id,
                            pass_no,
                            window.start,
                            window.end,
                            window.gap_start,
                            window.gap_end,
                        )
        finally:
            del model
            gc.collect()

        left = find_gaps(merged, duration, s.min_gap_seconds)
        self.logger.info(
            "Post %s: gap-fill (%s) found %d gaps totalling %.1fs, re-transcribed "
            "%.1fs in %d windows (%d failed), added %d segments, dropped %d (%s) "
            "in %.1fs; %.1fs still untranscribed",
            post_id,
            s.model_name,
            len(gaps),
            sum(e - b for b, e in gaps),
            seconds_run,
            windows_run,
            windows_failed,
            len(added),
            sum(drops.values()),
            ", ".join(f"{reason} {n}" for reason, n in sorted(drops.items())) or "none",
            time.time() - started,
            sum(e - b for b, e in left),
        )
        return merged

    def _transcribe_window(
        self, model: WhisperModel, audio_path: str, window: Window
    ) -> tuple[list[Segment], list[Dropped]]:
        """Recovered segments in episode time, plus those rejected as junk."""
        audio = self.audio_loader(audio_path, window.start, window.end - window.start)
        if audio.size == 0:
            return [], []
        result = model.transcribe(
            audio,
            fp16=False,
            language=self.settings.language,
            condition_on_previous_text=False,
            # No temperature fallback: sampled re-decodes of a short clip of
            # silence or music are where hallucinated words come from.
            temperature=0.0,
        )
        out: list[Segment] = []
        rejected: list[Dropped] = []
        for raw in result.get("segments") or []:
            start = window.start + float(raw["start"])
            # Whisper pads a short clip to 30s and can time speech past its end.
            end = min(window.start + float(raw["end"]), window.end)
            seg = Segment(
                start=start, end=max(start, end), text=str(raw.get("text", ""))
            )
            reason = rejection_reason(raw)
            if reason is None and end <= start:
                reason = "past window end"
            if reason is None:
                out.append(seg)
            else:
                rejected.append(Dropped(seg, reason))
        return out, rejected
