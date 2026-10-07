"""Ad cuts must cover untranscribed audio that borders an ad.

Drives the real AudioProcessor.process_audio -> get_ad_segments -> AdMerger ->
refined-boundaries -> merge_ad_segments path against an in-memory DB; only the
audio I/O (duration probe, ffmpeg clip) is patched.
"""

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from app.extensions import db
from app.models import Feed, Identification, ModelCall, Post, TranscriptSegment
from podcast_processor.audio_processor import AudioProcessor
from podcast_processor.untranscribed_gaps import (
    MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS,
    MAX_INTERIOR_UNTRANSCRIBED_GAP_SECONDS,
)
from shared.config import Config

POST_1025_FIXTURE = (
    Path(__file__).parent / "data" / "post_1025_untranscribed_ad_gap.json"
)

# (start, end, label) where label is "ad", "content" (LLM said non-ad) or None.
Seg = tuple[float, float, str | None]


def _seed_post(
    segments: list[Seg],
    *,
    texts: list[str] | None = None,
    ad_confidences: list[float | None] | None = None,
    refined: list[dict[str, float]] | None = None,
) -> Post:
    feed = Feed(title="Feed", rss_url="http://example.com/rss.xml")
    db.session.add(feed)
    db.session.commit()
    post = Post(
        feed_id=feed.id,
        title="Post",
        guid="gap-guid",
        download_url="http://example.com/a.mp3",
        unprocessed_audio_path="in.mp3",
        refined_ad_boundaries=refined,
    )
    db.session.add(post)
    db.session.commit()
    call = ModelCall(
        post_id=post.id,
        first_segment_sequence_num=0,
        last_segment_sequence_num=len(segments) - 1,
        model_name="test-model",
        prompt="p",
        status="success",
    )
    db.session.add(call)
    db.session.commit()
    for seq, (start, end, label) in enumerate(segments):
        seg = TranscriptSegment(
            post_id=post.id,
            sequence_num=seq,
            start_time=start,
            end_time=end,
            text=texts[seq] if texts else f"segment {seq}",
        )
        db.session.add(seg)
        db.session.flush()
        if label is not None:
            confidence = ad_confidences[seq] if ad_confidences else None
            db.session.add(
                Identification(
                    transcript_segment_id=seg.id,
                    model_call_id=call.id,
                    label=label,
                    confidence=confidence if confidence is not None else 0.95,
                )
            )
    db.session.commit()
    return post


def _cut_windows(
    config: Config,
    post: Post,
    duration_s: float,
    *,
    separation: int = 60,
    duration_ms: int | None = None,
) -> list[tuple[int, int]]:
    config.output.min_ad_segement_separation_seconds = separation
    if duration_ms is None:
        duration_ms = round(duration_s * 1000)
    processor = AudioProcessor(
        config=config, logger=logging.getLogger("test"), db_session=db.session
    )
    with (
        patch(
            "podcast_processor.audio_processor.get_audio_duration_ms",
            side_effect=[duration_ms, 1000],
        ),
        patch("podcast_processor.audio_processor.clip_segments_with_fade") as clip,
    ):
        removed = processor.process_audio(post, "out.mp3")
    clipped: list[tuple[int, int]] = clip.call_args.kwargs["ad_segments_ms"]
    assert clipped == removed
    return clipped


def _post_1025_fixture() -> dict[str, Any]:
    return json.loads(POST_1025_FIXTURE.read_text())


@pytest.mark.parametrize(
    ("refinement", "expected"),
    [
        # Live config: refined end 2455.8 is kept, start widens over the gap.
        (True, (2292500, 2455800)),
        (False, (2292500, 2458400)),
    ],
)
def test_post_1025_cut_starts_at_untranscribed_gap(
    app: Flask, test_config: Config, refinement: bool, expected: tuple[int, int]
) -> None:
    """Segment 603 ends 2292.5, ad segment 604 starts 2322.5; the 30s between
    is untranscribed ad audio (New World + start of Xero) and must be cut."""
    data = _post_1025_fixture()
    rows = data["segments"]
    with app.app_context():
        test_config.enable_boundary_refinement = refinement
        post = _seed_post(
            [
                (r["start"], r["end"], "ad" if r["ad_confidence"] else None)
                for r in rows
            ],
            texts=[r["text"] for r in rows],
            ad_confidences=[r["ad_confidence"] for r in rows],
            refined=data["refined_ad_boundaries"],
        )
        assert _cut_windows(test_config, post, duration_s=2700.0) == [expected]


