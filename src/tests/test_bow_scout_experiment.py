"""Offline bow-scout + Gemini-confirm experiment (no API keys)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from podcast_processor.experiments.bow_scout import BowScout, ScoutConfig
from podcast_processor.experiments.cost_model import (
    adclassifier_chunk_plan,
    estimate_adclassifier_tokens,
    estimate_scout_confirm_tokens,
    token_reduction_pct,
)
from podcast_processor.experiments.eval_harness import (
    RECOMMENDED_CONFIG,
    ad_coverage,
    ad_hit_rate,
    evaluate_all,
    evaluate_episode,
)
from podcast_processor.experiments.fixtures import (
    DATA_DIR,
    classic_host_reads,
    cue_sparse_storytelling,
    episode_from_json,
    short_preroll_only,
)
from podcast_processor.experiments.gemini_confirm import (
    GEMINI_API_KEY_ENV,
    GEMINI_LIVE_ENV,
    GeminiConfirmClient,
    live_calls_enabled,
)
from podcast_processor.experiments.removal_verifier import (
    ads_to_ms,
    expected_output_duration_ms,
    residual_cue_scan,
)
from podcast_processor.experiments.types import LabeledAd, ScoutSegment, ScoutWindow
from shared import defaults as DEFAULTS

MINI_FIXTURE = DATA_DIR / "mini_preroll.json"


def test_mini_json_fixture_roundtrip() -> None:
    payload = json.loads(MINI_FIXTURE.read_text(encoding="utf-8"))
    episode = episode_from_json(payload)
    assert episode.fixture_id == "mini_preroll"
    assert len(episode.segments) == 8
    assert episode.labeled_ads[0].end == 10.0


def test_scout_flags_and_pads_contiguous_span() -> None:
    episode = episode_from_json(json.loads(MINI_FIXTURE.read_text(encoding="utf-8")))
    scout = BowScout(
        ScoutConfig(
            threshold=0.8,
            pad_seconds=0.0,
            pad_segments=1,
            include_scout_extras=False,
        )
    )
    windows = scout.scout(episode.segments)
    assert len(windows) == 1
    window = windows[0]
    assert window.start_seq == 0
    # pad_segments=1 pulls in the first content segment after the ad
    assert window.end_seq == 2
    assert ad_hit_rate(episode.labeled_ads, windows) == 1.0
    assert ad_coverage(episode.labeled_ads, windows) == 1.0


def test_scout_misses_cue_sparse_ad() -> None:
    episode = cue_sparse_storytelling()
    windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    assert ad_hit_rate(episode.labeled_ads, windows) == 0.0
    assert windows == []


def test_classic_host_reads_localized_with_padding() -> None:
    episode = classic_host_reads()
    windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    assert ad_hit_rate(episode.labeled_ads, windows) == 1.0
    assert ad_coverage(episode.labeled_ads, windows) >= 0.85
    # Three ad blocks should not explode into a full-episode window
    assert len(windows) == 3
    assert sum(w.duration() for w in windows) < episode.duration_seconds * 0.2


def test_gemini_mock_does_not_need_api_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    assert live_calls_enabled() is False

    episode = episode_from_json(json.loads(MINI_FIXTURE.read_text(encoding="utf-8")))
    windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    client = GeminiConfirmClient(
        mock_mode="oracle",
        cache_dir=tmp_path,
        labeled_ads=episode.labeled_ads,
    )
    first = client.confirm_windows(
        windows, episode.podcast_title, episode.podcast_topic
    )
    assert first
    assert first[0].is_ad
    assert first[0].cached is False
    assert first[0].input_tokens > 0
    second = client.confirm_windows(
        windows, episode.podcast_title, episode.podcast_topic
    )
    assert second[0].cached is True
    assert second[0].input_tokens == 0
    assert second[0].prompt_hash == first[0].prompt_hash
    assert (tmp_path / f"{first[0].prompt_hash}.json").exists()


def test_gemini_live_path_uses_injected_completion(monkeypatch) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "sk-test")
    monkeypatch.setenv(GEMINI_LIVE_ENV, "true")
    assert live_calls_enabled() is True

    window = ScoutWindow(
        start_time=0.0,
        end_time=5.0,
        start_seq=0,
        end_seq=0,
        segment_indices=[0],
        peak_score=1.0,
        cue_types=["url"],
        segments=[
            ScoutSegment(0, 0.0, 5.0, "Visit example.com today."),
        ],
    )

    def fake_completion(**kwargs):
        assert kwargs["model"]
        assert kwargs["messages"][0]["role"] == "system"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "is_ad": True,
                                "ad_spans": [
                                    {"start": 0.0, "end": 5.0, "confidence": 0.9}
                                ],
                                "content_type": "promotional_external",
                                "confidence": 0.9,
                            }
                        )
                    )
                )
            ]
        )

    client = GeminiConfirmClient(mock_mode="echo", completion_fn=fake_completion)
    result = client.confirm_window(window, "t", "topic")
    assert result.is_ad
    assert result.ad_spans[0].end == 5.0
    assert "gemini" in result.model or result.model


def test_live_flag_alone_is_not_enough(monkeypatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.setenv(GEMINI_LIVE_ENV, "true")
    assert live_calls_enabled() is False


def test_adclassifier_chunk_plan_matches_no_ad_walk() -> None:
    chunks = adclassifier_chunk_plan(
        120,
        num_segments_per_prompt=60,
        max_overlap_segments=30,
    )
    assert chunks == [(0, 60), (30, 120)]
    assert adclassifier_chunk_plan(0) == []
    assert adclassifier_chunk_plan(60) == [(0, 60)]


def test_scout_tokens_much_smaller_than_full_walk() -> None:
    episode = short_preroll_only()
    windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    full = estimate_adclassifier_tokens(
        episode.segments, episode.podcast_title, episode.podcast_topic
    )
    scout = estimate_scout_confirm_tokens(
        windows, episode.podcast_title, episode.podcast_topic
    )
    assert full.calls >= 1
    assert scout.calls == len(windows)
    assert token_reduction_pct(full, scout) > 50.0

    filled: set[str] = set()
    first = estimate_scout_confirm_tokens(
        windows,
        episode.podcast_title,
        episode.podcast_topic,
        cached_hashes=filled,
    )
    second = estimate_scout_confirm_tokens(
        windows,
        episode.podcast_title,
        episode.podcast_topic,
        cached_hashes=filled,
    )
    assert first.input_tokens > 0
    assert second.input_tokens == 0
    assert second.calls == 0


def test_fade_formula_matches_audio_processor_complex_filter() -> None:
    source = 66_048
    ads = [(3_000, 21_000)]
    fade = 5_000
    account = expected_output_duration_ms(
        source, ads, fade_ms=fade, complex_filter=True
    )
    assert account.expected_output_ms == source - 18_000 + 2 * fade
    simple = expected_output_duration_ms(
        source, ads, fade_ms=fade, complex_filter=False
    )
    assert simple.expected_output_ms == source - 18_000
    assert simple.fade_added_ms == 0


def test_residual_scan_finds_unremoved_url() -> None:
    segments = [
        ScoutSegment(0, 0.0, 5.0, "Visit leftover.com now."),
        ScoutSegment(1, 5.0, 10.0, "Normal conversation about latency."),
    ]
    leftovers = residual_cue_scan(segments, [LabeledAd(5.0, 10.0, "other")])
    assert len(leftovers) == 1
    assert leftovers[0].sequence_num == 0


def test_evaluate_episode_offline_no_keys(monkeypatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    row = evaluate_episode(short_preroll_only(), RECOMMENDED_CONFIG)
    assert row["ad_hit_rate"] == 1.0
    assert row["token_reduction_pct"] > 50.0
    assert row["scout_confirm"]["cached_repeat_input_tokens"] == 0
    assert row["removal"]["n_cuts"] == 1
    assert row["removal"]["fade_added_ms"] == 2 * DEFAULTS.OUTPUT_FADE_MS


def test_evaluate_all_writes_expected_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    results = evaluate_all()
    assert results["live_gemini"] is False
    assert results["recommended"]["macro"]["n_fixtures"] == 6
    assert "mean_token_reduction_pct" in results["recommended"]["macro"]
    assert len(results["sweep"]) >= 3
    from podcast_processor.experiments.eval_harness import write_artifacts

    md_path, json_path = write_artifacts(results, tmp_path)
    assert md_path.exists()
    assert json_path.exists()
    body = md_path.read_text(encoding="utf-8")
    assert "Headline metrics" in body
    assert "ready to PR" in body.lower() or "Ready to PR" in body
    dumped = json.loads(json_path.read_text(encoding="utf-8"))
    assert dumped["recommended"]["macro"]["sum_full_input_tokens"] > 0


def test_ads_to_ms_rounds_labeled_ads() -> None:
    assert ads_to_ms([LabeledAd(1.5, 2.5)]) == [(1500, 2500)]
