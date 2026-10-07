"""Gap-fill re-transcribes audio the primary transcription left uncovered.

The real path runs end to end (ffprobe duration, ffmpeg window decode, offset,
overlap trim, merge, TranscriptionManager storage); only the whisper model is
stubbed. The stub "hears" speech wherever the decoded window is non-silent, so
offsets and window bounds are checked against what is actually in the audio.
"""

from __future__ import annotations

import logging
import wave
from collections.abc import Generator
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from flask import Flask

from app.extensions import db
from app.models import Feed, Post, TranscriptSegment
from podcast_processor import transcript_gap_fill as gap_fill
from podcast_processor.transcribe import Segment, Transcriber
from podcast_processor.transcript_gap_fill import (
    SAMPLE_RATE,
    GapFillSettings,
    WhisperGapFiller,
    find_gaps,
    gap_fill_settings_from_config,
    merge_recovered,
    plan_windows,
)
from podcast_processor.transcription_manager import TranscriptionManager
from shared.config import (
    GroqWhisperConfig,
    LocalWhisperConfig,
    TestWhisperConfig,
)
from shared.test_utils import create_standard_test_config

FRAME = 0.05  # stub model resolution, seconds
TOL = 0.15


def _write_wav(path: Path, duration: float, speech: list[tuple[float, float]]) -> str:
    """Silent mono 16 kHz wav with a 440 Hz tone over each ``speech`` span."""
    t = np.arange(int(duration * SAMPLE_RATE)) / SAMPLE_RATE
    audio = np.zeros_like(t)
    for start, end in speech:
        mask = (t >= start) & (t < end)
        audio[mask] = 0.5 * np.sin(2 * np.pi * 440 * t[mask])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())
    return str(path)


class StubWhisperModel:
    """Returns one segment per non-silent run, timed relative to the clip."""

    def __init__(self, text: str = " ~ recovered ad speech") -> None:
        self.calls: list[dict[str, Any]] = []
        self.text = text

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"seconds": len(audio) / SAMPLE_RATE, **kwargs})
        hop = int(FRAME * SAMPLE_RATE)
        loud = [
            float(np.sqrt(np.mean(audio[i : i + hop] ** 2))) > 0.05
            for i in range(0, len(audio), hop)
        ]
        segments, run_start = [], None
        for idx, flag in enumerate([*loud, False]):
            if flag and run_start is None:
                run_start = idx
            elif not flag and run_start is not None:
                segments.append(
                    {
                        "start": run_start * FRAME,
                        "end": idx * FRAME,
                        "text": self.text,  # "~" marks recovered text
                    }
                )
                run_start = None
        return {"segments": segments}


class StubLoader:
    def __init__(self, model: Any | None = None) -> None:
        self.model = model or StubWhisperModel()
        self.names: list[str] = []

    def __call__(self, name: str) -> Any:
        self.names.append(name)
        return self.model


def _filler(loader: StubLoader, **overrides: Any) -> WhisperGapFiller:
    settings = GapFillSettings(model_name="base.en", **overrides)
    return WhisperGapFiller(logging.getLogger("test"), settings, model_loader=loader)


def _seg(start: float, end: float, text: str = "primary") -> Segment:
    return Segment(start=start, end=end, text=text)


# --- gap detection ---------------------------------------------------------


def test_find_gaps_includes_start_interior_and_end() -> None:
    segs = [_seg(4, 10), _seg(20, 25)]
    assert find_gaps(segs, 30.0, 3.0) == [(0.0, 4), (10, 20), (25, 30.0)]


def test_find_gaps_threshold_and_overlapping_segments() -> None:
    # 0-2 start gap is under threshold; overlapping segments form one span.
    segs = [_seg(2, 8), _seg(5, 12), _seg(14, 20)]
    assert find_gaps(segs, 21.0, 3.0) == []
    assert find_gaps(segs, 21.0, 2.0) == [(0.0, 2), (12, 14)]


def test_find_gaps_unknown_duration_has_no_end_gap() -> None:
    assert find_gaps([_seg(0, 5)], None, 3.0) == []


def test_find_gaps_empty_transcript_is_whole_file() -> None:
    assert find_gaps([], 42.0, 3.0) == [(0.0, 42.0)]


# --- window planning -------------------------------------------------------