def test_gap_bordered_by_non_ad_speech_is_untouched(
    app: Flask, test_config: Config
) -> None:
    """Gaps whose neighbours are transcribed non-ad speech stay in the output,
    even when an ad sits right next to that speech."""
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (120.0, 125.0, "content"),  # 20s gap before it, content both sides
                (125.0, 145.0, "ad"),
                (145.0, 165.0, "ad"),
                (165.0, 168.0, "content"),  # ad ends into speech, not into the gap
                (190.0, 400.0, "content"),  # 22s gap before it, content both sides
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [(125000, 165000)]


def test_gap_shorter_than_threshold_is_untouched(
    app: Flask, test_config: Config
) -> None:
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (104.5, 125.0, "ad"),  # 4.5s gap, below the 5s threshold
                (125.0, 145.0, "ad"),
                (145.0, 400.0, "content"),
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [(104500, 145000)]


def test_gap_at_threshold_is_extended(app: Flask, test_config: Config) -> None:
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (105.0, 125.0, "ad"),  # exactly 5s gap
                (125.0, 145.0, "ad"),
                (145.0, 400.0, "content"),
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [(100000, 145000)]


def test_gap_after_ad_extends_forward_to_next_segment(
    app: Flask, test_config: Config
) -> None:
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (100.0, 120.0, "ad"),
                (120.0, 140.0, "ad"),
                (152.0, 400.0, "content"),  # 12s untranscribed after the ad
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [(100000, 152000)]


def test_untranscribed_tail_after_last_ad_is_cut_to_end_of_audio(
    app: Flask, test_config: Config
) -> None:
    """Separation is set below the tail length so the pre-existing
    "near the end" extension cannot be what covers it."""
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 300.0, "content"),
                (300.0, 320.0, "ad"),
                (320.0, 340.0, "ad"),
            ]
        )
        assert _cut_windows(test_config, post, duration_s=370.0, separation=10) == [
            (300000, 370000)
        ]


def test_untranscribed_head_before_first_ad_is_cut_from_zero(
    app: Flask, test_config: Config
) -> None:
    with app.app_context():
        post = _seed_post(
            [
                (12.0, 30.0, "ad"),  # nothing transcribed before 12s
                (30.0, 45.0, "ad"),
                (45.0, 400.0, "content"),
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [(0, 45000)]


@pytest.mark.parametrize(
    ("refined_start", "refined_end", "expected"),
    [
        (133.0, 160.0, (133000, 190000)),  # start moved in; end still extends
        (130.0, 157.0, (100000, 157000)),  # end moved in; start still extends
    ],
)
def test_refined_edge_moved_inside_group_blocks_gap_extension(
    app: Flask,
    test_config: Config,
    refined_start: float,
    refined_end: float,
    expected: tuple[int, int],
) -> None:
    """Refinement put the ad edge inside the first/last ad segment (that part
    is content), so the gap beyond it borders content and stays."""
    with app.app_context():
        test_config.enable_boundary_refinement = True
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (130.0, 145.0, "ad"),  # 30s untranscribed before
                (145.0, 160.0, "ad"),
                (190.0, 400.0, "content"),  # 30s untranscribed after
            ],
            refined=[
                {
                    "orig_start": 130.0,
                    "orig_end": 160.0,
                    "refined_start": refined_start,
                    "refined_end": refined_end,
                }
            ],
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [expected]


def test_extension_does_not_let_separation_merge_swallow_speech(
    app: Flask, test_config: Config
) -> None:
    """The 60s separation merge must judge the transcribed ad windows. Judged
    on extended windows, ad 100-130 (+50s gap to 180) is within 60s of ad
    200-230 and the transcribed speech at 180-200 would be cut."""
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (100.0, 130.0, "ad"),
                (180.0, 200.0, "content"),  # 50s untranscribed before it
                (200.0, 230.0, "ad"),
                (230.0, 400.0, "content"),
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0) == [
            (100000, 180000),
            (200000, 230000),
        ]


def test_short_ad_group_is_dropped_before_extension(
    app: Flask, test_config: Config
) -> None:
    """An 8s ad group is below the 14s minimum; a 20s gap after it must not
    lift it over the minimum."""
    with app.app_context():
        test_config.output.min_ad_segment_length_seconds = 14
        post = _seed_post(
            [
                (0.0, 500.0, "content"),
                (500.0, 508.0, "ad"),
                (528.0, 1000.0, "content"),  # 20s untranscribed before it
            ]
        )
        assert _cut_windows(test_config, post, duration_s=1000.0) == []


