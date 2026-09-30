"""Daily hypothesis → experiment → gate loop (offline by default).

Does not change production AdClassifier, CueDetector defaults, Feed strategy,
or frozen gates.json. Live Groq/Gemini require env flags and PODLY_DAILY_BUDGET.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from podcast_processor.experiments.baseline import (
    compare_to_snapshot,
    format_gate_failures,
)
from podcast_processor.experiments.bow_scout import ScoutConfig
from podcast_processor.experiments.budget import (
    DailyBudget,
    daily_budget_usd,
    default_cache_dir,
    write_budget,
)
from podcast_processor.experiments.candidates import (
    StorytellingScoutDetector,
    TightPromoCueDetector,
    cheap_midroll_probe,
)
from podcast_processor.experiments.eval_harness import (
    DEFAULT_SWEEP,
    RECOMMENDED_CONFIG,
    evaluate_all,
)
from podcast_processor.experiments.gemini_confirm import (
    any_live_confirm_enabled,
    default_live_confirm_model,
    groq_live_calls_enabled,
    live_calls_enabled,
)
from podcast_processor.experiments.golden_ingest import (
    example_soft_skills_style_payload,
    example_the_daily_style_payload,
    validate_golden_payload,
)
from podcast_processor.experiments.hypothesis_ledger import (
    Hypothesis,
    LastResult,
    Ledger,
    load_ledger,
    rank_open_hypotheses,
    update_hypothesis,
    write_ledger,
)
from podcast_processor.experiments.scorer import (
    RankedCandidate,
    format_ranking,
    rank_candidates,
    score_candidate,
)
from shared import defaults as DEFAULTS

DEFAULT_RUNS_DIR = Path("docs/experiments/runs")
EvaluateFn = Callable[..., dict[str, Any]]


def _utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


def results_with_macro(
    base: dict[str, Any], config: dict[str, Any], macro: dict[str, Any]
) -> dict[str, Any]:
    """Wrap a sweep macro so compare_to_snapshot can score it.

    Gates read n_fixtures + macro only. per_fixture is copied from the
    recommended eval so extract_snapshot can run.
    """
    return {
        "corpus_version": base.get("corpus_version", "v1"),
        "corpus_sha256": base.get("corpus_sha256"),
        "live_gemini": bool(base.get("live_gemini", False)),
        "recommended_config": config,
        "recommended": {
            "macro": macro,
            "per_fixture": base["recommended"]["per_fixture"],
        },
    }


def _verdict_for_candidate(
    scored: RankedCandidate,
    *,
    improved_primary: bool,
    process_only: bool = False,
) -> tuple[str, str]:
    """Return (status, verdict). Never accepted if gates failed."""
    if process_only:
        return ("measured", "process_ok" if not scored.rejected else "process_failed")
    if scored.rejected:
        return ("rejected", "failed_gates")
    if improved_primary:
        return ("accepted", "fold_eligible")
    return ("measured", "no_win")


def _improved_detection(
    scored: RankedCandidate, baseline_macro: dict[str, Any]
) -> bool:
    return (
        scored.detection_f1
        > float(baseline_macro["scout_confirm_mean_time_f1"]) + 1e-12
        or scored.detection_recall
        > float(baseline_macro["scout_confirm_mean_time_recall"]) + 1e-12
        or scored.ad_hit_rate > float(baseline_macro["scout_mean_ad_hit_rate"]) + 1e-12
    )


def _improved_cost(scored: RankedCandidate, baseline_macro: dict[str, Any]) -> bool:
    return (
        scored.token_reduction_pct
        > float(baseline_macro["mean_token_reduction_pct"]) + 1e-12
        or scored.scout_input_tokens
        < float(baseline_macro["sum_scout_input_tokens"]) - 1e-12
    )


def _improved_confidence(
    scored: RankedCandidate, baseline_macro: dict[str, Any]
) -> bool:
    # Residual strong-cue rate is the confidence proxy already gated; a drop
    # plus no gate failure is a confidence win.
    actual = float(scored.macro["scout_confirm_mean_residual_strong_cue_rate"])
    base = float(baseline_macro["scout_confirm_mean_residual_strong_cue_rate"])
    precision = float(scored.macro["scout_confirm_mean_time_precision"])
    base_p = float(baseline_macro["scout_confirm_mean_time_precision"])
    return actual < base - 1e-12 or precision > base_p + 1e-12


def _improved_for_primary(
    primary: str, scored: RankedCandidate, baseline_macro: dict[str, Any]
) -> bool:
    if scored.rejected:
        return False
    if primary == "detection":
        return _improved_detection(scored, baseline_macro)
    if primary == "cost":
        return _improved_cost(scored, baseline_macro) and not scored.rejected
    return _improved_confidence(scored, baseline_macro)


def _live_provider(offline: bool) -> str:
    if offline:
        return "mock"
    if live_calls_enabled():
        return "gemini"
    if groq_live_calls_enabled():
        return "groq"
    return "mock"


def _eval_kwargs(
    *,
    offline: bool,
    cache_dir: Path,
    detector: Any | None = None,
    window_postprocess: Any | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "sweep": [],
        "cache_dir": cache_dir,
        "detector": detector,
        "window_postprocess": window_postprocess,
        "confirm_mock_mode": "oracle",
    }
    if not offline and any_live_confirm_enabled():
        kwargs["confirm_model"] = default_live_confirm_model()
    return kwargs


def run_hypothesis_experiment(
    item: Hypothesis,
    baseline_results: dict[str, Any],
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
) -> dict[str, Any]:
    kind = str(item.experiment.get("kind") or "offline_eval")
    baseline_macro = baseline_results["recommended"]["macro"]
    candidates: list[RankedCandidate] = []
    extras: dict[str, Any] = {"kind": kind}

    if kind == "cue_pattern":
        detector = TightPromoCueDetector(include_scout_extras=True)
        results = evaluate_fn(
            config=RECOMMENDED_CONFIG,
            **_eval_kwargs(offline=offline, cache_dir=cache_dir, detector=detector),
        )
        scored = score_candidate(item.id, results)
        candidates.append(scored)
        extras["detector"] = "TightPromoCueDetector"
    elif kind == "cheap_recovery":
        story = evaluate_fn(
            config=RECOMMENDED_CONFIG,
            **_eval_kwargs(
                offline=offline,
                cache_dir=cache_dir,
                detector=StorytellingScoutDetector(include_scout_extras=True),
            ),
        )
        probe = evaluate_fn(
            config=RECOMMENDED_CONFIG,
            **_eval_kwargs(
                offline=offline,
                cache_dir=cache_dir,
                window_postprocess=cheap_midroll_probe,
            ),
        )
        candidates.extend(
            [
                score_candidate(f"{item.id}-storytelling", story),
                score_candidate(f"{item.id}-midroll-probe", probe),
            ]
        )
        extras["variants"] = ["storytelling_phrase", "cheap_midroll_probe"]
        results = story
    elif kind == "pad_sweep":
        extra_cfgs = list(DEFAULT_SWEEP)
        extra_cfgs.extend(
            [
                ScoutConfig(
                    threshold=0.4,
                    pad_seconds=15.0,
                    pad_segments=3,
                    include_scout_extras=True,
                ),
                ScoutConfig(
                    threshold=0.6,
                    pad_seconds=10.0,
                    pad_segments=2,
                    include_scout_extras=True,
                ),
            ]
        )
        sweep_eval = evaluate_fn(
            config=RECOMMENDED_CONFIG,
            sweep=extra_cfgs,
            cache_dir=cache_dir,
            confirm_mock_mode="oracle",
        )
        for row in sweep_eval.get("sweep", []):
            wrapped = results_with_macro(sweep_eval, row["config"], row["macro"])
            label = (
                "extras={include_scout_extras} t={threshold} pad={pad_seconds}/"
                "{pad_segments}".format(**row["config"])
            )
            candidates.append(score_candidate(label, wrapped))
        results = sweep_eval
    elif kind == "golden_ingest":
        templates = [
            example_soft_skills_style_payload(),
            example_the_daily_style_payload(),
        ]
        checked = []
        for payload in templates:
            episode = validate_golden_payload(payload)
            checked.append(
                {
                    "id": episode.fixture_id,
                    "n_segments": len(episode.segments),
                    "n_labeled_ads": len(episode.labeled_ads),
                }
            )
        # Process check uses the frozen eval as the gate: ingest must not
        # require live keys and must not change production defaults.
        scored = score_candidate(item.id, baseline_results)
        candidates.append(scored)
        extras["templates_validated"] = checked
        extras["promotes_to_corpus"] = False
        results = baseline_results
    else:
        results = evaluate_fn(
            config=RECOMMENDED_CONFIG,
            **_eval_kwargs(offline=offline, cache_dir=cache_dir),
        )
        candidates.append(score_candidate(item.id, results))

    ranked = rank_candidates(candidates)
    winner = next((row for row in ranked if not row.rejected), None)
    process_only = kind == "golden_ingest"
    improved = False
    if winner is not None and not process_only:
        improved = _improved_for_primary(item.metric_primary, winner, baseline_macro)
    # Multiple variants: accepted only if a survivor improved the primary.
    representative = winner or (
        ranked[0] if ranked else score_candidate(item.id, results)
    )
    status, verdict = _verdict_for_candidate(
        representative,
        improved_primary=improved,
        process_only=process_only,
    )
    if process_only:
        status, verdict = "measured", "process_ok"
    extras["ranking"] = format_ranking(ranked)
    extras["improved_primary"] = improved
    extras["fold_eligible"] = status == "accepted"
    extras["production_flag"] = bool(DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM)
    return {
        "hypothesis_id": item.id,
        "status": status,
        "verdict": verdict,
        "scored": representative.to_json(),
        "candidates": [row.to_json() for row in ranked],
        "extras": extras,
        "live_llm": bool(results.get("live_llm") or results.get("live_gemini")),
        "corpus_version": results.get("corpus_version"),
    }


def _offline_and_block(
    *,
    offline: bool,
    budget: DailyBudget | None,
) -> tuple[bool, DailyBudget, str | None]:
    cap = budget if budget is not None else DailyBudget(limit_usd=daily_budget_usd())
    if offline:
        return True, cap, None
    if any_live_confirm_enabled():
        if cap.remaining_usd <= 0:
            return True, cap, "budget_exhausted"
        return False, cap, None
    return True, cap, "live_flags_off"


def run_daily_loop(
    *,
    repo_root: Path | None = None,
    ledger_root: Path | None = None,
    runs_dir: Path | None = None,
    run_date: str | None = None,
    offline: bool = True,
    limit: int | None = None,
    hypothesis_ids: list[str] | None = None,
    update_ledger: bool = True,
    evaluate_fn: EvaluateFn | None = None,
    budget: DailyBudget | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Run ranked open hypotheses. Default is offline, no API keys, no fold."""
    date = run_date or _utc_today()
    base = Path(repo_root) if repo_root is not None else Path(".")
    resolved_ledger = ledger_root or (base / "docs/experiments/hypotheses")
    ledger = load_ledger(resolved_ledger)
    eval_impl = evaluate_fn or evaluate_all
    cache = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)

    if DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM:
        raise RuntimeError(
            "Daily loop refuses to run while production "
            "ENABLE_BOW_SCOUT_GEMINI_CONFIRM is True"
        )

    offline, cap, live_blocked = _offline_and_block(offline=offline, budget=budget)
    baseline_results = eval_impl(
        config=RECOMMENDED_CONFIG,
        **_eval_kwargs(offline=True, cache_dir=cache),
    )
    baseline_failures = compare_to_snapshot(baseline_results)
    if baseline_failures:
        raise RuntimeError(
            "Frozen baseline gates failed; fix regressions before experiments.\n"
            + format_gate_failures(baseline_failures)
        )
    baseline_scored = score_candidate("frozen_recommended", baseline_results)

    if hypothesis_ids:
        wanted = set(hypothesis_ids)
        picked = [item for item in ledger.hypotheses if item.id in wanted]
        missing = wanted - {item.id for item in picked}
        if missing:
            raise KeyError(f"unknown hypothesis ids: {sorted(missing)}")
    else:
        picked = rank_open_hypotheses(ledger.hypotheses)
    if limit is not None:
        picked = picked[: max(0, limit)]

    run_root = (Path(runs_dir) if runs_dir is not None else DEFAULT_RUNS_DIR) / date
    run_root.mkdir(parents=True, exist_ok=True)

    hypothesis_rows: list[dict[str, Any]] = []
    for item in picked:
        row = run_hypothesis_experiment(
            item,
            baseline_results,
            evaluate_fn=eval_impl,
            offline=offline or live_blocked is not None,
            cache_dir=cache,
        )
        result_path = run_root / f"{item.id}.json"
        result_path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        last = LastResult(
            run_date=date,
            run_dir=str(run_root),
            summary_path=str(run_root / "summary.json"),
            hypothesis_result_path=str(result_path),
            verdict=str(row["verdict"]),
            passed_gates=not bool(row["scored"].get("rejected")),
            score=row["scored"],
        )
        if update_ledger:
            update_hypothesis(
                ledger,
                item.id,
                status=row["status"],  # type: ignore[arg-type]
                last_result=last,
            )
        hypothesis_rows.append(
            {
                "id": item.id,
                "status": row["status"],
                "verdict": row["verdict"],
                "result_path": str(result_path),
                "fold_eligible": row["extras"].get("fold_eligible"),
            }
        )

    spend_path = run_root / "spend.json"
    write_budget(cap, spend_path)

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_date": date,
        "offline": offline or live_blocked is not None,
        "live_provider": _live_provider(offline or live_blocked is not None),
        "live_blocked": live_blocked,
        "daily_budget_usd": cap.limit_usd,
        "spent_usd": cap.spent_usd,
        "cache_dir": str(cache),
        "production": {
            "enable_bow_scout_gemini_confirm": bool(
                DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM
            ),
            "ad_detection_default_strategy": DEFAULTS.AD_DETECTION_DEFAULT_STRATEGY,
        },
        "baseline": {
            "passed_gates": True,
            "ranking_line": format_ranking([baseline_scored]),
            "macro": baseline_results["recommended"]["macro"],
            "recommended_config": baseline_results["recommended_config"],
        },
        "hypotheses": hypothesis_rows,
        "fold_policy": (
            "accepted = fold-eligible on the experiment package only. "
            "Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. "
            "Snapshot updates require --update-baseline plus RESULTS notes."
        ),
    }
    (run_root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (run_root / "summary.md").write_text(
        _render_summary_markdown(summary, ledger), encoding="utf-8"
    )
    if update_ledger:
        write_ledger(ledger, resolved_ledger)
    summary["run_dir"] = str(run_root)
    summary["ledger_updated"] = update_ledger
    return summary


def _render_summary_markdown(summary: dict[str, Any], ledger: Ledger) -> str:
    lines = [
        f"# Daily experiment run {summary['run_date']}",
        "",
        f"Generated at `{summary['generated_at']}`. "
        f"Mode: **{'offline' if summary['offline'] else 'live'}** "
        f"(provider `{summary['live_provider']}`).",
        "",
        f"- Daily budget: `${summary['daily_budget_usd']:.2f}` "
        f"(spent `${summary['spent_usd']:.4f}`)",
        f"- Production flag `enable_bow_scout_gemini_confirm`: "
        f"`{summary['production']['enable_bow_scout_gemini_confirm']}`",
        f"- Feed default strategy: "
        f"`{summary['production']['ad_detection_default_strategy']}`",
        "- Baseline gates: **passed**",
        "",
        "## Hypotheses this run",
        "",
        "| ID | Status | Verdict | Fold-eligible |",
        "| --- | --- | --- | --- |",
    ]
    for row in summary["hypotheses"]:
        lines.append(
            f"| `{row['id']}` | {row['status']} | {row['verdict']} | "
            f"{row['fold_eligible']} |"
        )
    open_ids = [item.id for item in rank_open_hypotheses(ledger.hypotheses)]
    lines.extend(
        [
            "",
            "## Next open (ranked)",
            "",
            ", ".join(f"`{hid}`" for hid in open_ids) or "none",
            "",
            summary["fold_policy"],
            "",
        ]
    )
    return "\n".join(lines)