def test_plan_windows_padding_clamped_to_audio_bounds() -> None:
    windows = plan_windows([(0.0, 4.0), (10.0, 20.0), (28.5, 30.0)], 30.0, 1.0, 60.0)
    assert [(w.start, w.end) for w in windows] == [
        (0.0, 5.0),
        (9.0, 21.0),
        (27.5, 30.0),
    ]


def test_plan_windows_chunks_long_gap() -> None:
    windows = plan_windows([(100.0, 250.0)], 400.0, 1.0, 60.0)
    assert len(windows) == 3
    assert all(w.end - w.start <= 60.0 + 1e-9 for w in windows)
    assert windows[0].start == 99.0 and windows[-1].end == 251.0
    # pieces tile the gap with no hole
    for prev, nxt in pairwise(windows):
        assert nxt.start < prev.end


# --- merge -----------------------------------------------------------------


def test_merge_drops_mostly_covered_and_trims_partial_overlap() -> None:
    existing = [_seg(0, 10), _seg(20, 30)]
    recovered = [
        _seg(9.0, 10.4, "padding re-read"),  # 1.0 of 1.4s covered -> drop
        _seg(9.5, 14.0, "tail of gap"),  # 0.5s covered -> trim to 10-14
        _seg(14.0, 19.0, "middle"),
        _seg(18.8, 20.6, "head"),  # 0.8 of 1.8s covered -> trim to 19-20
    ]
    merged, added, dropped = merge_recovered(existing, recovered)
    assert [(d.segment.text, d.reason) for d in dropped] == [
        ("padding re-read", "already transcribed")
    ]
    assert [(s.start, s.end, s.text) for s in added] == [
        (10, 14.0, "tail of gap"),
        (14.0, 19.0, "middle"),
        (19.0, 20, "head"),
    ]
    assert [s.start for s in merged] == sorted(s.start for s in merged)
    for a, b in pairwise(merged):
        assert a.end <= b.start


def test_merge_drops_duplicate_from_overlapping_chunks() -> None:
    merged, added, _ = merge_recovered([], [_seg(10, 15, "a"), _seg(10.2, 15.1, "a")])
    assert [s.text for s in added] == ["a"]
    assert len(merged) == 1


def test_merge_drops_repeat_of_neighbour_edge_words() -> None:
    """Real case (post 1025): whisper ends a segment early, the padded clip
    re-reads its tail. Repeats are dropped; new words in the same gap are kept."""
    existing = [
        _seg(4809.6, 4815.6, " just to read a shitty review of my work."),
        _seg(4818.7, 4824.8, " And then when I'm finished reading it"),
        _seg(4906.1, 4910.4, " rob a dog. Dog bless."),
    ]
    recovered = [
        _seg(4815.6, 4818.7, " review of my work."),
        _seg(4910.4, 4911.4, " God bless."),
        _seg(4911.4, 4915.0, " This episode is brought to you by Xero."),
    ]
    _, added, dropped = merge_recovered(existing, recovered)
    assert [s.text for s in added] == [" This episode is brought to you by Xero."]
    assert [d.reason for d in dropped] == ["repeats neighbour"] * 2


def test_merge_keeps_recovered_lines_that_echo_each_other() -> None:
    """Ad copy repeats itself; only primary neighbours count as repeats."""
    existing = [_seg(0.0, 10.0, " as good as far as"), _seg(30.0, 40.0, " Hey")]
    recovered = [
        _seg(10.0, 12.0, " No, you did."),
        _seg(12.0, 14.0, " So you did."),
        _seg(14.0, 16.0, " This is your business."),
        _seg(16.0, 18.0, " This is your business."),
    ]
    _, added, _ = merge_recovered(existing, recovered)
    assert len(added) == 4


# Real primary lines from post 1025 (segments 618-633): one Xero read.
XERO_READ = [
    (2391.16, 2392.92, " This is your business."),
    (2392.92, 2394.52, " This is your business super chance"),
    (2394.52, 2397.16, " with the help of zero accounting software!"),
    (2397.16, 2399.16, " This is managing cash flow."),
    (2399.16, 2400.56, " This is managing your cash flow"),
    (2400.56, 2402.96, " with the help of zero accounting software!"),
    (2402.96, 2404.84, " These are your customers paying you..."),
    (2404.84, 2407.0, " These are your customers having more ways to pay you"),
    (2407.0, 2409.64, " with the help of zero accounting software!"),
    (2409.64, 2411.56, " This is your business super chance with the help of zero"),
    (2411.56, 2412.72, " helping you solve your cash flow"),
    (2412.72, 2414.28, " by giving your customers more ways to pay"),
    (2414.28, 2417.08, " so now you can focus on making your business move!"),
    (2417.08, 2419.48, " Super-tied your business today with the help of zero."),
    (2419.48, 2421.32, " Don't share it with an ex!"),
]
AFTER_XERO = (2421.32, 2423.08, " Hey, still, how's hunting next weekend?")


