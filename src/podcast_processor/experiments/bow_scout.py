"""CueDetector-based scout: flag segments and stitch padded windows.

This wraps CueDetector rather than inventing a second bag-of-words lexicon.
Production AdClassifier still walks the full transcript; this module is
experiment-only and is not imported by PodcastProcessor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from podcast_processor.cue_detector import DEFAULT_SIGNAL_WEIGHTS, CueDetector
from podcast_processor.experiments.types import ScoutSegment, ScoutWindow


@dataclass(frozen=True)
class ScoutConfig:
    """Thresholds and padding for stitching likely-ad windows."""

    threshold: float = 0.8
    pad_seconds: float = 15.0
    pad_segments: int = 3
    merge_gap_seconds: float = 8.0
    include_scout_extras: bool = True
    # If True, flag self-promo even when it is the only signal.
    include_self_promo: bool = False


def default_scout_config() -> ScoutConfig:
    return ScoutConfig()


class BowScout:
    def __init__(
        self,
        config: ScoutConfig | None = None,
        detector: CueDetector | None = None,
    ) -> None:
        self.config = config or default_scout_config()
        self.detector = detector or CueDetector(
            include_scout_extras=self.config.include_scout_extras
        )

    def score_segment(self, segment: ScoutSegment) -> tuple[float, dict[str, bool]]:
        score, signals = self.detector.score(segment.text)
        if not self.config.include_self_promo and signals.get("self_promo"):
            # Keep the signal for reporting but drop its contribution unless
            # another cue also fired. Self-promo is a known AdClassifier demotion.
            if not any(
                signals.get(key)
                for key in ("url", "promo", "phone", "cta", "sponsor", "ad_break")
            ):
                score = max(0.0, score - DEFAULT_SIGNAL_WEIGHTS.get("self_promo", 0.0))
        return score, signals

    def flag_indices(self, segments: list[ScoutSegment]) -> list[int]:
        flagged: list[int] = []
        for idx, segment in enumerate(segments):
            score, _signals = self.score_segment(segment)
            if score >= self.config.threshold:
                flagged.append(idx)
        return flagged

    def scout(self, segments: list[ScoutSegment]) -> list[ScoutWindow]:
        if not segments:
            return []

        scores: list[float] = []
        signals_by_idx: list[dict[str, bool]] = []
        flagged = [False] * len(segments)
        for idx, segment in enumerate(segments):
            score, signals = self.score_segment(segment)
            scores.append(score)
            signals_by_idx.append(signals)
            if score >= self.config.threshold:
                flagged[idx] = True

        expanded = self._expand_flags(segments, flagged)
        return self._stitch(segments, expanded, scores, signals_by_idx)

    def _expand_flags(
        self, segments: list[ScoutSegment], flagged: list[bool]
    ) -> list[bool]:
        expanded = list(flagged)
        n = len(segments)
        pad_seg = max(0, self.config.pad_segments)
        pad_sec = max(0.0, self.config.pad_seconds)

        seed_indices = [i for i, is_flagged in enumerate(flagged) if is_flagged]
        for idx in seed_indices:
            lo = max(0, idx - pad_seg)
            hi = min(n - 1, idx + pad_seg)
            for j in range(lo, hi + 1):
                expanded[j] = True

            seed = segments[idx]
            window_start = seed.start_time - pad_sec
            window_end = seed.end_time + pad_sec
            for j, segment in enumerate(segments):
                if segment.end_time < window_start or segment.start_time > window_end:
                    continue
                expanded[j] = True
        return expanded

    def _stitch(
        self,
        segments: list[ScoutSegment],
        flagged: list[bool],
        scores: list[float],
        signals_by_idx: list[dict[str, bool]],
    ) -> list[ScoutWindow]:
        windows: list[ScoutWindow] = []
        current_indices: list[int] = []

        def flush() -> None:
            if not current_indices:
                return
            windows.append(
                self._window_from_indices(
                    segments, current_indices, scores, signals_by_idx
                )
            )
            current_indices.clear()

        for idx, is_flagged in enumerate(flagged):
            if is_flagged:
                current_indices.append(idx)
            else:
                flush()
        flush()

        return self._merge_close_windows(windows, segments, scores, signals_by_idx)

    def _merge_close_windows(
        self,
        windows: list[ScoutWindow],
        segments: list[ScoutSegment],
        scores: list[float],
        signals_by_idx: list[dict[str, bool]],
    ) -> list[ScoutWindow]:
        if not windows:
            return []
        gap = self.config.merge_gap_seconds
        merged: list[ScoutWindow] = [windows[0]]
        for window in windows[1:]:
            prev = merged[-1]
            if window.start_time - prev.end_time <= gap:
                combined_indices = sorted(
                    set(prev.segment_indices) | set(window.segment_indices)
                )
                merged[-1] = self._window_from_indices(
                    segments, combined_indices, scores, signals_by_idx
                )
            else:
                merged.append(window)
        return merged

    def _window_from_indices(
        self,
        segments: list[ScoutSegment],
        indices: list[int],
        scores: list[float],
        signals_by_idx: list[dict[str, bool]],
    ) -> ScoutWindow:
        window_segments = [segments[i] for i in indices]
        cue_types: list[str] = []
        seen: set[str] = set()
        for i in indices:
            for key, fired in signals_by_idx[i].items():
                if fired and key not in seen:
                    seen.add(key)
                    cue_types.append(key)
        peak = max((scores[i] for i in indices), default=0.0)
        return ScoutWindow(
            start_time=window_segments[0].start_time,
            end_time=window_segments[-1].end_time,
            start_seq=window_segments[0].sequence_num,
            end_seq=window_segments[-1].sequence_num,
            segment_indices=list(indices),
            peak_score=peak,
            cue_types=cue_types,
            segments=window_segments,
        )


def overlap_seconds(
    a_start: int | float,
    a_end: int | float,
    b_start: int | float,
    b_end: int | float,
) -> float:
    return max(
        0.0, min(float(a_end), float(b_end)) - max(float(a_start), float(b_start))
    )


def with_config(base: ScoutConfig, **overrides: float | int | bool) -> ScoutConfig:
    return replace(base, **overrides)
