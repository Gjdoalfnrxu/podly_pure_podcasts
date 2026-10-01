"""Regression gates vs the frozen bow-scout baseline (no API keys)."""

from __future__ import annotations

from pathlib import Path

import pytest

from podcast_processor.experiments.baseline import (
    DEFAULT_GATES,
    compare_to_snapshot,
    extract_snapshot,
    format_gate_failures,
    load_gates,
    load_snapshot,
)
from podcast_processor.experiments.eval_harness import evaluate_all
from podcast_processor.experiments.fixtures import (
    CORPUS_VERSION,
    builder_fixtures,
    corpus_manifest_path,
    load_corpus,
    sha256_file,
)
from podcast_processor.experiments.gemini_confirm import (
    GEMINI_API_KEY_ENV,
    GEMINI_LIVE_ENV,
    GROQ_API_KEY_ENV,
    GROQ_LIVE_ENV,
)
from shared.env import GROQ_KEY_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SUITE_FILES = DEFAULT_GATES["required_production_test_modules"]


@pytest.fixture(autouse=True)
def _no_live_gemini(monkeypatch) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_LIVE_ENV, raising=False)


def test_frozen_corpus_hashes_and_matches_builders() -> None:
    episodes = load_corpus()
    builders = builder_fixtures()
    assert [ep.fixture_id for ep in episodes] == [ep.fixture_id for ep in builders]
    assert len(episodes) >= 9
    for loaded, built in zip(episodes, builders, strict=True):
        assert loaded.fixture_id == built.fixture_id
        assert len(loaded.segments) == len(built.segments)
        assert [(ad.start, ad.end, ad.kind) for ad in loaded.labeled_ads] == [
            (ad.start, ad.end, ad.kind) for ad in built.labeled_ads
        ]
        assert [seg.text for seg in loaded.segments] == [
            seg.text for seg in built.segments
        ]
    manifest = corpus_manifest_path()
    assert manifest.exists()
    assert sha256_file(manifest)


def test_eval_gates_pass_against_committed_snapshot() -> None:
    results = evaluate_all()
    assert results["live_gemini"] is False
    assert results["corpus_version"] == CORPUS_VERSION
    failures = compare_to_snapshot(results)
    assert failures == [], format_gate_failures(failures)


def test_eval_gates_fail_on_recall_drop() -> None:
    results = evaluate_all()
    snapshot = extract_snapshot(results)
    snapshot["macro"]["scout_confirm_mean_time_recall"] = 1.0
    failures = compare_to_snapshot(results, snapshot=snapshot)
    metrics = {item.metric for item in failures}
    assert "scout_confirm_mean_time_recall" in metrics


def test_eval_gates_fail_on_fn_or_residual_rise() -> None:
    results = evaluate_all()
    snapshot = extract_snapshot(results)
    # FN can already be 0.0 after H007; inject a synthetic rise beyond ε.
    results["recommended"]["macro"]["scout_mean_false_negative_rate"] = (
        float(snapshot["macro"]["scout_mean_false_negative_rate"]) + 0.05
    )
    results["recommended"]["macro"]["scout_confirm_mean_residual_strong_cue_rate"] = (
        float(snapshot["macro"]["scout_confirm_mean_residual_strong_cue_rate"]) + 0.05
    )
    failures = compare_to_snapshot(results, snapshot=snapshot)
    metrics = {item.metric for item in failures}
    assert "scout_mean_false_negative_rate" in metrics
    assert "scout_confirm_mean_residual_strong_cue_rate" in metrics


def test_eval_gates_fail_on_token_blowup() -> None:
    results = evaluate_all()
    snapshot = extract_snapshot(results)
    snapshot["macro"]["sum_scout_input_tokens"] = 1
    failures = compare_to_snapshot(results, snapshot=snapshot)
    assert any(item.metric == "sum_scout_input_tokens" for item in failures)


def test_committed_snapshot_and_gates_exist() -> None:
    snapshot = load_snapshot()
    gates = load_gates()
    assert snapshot["n_fixtures"] >= 9
    assert "scout_confirm_mean_time_recall" in snapshot["macro"]
    assert gates["tolerances"]["scout_time_recall_drop"] == 0.02
    assert gates["tolerances"]["scout_false_negative_rate_rise"] == 0.02
    assert gates["tolerances"]["scout_residual_strong_cue_rate_rise"] == 0.01


def test_production_classifier_suites_exist_and_are_nonempty() -> None:
    for rel in PRODUCTION_SUITE_FILES:
        path = REPO_ROOT / rel
        assert path.is_file(), f"missing production suite {rel}"
        text = path.read_text(encoding="utf-8")
        assert "def test_" in text