def test_merge_keeps_back_to_back_repeat_of_whole_ad() -> None:
    """The same ad played twice in a row; the primary got the first copy and
    skipped the second. The second copy's closing line repeats the primary
    line before the gap, 30s away from it, and must be kept."""
    shift = XERO_READ[-1][1] - XERO_READ[0][0]
    copy1 = [_seg(*line) for line in XERO_READ]
    after = _seg(AFTER_XERO[0] + shift, AFTER_XERO[1] + shift, AFTER_XERO[2])
    copy2 = [_seg(a + shift, b + shift, t) for a, b, t in XERO_READ]

    _, added, dropped = merge_recovered([*copy1, after], copy2)

    assert [s.text for s in added] == [t for _, _, t in XERO_READ]
    assert added[-1].text == " Don't share it with an ex!"
    assert dropped == []


def test_merge_keeps_repeat_of_neighbour_inside_one_read() -> None:
    """A gap inside one read: the primary line before it ends "with the help
    of zero accounting software!" and the read repeats that line twice more,
    4s and 10s into the gap."""
    primary = [_seg(*line) for line in [*XERO_READ[:3], *XERO_READ[13:]]]
    recovered = [_seg(*line) for line in XERO_READ[3:13]]

    _, added, dropped = merge_recovered(primary, recovered)

    assert [s.text for s in added] == [t for _, _, t in XERO_READ[3:13]]
    assert dropped == []


def test_merge_repeat_check_is_limited_to_the_gap_edges() -> None:
    """Within padding + 1.5s of a primary neighbour a repeat of its edge words
    is the padding re-read and is dropped; past that it is kept."""
    primary = [_seg(0.0, 5.0, " Search Xero with an X."), _seg(40.0, 45.0, " Hey")]
    recovered = [
        _seg(5.5, 7.0, " with an X."),  # 0.5s after the neighbour: re-read
        _seg(20.0, 22.0, " Search Xero with an X."),  # 15s in: ad repeats
        _seg(37.0, 39.5, " Hey"),  # 0.5s before the next neighbour
    ]

    _, added, dropped = merge_recovered(primary, recovered, repeat_edge_seconds=2.5)

    assert [(s.start, s.text) for s in added] == [(20.0, " Search Xero with an X.")]
    assert [(d.segment.start, d.reason) for d in dropped] == [
        (5.5, "repeats neighbour"),
        (37.0, "repeats neighbour"),
    ]


# --- end to end through real audio ----------------------------------------


@pytest.fixture
def episode(tmp_path: Path) -> str:
    # speech the primary missed: 0.5-2.5 (start), 13-17 (interior), 27-29 (end)
    return _write_wav(
        tmp_path / "ep.wav",
        30.0,
        [(0.5, 2.5), (4.0, 10.0), (13.0, 17.0), (20.0, 25.0), (27.0, 29.0)],
    )


def test_fill_recovers_start_interior_and_end_speech(episode: str) -> None:
    primary = [_seg(4.0, 10.0, "a"), _seg(20.0, 25.0, "b")]
    loader = StubLoader()

    merged = _filler(loader).fill(1025, episode, primary)

    recovered = [s for s in merged if s.text.startswith(" ~")]
    spans = [(s.start, s.end) for s in recovered]
    assert len(spans) == 3, spans
    for (start, end), (want_start, want_end) in zip(
        spans, [(0.5, 2.5), (13.0, 17.0), (27.0, 29.0)], strict=True
    ):
        assert start == pytest.approx(want_start, abs=TOL)
        assert end == pytest.approx(want_end, abs=TOL)
    assert [s.text for s in merged if not s.text.startswith(" ~")] == ["a", "b"]
    assert [s.start for s in merged] == sorted(s.start for s in merged)
    # one model load for the whole pass, fresh-context decoding per window
    assert loader.names == ["base.en"]
    # pass 1: three padded windows; pass 2 retries the silent 3s stretches
    # left between primary and recovered speech, unpadded
    assert [c["seconds"] for c in loader.model.calls] == pytest.approx(
        [5.0, 12.0, 6.0, 3.0, 3.0], abs=0.01
    )
    for call in loader.model.calls:
        assert call["fp16"] is False
        assert call["condition_on_previous_text"] is False
        assert call["language"] == "en"
        assert call["temperature"] == 0.0


