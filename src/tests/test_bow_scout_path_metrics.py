"""Unit tests for comparable path metrics and the production-like mock."""

from __future__ import annotations

from podcast_processor.experiments.bow_scout import BowScout, ScoutConfig
from podcast_processor.experiments.eval_harness import RECOMMENDED_CONFIG
from podcast_processor.experiments.fixtures import (
    ad_free_interview,
    chapter_style_ad_break,
    cue_sparse_storytelling,
    stacked_midrolls,
)
from podcast_processor.experiments.metrics import (
    merge_spans,
    path_quality_metrics,
    time_prf,
    windows_to_spans,
)
from podcast_processor.experiments.production_mock import classify_production_like
from podcast_processor.experiments.types import LabeledAd, ScoutSegment


def test_time_prf_empty_is_perfect() -> None:
    scores = time_prf([], [])
    assert scores["precision"] == 1.0
    assert scores["recall"] == 1.0
    assert scores["f1"] == 1.0


def test_time_prf_partial_overlap() -> None:
    scores = time_prf([(0.0, 10.0)], [(5.0, 15.0)])
    assert scores["tp_seconds"] == 5.0
    assert scores["fp_seconds"] == 5.0
    assert scores["fn_seconds"] == 5.0
    assert scores["precision"] == 0.5
    assert scores["recall"] == 0.5


def test_merge_spans_joins_touching() -> None:
    assert merge_spans([(0.0, 5.0), (5.0, 8.0), (20.0, 21.0)]) == [
        (0.0, 8.0),
        (20.0, 21.0),
    ]


def test_production_like_catches_cue_sparse_oracle() -> None:
    episode = cue_sparse_storytelling()
    result = classify_production_like(episode)
    quality = path_quality_metrics(
        labeled_ads=episode.labeled_ads,
        predicted=result.predicted,
        segments=episode.segments,
        source_seconds=float(episode.duration_seconds),
    )
    assert quality["time_recall"] == 1.0
    assert quality["ad_hit_rate"] == 1.0
    scout_windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    assert windows_to_spans(scout_windows) == []


def test_ad_free_has_no_false_windows() -> None:
    episode = ad_free_interview()
    production = classify_production_like(episode)
    assert production.predicted == []
    windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    assert windows == []
    quality = path_quality_metrics(
        labeled_ads=episode.labeled_ads,
        predicted=[],
        segments=episode.segments,
        source_seconds=float(episode.duration_seconds),
    )
    assert quality["time_f1"] == 1.0
    assert quality["false_negative_rate"] == 0.0


def test_chapter_style_needs_scout_extras() -> None:
    episode = chapter_style_ad_break()
    with_extras = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    without = BowScout(
        ScoutConfig(
            threshold=0.5,
            pad_seconds=15.0,
            pad_segments=3,
            include_scout_extras=False,
        )
    ).scout(episode.segments)
    assert with_extras
    assert without == []


def test_stacked_midrolls_merge_into_one_window() -> None:
    episode = stacked_midrolls()
    windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    assert len(windows) == 1
    quality = path_quality_metrics(
        labeled_ads=episode.labeled_ads,
        predicted=windows_to_spans(windows),
        segments=episode.segments,
        source_seconds=float(episode.duration_seconds),
    )
    assert quality["ad_hit_rate"] == 1.0
    assert quality["time_recall"] == 1.0


def test_duration_stub_counts_cuts() -> None:
    segments = [
        ScoutSegment(0, 0.0, 5.0, "Visit example.com now."),
        ScoutSegment(1, 5.0, 10.0, "Normal talk."),
    ]
    quality = path_quality_metrics(
        labeled_ads=[LabeledAd(0.0, 5.0, "preroll")],
        predicted=[(0.0, 5.0)],
        segments=segments,
        source_seconds=10.0,
    )
    assert quality["duration"]["n_cuts"] == 1
    assert quality["duration"]["removed_ms"] == 5000
