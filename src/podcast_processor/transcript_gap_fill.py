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
episode time, trimmed so they never overlap what is already transcribed, and
merged in time order.
"""

from __future__ import annotations

import bisect
import gc
import logging
import math
import re
import time
from collections.abc import Callable, Sequence
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


@runtime_checkable
class WhisperModel(Protocol):
    def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]: ...


ModelLoader = Callable[[str], WhisperModel]
AudioLoader = Callable[[str, float, float], np.ndarray]


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
    existing: Sequence[Segment], recovered: Sequence[Segment]
) -> tuple[list[Segment], list[Segment]]:
    """Merge recovered segments into ``existing`` without overlap or repeats.

    A recovered segment mostly inside already-covered time (existing or an
    earlier accepted recovery) is dropped; otherwise its times are trimmed to
    its longest uncovered stretch. One that repeats the words at the edge of
    its neighbouring segment is dropped too. Returns (merged sorted, added).
    """
    merged = sorted(existing, key=lambda s: (s.start, s.end))
    added: list[Segment] = []
    for seg in sorted(recovered, key=lambda s: (s.start, s.end)):
        duration = seg.end - seg.start
        if duration < _MIN_SEGMENT_SECONDS:
            continue
        parts = _uncovered_parts(seg.start, seg.end, _covered_spans(merged))
        free = sum(e - s for s, e in parts)
        if free < duration * _MIN_UNCOVERED_FRACTION:
            continue
        start, end = max(parts, key=lambda p: p[1] - p[0])
        if end - start < _MIN_SEGMENT_SECONDS:
            continue
        idx = bisect.bisect_left([m.start for m in merged], start)
        before = merged[idx - 1] if idx > 0 else None
        after = merged[idx] if idx < len(merged) else None
        if _repeats_neighbour(seg.text, before, after):
            continue
        kept = Segment(start=start, end=end, text=seg.text)
        merged.insert(idx, kept)
        added.append(kept)
    return merged, added


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

        windows = plan_windows(gaps, duration, s.padding_seconds, s.max_window_seconds)
        started = time.time()
        merged = sorted(segments, key=lambda seg: (seg.start, seg.end))
        added: list[Segment] = []
        model = self.model_loader(s.model_name)
        try:
            for window in windows:
                found = self._transcribe_window(model, audio_path, window)
                merged, new = merge_recovered(merged, found)
                added.extend(new)
                if not new:
                    self.logger.info(
                        "Post %s: gap-fill window %.1f-%.1f (gap %.1f-%.1f) "
                        "yielded no new speech; leaving it untranscribed",
                        post_id,
                        window.start,
                        window.end,
                        window.gap_start,
                        window.gap_end,
                    )
        finally:
            del model
            gc.collect()

        self.logger.info(
            "Post %s: gap-fill (%s) found %d gaps totalling %.1fs, re-transcribed "
            "%.1fs in %d windows, added %d segments in %.1fs",
            post_id,
            s.model_name,
            len(gaps),
            sum(e - b for b, e in gaps),
            sum(w.end - w.start for w in windows),
            len(windows),
            len(added),
            time.time() - started,
        )
        return merged

    def _transcribe_window(
        self, model: WhisperModel, audio_path: str, window: Window
    ) -> list[Segment]:
        audio = self.audio_loader(audio_path, window.start, window.end - window.start)
        if audio.size == 0:
            return []
        result = model.transcribe(
            audio,
            fp16=False,
            language=self.settings.language,
            condition_on_previous_text=False,
        )
        out: list[Segment] = []
        for raw in result.get("segments") or []:
            text = str(raw.get("text", ""))
            if not _HAS_WORD.search(text):
                continue
            start = window.start + float(raw["start"])
            end = min(window.start + float(raw["end"]), window.end)
            if end > start:
                out.append(Segment(start=start, end=end, text=text))
        return out