def test_fill_is_noop_without_gaps(episode: str) -> None:
    primary = [_seg(0.0, 15.0, "a"), _seg(16.0, 28.0, "b")]
    loader = StubLoader()

    merged = _filler(loader).fill(7, episode, primary)

    assert merged == primary
    assert loader.names == []


def test_fill_music_gap_yields_nothing_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # 10-20 is "music" the model hears no words in (silence here)
    path = _write_wav(tmp_path / "m.wav", 30.0, [(0.0, 10.0), (20.0, 30.0)])
    primary = [_seg(0.0, 10.0, "a"), _seg(20.0, 30.0, "b")]
    loader = StubLoader()

    with caplog.at_level(logging.INFO, logger="test"):
        merged = _filler(loader).fill(42, path, primary)

    assert merged == primary
    # padded attempt, then one unpadded retry
    assert [c["seconds"] for c in loader.model.calls] == pytest.approx(
        [12.0, 10.0], abs=0.01
    )
    assert (
        "Post 42: gap-fill pass 1 window 9.0-21.0 (gap 10.0-20.0) yielded no new speech"
        in (caplog.text)
    )
    assert "found 1 gaps totalling 10.0s" in caplog.text
    assert "added 0 segments" in caplog.text


def test_fill_long_gap_is_chunked_without_duplicates(tmp_path: Path) -> None:
    path = _write_wav(tmp_path / "long.wav", 160.0, [(0, 5), (30, 130), (155, 160)])
    primary = [_seg(0.0, 5.0, "a"), _seg(155.0, 160.0, "b")]
    loader = StubLoader()

    merged = _filler(loader, max_window_seconds=60.0).fill(1, path, primary)

    # pass 1 chunks the 150s gap into 3 windows; pass 2 retries the two
    # silent 25s ends left uncovered
    assert len(loader.model.calls) == 5
    assert max(c["seconds"] for c in loader.model.calls) <= 60.0 + 0.01
    recovered = [s for s in merged if s.text.startswith(" ~")]
    assert recovered[0].start == pytest.approx(30.0, abs=TOL)
    assert recovered[-1].end == pytest.approx(130.0, abs=TOL)
    covered = sum(s.end - s.start for s in recovered)
    assert covered == pytest.approx(100.0, abs=3 * TOL)
    for a, b in pairwise(merged):
        assert a.end <= b.start + 1e-9


class JumpingWhisperModel(StubWhisperModel):
    """Mimics base.en on post 1025: a clip opening on the tail of earlier speech
    yields only that fragment, then whisper seeks past the rest of its 30s
    window, so the speech after it is never emitted."""

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> dict[str, Any]:
        result = super().transcribe(audio, **kwargs)
        if result["segments"] and result["segments"][0]["start"] == 0.0:
            return {"segments": result["segments"][:1]}
        return result


def test_unpadded_retry_recovers_speech_whisper_jumped_over(tmp_path: Path) -> None:
    path = _write_wav(tmp_path / "jump.wav", 50.0, [(0.0, 10.0), (12.0, 38.0)])
    primary = [_seg(0.0, 10.0, "show"), _seg(40.0, 50.0, "show again")]
    loader = StubLoader(JumpingWhisperModel())

    merged = _filler(loader).fill(1025, path, primary)

    recovered = [s for s in merged if s.text.startswith(" ~")]
    assert len(recovered) == 1
    assert recovered[0].start == pytest.approx(12.0, abs=TOL)
    assert recovered[0].end == pytest.approx(38.0, abs=TOL)
    assert len(loader.model.calls) == 2


