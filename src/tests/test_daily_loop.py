"""Daily hypothesis loop: offline, no production flag, no ledger mutation in --check."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

import pytest

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
from shared.env import GROQ_KEY_ENV


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
    monkeypatch.delenv(GROQ_KEY_ENV, raising=False)
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
    monkeypatch.delenv(GROQ_KEY_ENV, raising=False)
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
        "H010",
        "H013",
        "H014",
        "H002",
        "H007",
        "H009",
        "H003",
        "H008",
        "H011",
        "H012",
    ]


def test_daily_loop_h006_style_golden_promo_records_window_drop(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_LIVE_ENV, raising=False)
    ledger_copy = tmp_path / "ledger"
    write_ledger(load_ledger(), ledger_copy)
    summary = run_daily_loop(
        ledger_root=ledger_copy,
        runs_dir=tmp_path / "runs",
        run_date="2026-10-01",
        offline=True,
        update_ledger=True,
        hypothesis_ids=["H006"],
        evaluate_fn=_fake_evaluate,
        cache_dir=tmp_path / "cache",
    )
    assert summary["hypotheses"][0]["id"] == "H006"
    payload = json.loads(
        (tmp_path / "runs" / "2026-10-01" / "H006.json").read_text(encoding="utf-8")
    )
    assert payload["extras"]["kind"] == "style_golden_promo"
    comparison = payload["extras"]["style_comparison"]
    assert comparison
    assert comparison[0]["fixture_id"] == "news_briefing_style_code_cta"
    assert comparison[0]["windows_dropped"] >= 1
    assert payload["extras"]["style_confidence_win"] is True
    assert payload["extras"]["production_promo_pattern_unchanged"] is True
    reloaded = load_ledger(ledger_copy)
    item = next(row for row in reloaded.hypotheses if row.id == "H006")
    assert item.status == "accepted"
    assert item.last_result is not None
    assert item.last_result.verdict == "fold_eligible"


def test_daily_loop_h005_style_golden_promo_records_window_drop(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GEMINI_LIVE_ENV, raising=False)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_KEY_ENV, raising=False)
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


def test_live_daily_loop_records_gemini_spend_and_stops_at_budget(
    monkeypatch, tmp_path
) -> None:
    """Billable Gemini confirms must increment spend.json and halt at $cap."""
    import sys
    import types
    from types import SimpleNamespace

    from podcast_processor.experiments.budget import DailyBudget, estimate_usd
    from podcast_processor.experiments.gemini_confirm import (
        CONFIRM_SYSTEM_PROMPT,
        DEFAULT_GEMINI_MODEL,
        PRE_CALL_OUTPUT_TOKEN_ESTIMATE,
        GeminiConfirmClient,
        estimate_message_tokens,
        live_calls_enabled,
        window_user_prompt,
    )
    from podcast_processor.experiments.types import ScoutSegment, ScoutWindow

    monkeypatch.setenv(GEMINI_API_KEY_ENV, "sk-test")
    monkeypatch.setenv(GEMINI_LIVE_ENV, "true")
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_KEY_ENV, raising=False)
    monkeypatch.delenv(GROQ_LIVE_ENV, raising=False)

    litellm_calls: list[dict[str, object]] = []

    def fake_completion(**kwargs: object) -> SimpleNamespace:
        litellm_calls.append(dict(kwargs))
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

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.completion = fake_completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    def make_window(tag: str) -> ScoutWindow:
        return ScoutWindow(
            start_time=0.0,
            end_time=5.0,
            start_seq=0,
            end_seq=0,
            segment_indices=[0],
            peak_score=1.0,
            cue_types=["url"],
            segments=[
                ScoutSegment(0, 0.0, 5.0, f"Visit example-{tag}.com today."),
            ],
        )

    probe = make_window("1")
    probe_messages = [
        {"role": "system", "content": CONFIRM_SYSTEM_PROMPT},
        {"role": "user", "content": window_user_prompt(probe, "t", "topic")},
    ]
    planned = estimate_usd(
        estimate_message_tokens(probe_messages),
        PRE_CALL_OUTPUT_TOKEN_ESTIMATE,
        provider="gemini",
        model=DEFAULT_GEMINI_MODEL,
    )
    budget = DailyBudget(limit_usd=planned * 1.01)
    live_eval_calls = {"n": 0}

    def spending_evaluate(**kwargs: object) -> dict[str, object]:
        if live_calls_enabled():
            live_eval_calls["n"] += 1
            client = GeminiConfirmClient(
                cache_dir=kwargs.get("cache_dir"),  # type: ignore[arg-type]
                budget=kwargs.get("budget"),  # type: ignore[arg-type]
            )
            client.confirm_window(make_window(str(live_eval_calls["n"])), "t", "topic")
        return _fake_evaluate(**kwargs)

    ledger_copy = tmp_path / "ledger"
    write_ledger(load_ledger(), ledger_copy)
    summary = run_daily_loop(
        ledger_root=ledger_copy,
        runs_dir=tmp_path / "runs",
        run_date="2026-10-05",
        offline=False,
        update_ledger=False,
        hypothesis_ids=["H001", "H010"],
        evaluate_fn=spending_evaluate,
        budget=budget,
        cache_dir=tmp_path / "cache",
    )

    assert DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM is False
    assert summary["production"]["enable_bow_scout_gemini_confirm"] is False
    assert budget.live_calls == 1
    assert budget.spent_usd > 0
    assert budget.skipped_live_calls >= 1
    assert len(litellm_calls) == 1
    assert summary["spent_usd"] == pytest.approx(budget.spent_usd)
    assert summary["live_blocked"] == "budget_exhausted"
    assert [row["id"] for row in summary["hypotheses"]] == ["H001"]
    spend_payload = json.loads(
        (tmp_path / "runs" / "2026-10-05" / "spend.json").read_text(encoding="utf-8")
    )
    assert spend_payload["live_calls"] == 1
    assert spend_payload["spent_usd"] == pytest.approx(budget.spent_usd)
    two_five = estimate_usd(
        estimate_message_tokens(probe_messages),
        PRE_CALL_OUTPUT_TOKEN_ESTIMATE,
        provider="gemini",
        model="gemini/gemini-2.5-flash",
    )
    assert planned != pytest.approx(two_five)