def test_groups_bordering_the_same_gap_coalesce(
    app: Flask, test_config: Config
) -> None:
    """Separation 10 keeps the two transcribed groups apart; both extend over
    the 40s gap between them, so the cut is one window."""
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 100.0, "content"),
                (100.0, 130.0, "ad"),
                (170.0, 200.0, "ad"),  # 40s untranscribed between the groups
                (200.0, 400.0, "content"),
            ]
        )
        assert _cut_windows(test_config, post, duration_s=400.0, separation=10) == [
            (100000, 200000)
        ]


def _interior_gap_post(gap: float) -> Post:
    return _seed_post(
        [
            (0.0, 100.0, "content"),
            (100.0, 130.0, "ad"),
            (130.0 + gap, 1000.0, "content"),
        ]
    )


@pytest.mark.parametrize(
    ("gap", "extends"),
    [
        (MAX_INTERIOR_UNTRANSCRIBED_GAP_SECONDS - 0.1, True),
        (MAX_INTERIOR_UNTRANSCRIBED_GAP_SECONDS + 0.1, False),
    ],
)
def test_interior_gap_cap(
    app: Flask,
    test_config: Config,
    caplog: pytest.LogCaptureFixture,
    gap: float,
    extends: bool,
) -> None:
    assert MAX_INTERIOR_UNTRANSCRIBED_GAP_SECONDS == 60.0
    with app.app_context():
        post = _interior_gap_post(gap)
        with caplog.at_level(logging.WARNING, logger="test"):
            cut = _cut_windows(test_config, post, duration_s=1000.0)
        end = round((130.0 + gap) * 1000) if extends else 130000
        assert cut == [(100000, end)]
        warned = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        if extends:
            assert warned == []
        else:
            assert warned == [
                f"Post {post.id}: untranscribed gap 130.0-{130.0 + gap:.1f} "
                f"({gap:.1f}s) beside an ad exceeds the 60s cap; "
                "not extending the cut over it"
            ]


@pytest.mark.parametrize(
    ("gap", "extends"),
    [
        (MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS - 0.1, True),
        (MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS + 0.1, False),
    ],
)
def test_head_gap_cap(
    app: Flask,
    test_config: Config,
    caplog: pytest.LogCaptureFixture,
    gap: float,
    extends: bool,
) -> None:
    assert MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS == 180.0
    with app.app_context():
        post = _seed_post(
            [
                (gap, gap + 30.0, "ad"),  # nothing transcribed before it
                (gap + 30.0, 1000.0, "content"),
            ]
        )
        with caplog.at_level(logging.WARNING, logger="test"):
            cut = _cut_windows(test_config, post, duration_s=1000.0)
        start = 0 if extends else round(gap * 1000)
        assert cut == [(start, round((gap + 30.0) * 1000))]
        assert (
            any(
                r.levelno == logging.WARNING and f"Post {post.id}:" in r.getMessage()
                for r in caplog.records
            )
            is not extends
        )


@pytest.mark.parametrize(
    ("gap", "extends"),
    [
        (MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS - 0.1, True),
        (MAX_EDGE_UNTRANSCRIBED_GAP_SECONDS + 0.1, False),
    ],
)
def test_tail_gap_cap(
    app: Flask,
    test_config: Config,
    caplog: pytest.LogCaptureFixture,
    gap: float,
    extends: bool,
) -> None:
    with app.app_context():
        post = _seed_post(
            [
                (0.0, 300.0, "content"),
                (300.0, 330.0, "ad"),  # nothing transcribed after it
            ]
        )
        duration = 330.0 + gap
        with caplog.at_level(logging.WARNING, logger="test"):
            cut = _cut_windows(test_config, post, duration_s=duration)
        end = round(duration * 1000) if extends else 330000
        assert cut == [(300000, end)]
        assert (
            any(
                r.levelno == logging.WARNING and f"Post {post.id}:" in r.getMessage()
                for r in caplog.records
            )
            is not extends
        )


def test_tail_cut_reaches_exact_duration(app: Flask, test_config: Config) -> None:
    """512002 / 1000 * 1000 truncates to 512001 with int(); the cut must end
    exactly at the source duration (no 1 ms keep segment)."""
    with app.app_context():
        post = _seed_post([(0.0, 300.0, "content"), (300.0, 340.0, "ad")])
        assert _cut_windows(
            test_config, post, duration_s=512.002, duration_ms=512002
        ) == [(300000, 512002)]