def test_retry_walks_past_fragment_when_gap_starts_mid_speech(
    tmp_path: Path,
) -> None:
    """Post 1025 shape: the primary segment's end time runs early, so the gap
    opens on speech. Pass 2 recovers only the fragment; pass 3 starts after it
    and gets the ad."""
    path = _write_wav(tmp_path / "mid.wav", 50.0, [(0.0, 10.0), (12.0, 38.0)])
    primary = [_seg(0.0, 9.5, "show"), _seg(40.0, 50.0, "show again")]
    loader = StubLoader(JumpingWhisperModel())

    merged = _filler(loader).fill(1025, path, primary)

    recovered = [s for s in merged if s.text.startswith(" ~")]
    assert [(round(s.start, 1), round(s.end, 1)) for s in recovered] == [
        (9.5, 10.0),
        (12.0, 38.0),
    ]
    assert len(loader.model.calls) == 3


def test_low_confidence_and_looping_segments_are_dropped(episode: str) -> None:
    class ShakyModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "segments": [
                    {"start": 0.5, "end": 1.0, "text": " So", "avg_logprob": -1.9},
                    {
                        "start": 1.0,
                        "end": 1.5,
                        "text": " the the the the",
                        "avg_logprob": -0.2,
                        "compression_ratio": 3.1,
                    },
                    {"start": 1.5, "end": 2.5, "text": " Hi", "avg_logprob": -0.4},
                ]
            }

    primary = [_seg(4.0, 30.0)]
    merged = _filler(StubLoader(ShakyModel())).fill(1, episode, primary)
    assert [s.text for s in merged] == [" Hi", "primary"]


def test_fill_keeps_repeated_ad_lines(tmp_path: Path) -> None:
    """Two reads of the same ad line in one gap are both kept, even when the
    second is merged after the first (later window)."""

    class EchoModel(StubWhisperModel):
        def transcribe(self, audio: np.ndarray, **kwargs: Any) -> dict[str, Any]:
            result = super().transcribe(audio, **kwargs)
            for seg in result["segments"]:
                seg["text"] = " This is your business."
            return result

    # the gap is chunked so the two reads land in different windows
    path = _write_wav(
        tmp_path / "echo.wav", 140.0, [(0, 10), (30, 33), (70, 73), (130, 140)]
    )
    primary = [_seg(0.0, 10.0, " intro"), _seg(130.0, 140.0, " outro")]

    merged = _filler(StubLoader(EchoModel())).fill(1, path, primary)

    assert [s.text for s in merged].count(" This is your business.") == 2


def test_fill_keeps_ad_line_that_repeats_primary_neighbour(tmp_path: Path) -> None:
    """The tagline the primary caught before the gap is read again 15s into
    the gap; only re-reads at the gap edge count as repeats."""
    tagline = " Search Xero with an X."
    path = _write_wav(tmp_path / "tag.wav", 50.0, [(0, 10), (25, 28), (40, 50)])
    primary = [_seg(0.0, 10.0, tagline), _seg(40.0, 50.0, " Welcome back.")]
    loader = StubLoader(StubWhisperModel(text=tagline))

    merged = _filler(loader).fill(1, path, primary)

    assert [(round(s.start, 1), round(s.end, 1), s.text) for s in merged] == [
        (0.0, 10.0, tagline),
        (25.0, 28.0, tagline),
        (40.0, 50.0, " Welcome back."),
    ]


@pytest.mark.parametrize(
    ("text", "no_speech", "logprob", "reason"),
    [
        # junk base.en produced in post 1025's gaps (stats are per decode)
        (" Yes.", 0.21, -0.95, "weak fragment"),
        (" Good.", 0.14, -0.67, "weak fragment"),
        (" F***", 0.58, -0.87, "weak fragment"),
        (" God bless.", 0.04, -0.94, "weak fragment"),
        (" As good as f-", 0.62, -0.71, "likely no speech"),
        (" The", 0.58, -2.20, "low confidence"),
        # real ad lines it recovered there
        (" No, you did.", 0.57, -0.39, None),
        (" Search Zero with an X.", 0.12, -0.29, None),
        (" When human beings try to find...", 0.02, -0.57, None),
        # a short line from a confident decode
        (" Hey Jane.", 0.12, -0.29, None),
    ],
)
def test_rejection_reason_on_real_post_1025_decodes(
    text: str, no_speech: float, logprob: float, reason: str | None
) -> None:
    raw = {
        "text": text,
        "no_speech_prob": no_speech,
        "avg_logprob": logprob,
        "compression_ratio": 1.0,
    }
    assert gap_fill.rejection_reason(raw) == reason


