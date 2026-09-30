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
)
from podcast_processor.experiments.eval_harness import RECOMMENDED_CONFIG
from podcast_processor.experiments.fixtures import (
    cue_sparse_storytelling,
    self_promo_vs_sponsor,
    short_preroll_only,
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
