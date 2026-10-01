"""Daily hypothesis loop: offline, no production flag, no ledger mutation in --check."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from podcast_processor.experiments.baseline import load_snapshot
from podcast_processor.experiments.bow_scout import ScoutConfig
from podcast_processor.experiments.daily_loop import run_daily_loop
from podcast_processor.experiments.gemini_confirm import (
    GEMINI_API_KEY_ENV,
    GEMINI_LIVE_ENV,
    GROQ_API_KEY_ENV,
    GROQ_LIVE_ENV,
)
from podcast_processor.experiments.hypothesis_ledger import load_ledger, write_ledger
from shared import defaults as DEFAULTS


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
    macro = dict(snapshot["macro"])
    macro["n_fixtures"] = snapshot["n_fixtures"]
    return {
        "corpus_version": snapshot["corpus_version"],
        "corpus_sha256": snapshot["corpus_sha256"],
        "live_gemini": False,
        "live_groq": False,
        "live_llm": False,
        "recommended_config": snapshot["recommended_config"],
        "recommended": {"macro": macro, "per_fixture": per_fixture},
        "sweep": [],
    }


def _fake_evaluate(**kwargs: Any) -> dict[str, Any]:
    payload = _results_from_snapshot()
    sweep = kwargs.get("sweep")
    if sweep:
        rows = []
        for cfg in sweep:
            config = asdict(cfg) if isinstance(cfg, ScoutConfig) else dict(cfg)
            rows.append({"config": config, "macro": payload["recommended"]["macro"]})
        payload["sweep"] = rows
    return payload


def test_daily_loop_check_does_not_mutate_committed_ledger(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_LIVE_ENV, raising=False)
    committed = load_ledger()
    ledger_copy = tmp_path / "ledger"
    write_ledger(committed, ledger_copy)
    before = (ledger_copy / "hypotheses.json").read_text(encoding="utf-8")
    summary = run_daily_loop(
        ledger_root=ledger_copy,
        runs_dir=tmp_path / "runs",
        run_date="2026-09-30",
        offline=True,
        update_ledger=False,
        hypothesis_ids=["H001", "H004"],
        evaluate_fn=_fake_evaluate,
        cache_dir=tmp_path / "cache",
    )
    after = (ledger_copy / "hypotheses.json").read_text(encoding="utf-8")
    assert before == after
    assert summary["offline"] is True
    assert summary["production"]["enable_bow_scout_gemini_confirm"] is False
    assert DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM is False
    assert [row["id"] for row in summary["hypotheses"]] == ["H001", "H004"]
    run_dir = tmp_path / "runs" / "2026-09-30"
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "H001.json").exists()
    assert (run_dir / "H004.json").exists()


def test_daily_loop_updates_last_result_when_requested(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_LIVE_ENV, raising=False)
    ledger_copy = tmp_path / "ledger"
    committed = load_ledger()
    write_ledger(committed, ledger_copy)
    original_h001 = next(row for row in committed.hypotheses if row.id == "H001")
    summary = run_daily_loop(
        ledger_root=ledger_copy,
        runs_dir=tmp_path / "runs",
        run_date="2026-09-30",
        offline=True,
        update_ledger=True,
        hypothesis_ids=["H004"],
        evaluate_fn=_fake_evaluate,
        cache_dir=tmp_path / "cache",
    )
    assert summary["ledger_updated"] is True
    reloaded = load_ledger(ledger_copy)
    item = next(row for row in reloaded.hypotheses if row.id == "H004")
    assert item.last_result is not None
    assert item.last_result.run_date == "2026-09-30"
    assert item.status == "measured"
    h001 = next(row for row in reloaded.hypotheses if row.id == "H001")
    assert h001.status == original_h001.status


def test_daily_loop_clears_live_flags_around_offline_baseline(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "sk-test")
    monkeypatch.setenv(GEMINI_LIVE_ENV, "true")
    monkeypatch.setenv(GROQ_LIVE_ENV, "true")
    seen_live: list[bool] = []

    def tracking_evaluate(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        seen_live.append(
            os.environ.get(GEMINI_LIVE_ENV) == "true"
            or os.environ.get(GROQ_LIVE_ENV) == "true"
        )
        return _fake_evaluate()

    ledger_copy = tmp_path / "ledger"
    write_ledger(load_ledger(), ledger_copy)
    run_daily_loop(
        ledger_root=ledger_copy,
        runs_dir=tmp_path / "runs",
        run_date="2026-09-30",
        offline=False,
        update_ledger=False,
        hypothesis_ids=["H001"],
        evaluate_fn=tracking_evaluate,
        cache_dir=tmp_path / "cache",
    )
    assert seen_live[0] is False
    assert seen_live[1] is True
    assert os.environ.get(GEMINI_LIVE_ENV) == "true"
    assert os.environ.get(GROQ_LIVE_ENV) == "true"


def test_ranked_open_puts_confidence_first() -> None:
    from podcast_processor.experiments.hypothesis_ledger import rank_open_hypotheses

    ledger = load_ledger()
    for item in ledger.hypotheses:
        item.status = "open"
    ranked = rank_open_hypotheses(ledger.hypotheses)
    assert ranked[0].metric_primary == "confidence"
    assert [item.id for item in ranked] == [
        "H001",
        "H004",
        "H005",
        "H006",
        "H002",
        "H007",
        "H003",
        "H008",
    ]


def test_daily_loop_h005_style_golden_promo_records_window_drop(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_LIVE_ENV, raising=False)
    ledger_copy = tmp_path / "ledger"
    write_ledger(load_ledger(), ledger_copy)
    summary = run_daily_loop(
        ledger_root=ledger_copy,
        runs_dir=tmp_path / "runs",
        run_date="2026-10-01",
        offline=True,
        update_ledger=True,
        hypothesis_ids=["H005"],
        evaluate_fn=_fake_evaluate,
        cache_dir=tmp_path / "cache",
    )
    assert summary["hypotheses"][0]["id"] == "H005"
    payload = json.loads(
        (tmp_path / "runs" / "2026-10-01" / "H005.json").read_text(encoding="utf-8")
    )
    assert payload["extras"]["kind"] == "style_golden_promo"
    comparison = payload["extras"]["style_comparison"]
    assert comparison
    assert comparison[0]["fixture_id"] == "soft_skills_style_interview"
    assert comparison[0]["windows_dropped"] >= 1
    assert payload["extras"]["style_confidence_win"] is True
    assert payload["extras"]["production_promo_pattern_unchanged"] is True
    reloaded = load_ledger(ledger_copy)
    item = next(row for row in reloaded.hypotheses if row.id == "H005")
    assert item.status == "accepted"
    assert item.last_result is not None
    assert item.last_result.verdict == "fold_eligible"