def test_weak_fragment_does_not_shrink_gap_beside_ad(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Post 1025 end: in the 5.04s gap before the BNZ ad, base.en heard only a
    mis-read of the previous word ("Yes.", lp -0.95). Kept, it would leave a
    gap too short for the ad-cut backstop; it is dropped and logged."""

    class TailModel(StubWhisperModel):
        def transcribe(self, audio: np.ndarray, **kwargs: Any) -> dict[str, Any]:
            result = super().transcribe(audio, **kwargs)
            for seg in result["segments"]:
                seg.update(text=" Yes.", avg_logprob=-0.95, no_speech_prob=0.21)
            return result

    path = _write_wav(tmp_path / "tail.wav", 30.0, [(0, 11.0), (15.04, 30.0)])
    primary = [
        _seg(0.0, 10.0, " a crow rubber dog dog bless"),
        _seg(15.04, 30.0, " hey still house hunting next weekend"),
    ]

    with caplog.at_level(logging.INFO, logger="test"):
        merged = _filler(StubLoader(TailModel())).fill(1025, path, primary)

    assert merged == primary
    assert "gap-fill dropped 10.00-11.00 (weak fragment): ' Yes.'" in caplog.text
    assert "added 0 segments, dropped 3 (weak fragment 3)" in caplog.text


def test_failed_window_keeps_other_windows(
    episode: str, caplog: pytest.LogCaptureFixture
) -> None:
    def flaky_audio(path: str, start: float, duration: float) -> np.ndarray:
        if start <= 13.0 < start + duration:
            raise RuntimeError("corrupt frame")
        return gap_fill.load_audio_window(path, start, duration)

    primary = [_seg(4.0, 10.0, "a"), _seg(20.0, 25.0, "b")]
    filler = WhisperGapFiller(
        logging.getLogger("test"),
        GapFillSettings("base.en"),
        model_loader=StubLoader(),
        audio_loader=flaky_audio,
    )

    with caplog.at_level(logging.INFO, logger="test"):
        merged = filler.fill(1, episode, primary)

    recovered = [(round(s.start, 1), round(s.end, 1)) for s in merged if "~" in s.text]
    assert recovered == [(0.5, 2.5), (27.0, 29.0)]
    assert "window 9.0-21.0 failed; keeping speech recovered so far" in caplog.text
    assert "(2 failed)" in caplog.text


def test_recovered_speech_is_clamped_to_its_window(tmp_path: Path) -> None:
    """Whisper pads a short clip to 30s and can time a segment past the clip
    end; at the end of the audio nothing else would trim it."""

    class OverrunModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]:
            seconds = len(audio) / SAMPLE_RATE
            return {"segments": [{"start": seconds - 3.0, "end": 30.0, "text": " Bye"}]}

    path = _write_wav(tmp_path / "end.wav", 30.0, [(0, 10), (27, 30)])
    merged = _filler(StubLoader(OverrunModel())).fill(1, path, [_seg(0.0, 10.0)])

    assert max(s.end for s in merged) == pytest.approx(30.0, abs=0.01)


def test_fill_keeps_primary_when_model_fails(episode: str) -> None:
    def broken(name: str) -> Any:
        raise RuntimeError("no model")

    primary = [_seg(4.0, 10.0, "a")]
    filler = WhisperGapFiller(
        logging.getLogger("test"), GapFillSettings("base.en"), model_loader=broken
    )
    assert filler.fill(1, episode, primary) == primary


def test_hallucinated_symbols_are_dropped(episode: str) -> None:
    class MusicModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> dict[str, Any]:
            return {"segments": [{"start": 0.0, "end": 2.0, "text": " ♪ ... ♪"}]}

    primary = [_seg(0.0, 10.0), _seg(20.0, 30.0)]
    merged = _filler(StubLoader(MusicModel())).fill(1, episode, primary)
    assert merged == primary


# --- settings --------------------------------------------------------------


def test_settings_follow_effective_config() -> None:
    cfg = create_standard_test_config()
    cfg.whisper = LocalWhisperConfig(model="small.en")
    assert gap_fill_settings_from_config(cfg).model_name == "small.en"  # type: ignore[union-attr]

    cfg.whisper = GroqWhisperConfig(api_key="x")
    s = gap_fill_settings_from_config(cfg)
    assert s is not None and s.model_name == "base.en" and s.language == "en"

    cfg.whisper_gap_fill_model = "tiny.en"
    assert gap_fill_settings_from_config(cfg).model_name == "tiny.en"  # type: ignore[union-attr]

    cfg.whisper_gap_fill_enabled = False
    assert gap_fill_settings_from_config(cfg) is None

    cfg.whisper_gap_fill_enabled = True
    cfg.whisper = TestWhisperConfig()
    assert gap_fill_settings_from_config(cfg) is None


def test_env_overrides_reach_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config_store import _apply_gap_fill_env_overrides

    cfg = create_standard_test_config()
    assert cfg.whisper_gap_fill_enabled is True
    monkeypatch.setenv("WHISPER_GAP_FILL_ENABLED", "false")
    monkeypatch.setenv("WHISPER_GAP_FILL_MIN_GAP_SECONDS", "5")
    monkeypatch.setenv("WHISPER_GAP_FILL_PADDING_SECONDS", "0")
    monkeypatch.setenv("WHISPER_GAP_FILL_MAX_WINDOW_SECONDS", "-1")
    monkeypatch.setenv("WHISPER_GAP_FILL_MODEL", "tiny.en")
    _apply_gap_fill_env_overrides(cfg)
    assert cfg.whisper_gap_fill_enabled is False
    assert cfg.whisper_gap_fill_min_gap_seconds == 5.0
    assert cfg.whisper_gap_fill_padding_seconds == 0.0
    assert cfg.whisper_gap_fill_max_window_seconds == 60.0  # invalid, ignored
    assert cfg.whisper_gap_fill_model == "tiny.en"


# --- wiring into TranscriptionManager --------------------------------------


class FixedTranscriber(Transcriber):
    def __init__(self, segments: list[Segment]):
        self.segments = segments

    @property
    def model_name(self) -> str:
        return "local_base.en"

    def transcribe(self, audio_file_path: str) -> list[Segment]:
        del audio_file_path
        return list(self.segments)


@pytest.fixture
def app() -> Generator[Flask, None, None]:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    with app.app_context():
        db.init_app(app)
        db.create_all()
        yield app


def test_manager_stores_recovered_segments_in_time_order(
    app: Flask, episode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default wiring: settings come from config, model via load_whisper_model."""
    loader = StubLoader()
    monkeypatch.setattr(gap_fill, "load_whisper_model", loader)
    cfg = create_standard_test_config()
    cfg.whisper = LocalWhisperConfig(model="base.en")

    with app.app_context():
        feed = Feed(title="F", rss_url="http://example.com/rss.xml")
        post = Post(
            feed=feed,
            guid="g",
            download_url="http://example.com/a.mp3",
            title="P",
            unprocessed_audio_path=episode,
        )
        db.session.add_all([feed, post])
        db.session.commit()

        manager = TranscriptionManager(
            logging.getLogger("test"),
            cfg,
            db_session=db.session,
            transcriber=FixedTranscriber([_seg(20.0, 25.0, "b"), _seg(4.0, 10.0, "a")]),
        )
        stored = manager.transcribe(post)

        assert loader.names == ["base.en"]
        rows = (
            TranscriptSegment.query.filter_by(post_id=post.id)
            .order_by(TranscriptSegment.sequence_num)
            .all()
        )
        assert [r.sequence_num for r in rows] == list(range(5))
        assert [r.start_time for r in rows] == [0.5, 4.0, 13.0, 20.0, 27.0]
        assert [r.text for r in rows][1::2] == ["a", "b"]
        assert len(stored) == 5


def test_manager_skips_gap_fill_when_disabled(
    app: Flask, episode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = StubLoader()
    monkeypatch.setattr(gap_fill, "load_whisper_model", loader)
    cfg = create_standard_test_config()
    cfg.whisper = LocalWhisperConfig(model="base.en")
    cfg.whisper_gap_fill_enabled = False

    with app.app_context():
        feed = Feed(title="F", rss_url="http://example.com/rss.xml")
        post = Post(
            feed=feed,
            guid="g2",
            download_url="http://example.com/b.mp3",
            title="P",
            unprocessed_audio_path=episode,
        )
        db.session.add_all([feed, post])
        db.session.commit()
        manager = TranscriptionManager(
            logging.getLogger("test"),
            cfg,
            db_session=db.session,
            transcriber=FixedTranscriber([_seg(4.0, 10.0, "a")]),
        )
        assert len(manager.transcribe(post)) == 1
        assert loader.names == []
