"""Experiment-only candidate detectors; production CueDetector unchanged."""

from __future__ import annotations

from podcast_processor.cue_detector import CueDetector
from podcast_processor.experiments.bow_scout import BowScout, overlap_seconds
from podcast_processor.experiments.candidates import (
    STORYTELLING_HOST_READ,
    TIGHT_PROMO_PATTERN,
    StorytellingScoutDetector,
    TightPromoCueDetector,
    cheap_midroll_probe,
    duration_gated_midroll_probe,
    wider_duration_gated_midroll_probe,
)
from podcast_processor.experiments.eval_harness import RECOMMENDED_CONFIG
from podcast_processor.experiments.fixtures import (
    ad_free_interview,
    cue_sparse_storytelling,
    news_briefing_style_code_cta,
    self_promo_vs_sponsor,
    short_preroll_only,
    soft_skills_style_interview,
    style_golden_fixtures,
)


def test_production_promo_still_flags_code_word() -> None:
    production = CueDetector()
    assert production.promo_pattern.search("the code review found a race")
    assert production.promo_pattern.search("use code SAVE20")
    assert TIGHT_PROMO_PATTERN.pattern != production.promo_pattern.pattern


def test_tight_promo_drops_tech_speech_keeps_use_code() -> None:
    candidate = TightPromoCueDetector()
    assert not candidate.promo_pattern.search("the code review found a race")
    assert not candidate.promo_pattern.search("the code path is hot")
    assert candidate.promo_pattern.search("use code SAVE20")
    assert candidate.promo_pattern.search("Use promo NOTION")
    episode = short_preroll_only()
    windows = BowScout(RECOMMENDED_CONFIG, detector=candidate).scout(episode.segments)
    assert windows
    sponsor = self_promo_vs_sponsor()
    sponsor_windows = BowScout(RECOMMENDED_CONFIG, detector=candidate).scout(
        sponsor.segments
    )
    assert sponsor_windows


def test_storytelling_recovers_cue_sparse_without_production_change() -> None:
    episode = cue_sparse_storytelling()
    production = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    assert production == []
    joined = " ".join(seg.text for seg in episode.segments)
    assert STORYTELLING_HOST_READ.search(joined)
    detector = StorytellingScoutDetector(include_scout_extras=True)
    windows = BowScout(RECOMMENDED_CONFIG, detector=detector).scout(episode.segments)
    assert windows
    ad = episode.labeled_ads[0]
    assert any(
        overlap_seconds(w.start_time, w.end_time, ad.start, ad.end) > 0.5
        for w in windows
    )


def test_cheap_midroll_probe_covers_cue_sparse_ad() -> None:
    episode = cue_sparse_storytelling()
    probed = cheap_midroll_probe(episode, [], RECOMMENDED_CONFIG)
    assert len(probed) == 1
    window = probed[0]
    assert window.end_time > 480.0
    assert window.start_time < 515.0
    # Do not add a second probe when scout already fired.
    assert cheap_midroll_probe(episode, probed, RECOMMENDED_CONFIG) == probed


def test_duration_gated_probe_recovers_cue_sparse_skips_ad_free() -> None:
    sparse = cue_sparse_storytelling()
    recovered = duration_gated_midroll_probe(sparse, [], RECOMMENDED_CONFIG)
    assert len(recovered) == 1
    ad = sparse.labeled_ads[0]
    assert (
        overlap_seconds(
            recovered[0].start_time, recovered[0].end_time, ad.start, ad.end
        )
        > 0.5
    )
    short = ad_free_interview()
    assert short.duration_seconds < 900
    assert duration_gated_midroll_probe(short, [], RECOMMENDED_CONFIG) == []
    already = duration_gated_midroll_probe(sparse, recovered, RECOMMENDED_CONFIG)
    assert already == recovered


def test_wider_duration_gated_probe_covers_full_cue_sparse_ad() -> None:
    sparse = cue_sparse_storytelling()
    recovered = wider_duration_gated_midroll_probe(sparse, [], RECOMMENDED_CONFIG)
    assert len(recovered) == 1
    ad = sparse.labeled_ads[0]
    assert recovered[0].start_time <= ad.start + 1e-9
    assert recovered[0].end_time >= ad.end - 1e-9
    assert duration_gated_midroll_probe(sparse, [], RECOMMENDED_CONFIG)[0].end_time < (
        ad.end - 1e-9
    )
    short = ad_free_interview()
    assert wider_duration_gated_midroll_probe(short, [], RECOMMENDED_CONFIG) == []


def test_evaluate_all_default_applies_duration_gated_probe() -> None:
    from podcast_processor.experiments.eval_harness import evaluate_all

    results = evaluate_all(sweep=[])
    by_id = {row["fixture_id"]: row for row in results["recommended"]["per_fixture"]}
    sparse = by_id["cue_sparse_storytelling"]
    assert sparse["ad_hit_rate"] == 1.0
    assert sparse["n_windows"] == 1
    free = by_id["ad_free_interview"]
    assert free["n_windows"] == 0
    assert ad_free_interview().duration_seconds < 900
    assert results["recommended"]["macro"]["scout_mean_ad_hit_rate"] == 1.0


def test_soft_skills_style_tight_promo_drops_tech_speech_keeps_use_code() -> None:
    episode = soft_skills_style_interview()
    production = CueDetector(include_scout_extras=True)
    tight = TightPromoCueDetector(include_scout_extras=True)
    joined_tech = "During the code review the team found a race."
    assert production.promo_pattern.search(joined_tech)
    assert not tight.promo_pattern.search(joined_tech)
    assert tight.promo_pattern.search("use code SOFT20")
    rec_windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    tight_windows = BowScout(RECOMMENDED_CONFIG, detector=tight).scout(episode.segments)
    assert len(tight_windows) < len(rec_windows)
    ad = episode.labeled_ads[0]
    assert any(
        overlap_seconds(w.start_time, w.end_time, ad.start, ad.end) > 0.5
        for w in rec_windows
    )
    assert any(
        overlap_seconds(w.start_time, w.end_time, ad.start, ad.end) > 0.5
        for w in tight_windows
    )


def test_news_briefing_style_has_code_speech_and_save_cta() -> None:
    episode = news_briefing_style_code_cta()
    texts = " ".join(seg.text for seg in episode.segments)
    assert "code review" in texts
    assert "use code SAVE50" in texts
    tight = TightPromoCueDetector(include_scout_extras=True)
    rec_windows = BowScout(RECOMMENDED_CONFIG).scout(episode.segments)
    tight_windows = BowScout(RECOMMENDED_CONFIG, detector=tight).scout(episode.segments)
    assert len(tight_windows) < len(rec_windows)
    ad = episode.labeled_ads[0]
    assert any(
        overlap_seconds(w.start_time, w.end_time, ad.start, ad.end) > 0.5
        for w in tight_windows
    )


def test_style_golden_fixtures_builders_after_h006_promotion() -> None:
    from podcast_processor.experiments.fixtures import builder_fixtures

    builder_ids = {episode.fixture_id for episode in builder_fixtures()}
    assert "soft_skills_style_interview" in builder_ids
    assert "news_briefing_style_code_cta" in builder_ids
    assert style_golden_fixtures(["news_briefing_style_code_cta"])[0].fixture_id == (
        "news_briefing_style_code_cta"
    )
