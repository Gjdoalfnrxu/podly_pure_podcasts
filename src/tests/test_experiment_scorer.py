"""Confidence-first scorer (offline, uses frozen snapshot macros)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from podcast_processor.experiments.baseline import load_snapshot
from podcast_processor.experiments.scorer import (
    best_survivor,
    rank_candidates,
    ranking_key,
    score_candidate,
)


def _results_from_snapshot() -> dict[str, Any]:
    snapshot = load_snapshot()
    per_fixture = []
    for row in snapshot["per_fixture"]:
        per_fixture.append(
            {
                "fixture_id": row["fixture_id"],
                "paths": {
                    "scout": {
                        "ad_hit_rate": row["scout_ad_hit_rate"],
                        "time_recall": row["scout_time_recall"],
                        "time_precision": row["scout_time_precision"],
                        "time_f1": row["scout_time_f1"],
                        "false_negative_rate": row["scout_false_negative_rate"],
                    },
                    "scout_confirm": {
                        "time_recall": row["scout_confirm_time_recall"],
                        "time_precision": row["scout_confirm_time_precision"],
                        "time_f1": row["scout_confirm_time_f1"],
                        "residual_strong_cues": row[
                            "scout_confirm_residual_strong_cues"
                        ],
                        "residual_strong_cue_rate": row[
                            "scout_confirm_residual_strong_cue_rate"
                        ],
                    },
                    "production_like": {
                        "time_recall": row["production_time_recall"],
                        "time_precision": row["production_time_precision"],
                        "time_f1": row["production_time_f1"],
                    },
                },
                "full_classifier": {"input_tokens": row["full_input_tokens"]},
                "scout_confirm_tokens": {"input_tokens": row["scout_input_tokens"]},
                "token_reduction_pct": row["token_reduction_pct"],
            }
        )
    return {
        "corpus_version": snapshot["corpus_version"],
        "corpus_sha256": snapshot["corpus_sha256"],
        "live_gemini": False,
        "recommended_config": snapshot["recommended_config"],
        "recommended": {
            "macro": {**snapshot["macro"], "n_fixtures": snapshot["n_fixtures"]},
            "per_fixture": per_fixture,
        },
    }


def test_scorer_rejects_regression_then_prefers_f1_then_tokens() -> None:
    results = _results_from_snapshot()
    snapshot = load_snapshot()
    current = score_candidate("current", results, snapshot=snapshot)
    assert current.rejected is False

    worse = deepcopy(results)
    worse["recommended"]["macro"]["scout_confirm_mean_time_recall"] = (
        snapshot["macro"]["scout_confirm_mean_time_recall"] - 0.2
    )
    rejected = score_candidate("worse-recall", worse, snapshot=snapshot)
    assert rejected.rejected is True

    cheaper = deepcopy(results)
    cheaper["recommended"]["macro"]["mean_token_reduction_pct"] = (
        current.token_reduction_pct + 1.0
    )
    cheaper["recommended"]["macro"]["sum_scout_input_tokens"] = max(
        0.0, current.scout_input_tokens - 50
    )
    better_cost = score_candidate("cheaper", cheaper, snapshot=snapshot)

    higher_f1 = deepcopy(results)
    higher_f1["recommended"]["macro"]["scout_confirm_mean_time_f1"] = min(
        1.0, current.detection_f1 + 0.01
    )
    better_det = score_candidate("higher-f1", higher_f1, snapshot=snapshot)

    ranked = rank_candidates([rejected, better_cost, better_det, current])
    assert ranked[0].name == "higher-f1"
    assert ranked[1].name == "cheaper"
    assert ranked[2].name == "current"
    assert ranked[-1].name == "worse-recall"
    winner = best_survivor(ranked)
    assert winner is not None
    assert winner.name == "higher-f1"


def test_ranking_key_orders_detection_before_cost() -> None:
    snap = load_snapshot()["macro"]
    low_f1 = dict(snap)
    low_f1["scout_confirm_mean_time_f1"] = 0.1
    low_f1["mean_token_reduction_pct"] = 99.0
    high_f1 = dict(snap)
    high_f1["scout_confirm_mean_time_f1"] = 0.9
    high_f1["mean_token_reduction_pct"] = 10.0
    assert ranking_key(high_f1) < ranking_key(low_f1)
