"""Daily hypothesis → experiment → gate loop (offline by default).

Does not change production AdClassifier, CueDetector defaults, Feed strategy,
or frozen gates.json. Live Groq/Gemini require env flags and PODLY_DAILY_BUDGET.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from podcast_processor.experiments.baseline import (
    compare_to_snapshot,
    format_gate_failures,
)
from podcast_processor.experiments.bow_scout import ScoutConfig, overlap_seconds
from podcast_processor.experiments.budget import (
    BudgetExceeded,
    DailyBudget,
    daily_budget_usd,
    default_cache_dir,
    write_budget,
)
from podcast_processor.experiments.candidates import (
    StorytellingScoutDetector,
    TightPromoCueDetector,
    cheap_midroll_probe,
    duration_gated_midroll_probe,
    wider_duration_gated_midroll_probe,
)
from podcast_processor.experiments.eval_harness import (
    DEFAULT_SWEEP,
    RECOMMENDED_CONFIG,
    evaluate_all,
    evaluate_episode,
)
from podcast_processor.experiments.fixtures import (
    self_promo_vs_sponsor,
    style_golden_fixtures,
)
from podcast_processor.experiments.gemini_confirm import (
    GEMINI_LIVE_ENV,
    GROQ_LIVE_ENV,
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
    VALID_STATUSES,
    Hypothesis,
    HypothesisStatus,
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
SEED_ONLY_KINDS = frozenset({"eval_alignment", "detection_gap"})
# H013: mock-only cost wins stay measured; live confirm is required to fold.
FOLD_POLICY = (
    "accepted = fold-eligible on the experiment package only. "
    "Cost hypotheses are never fold-eligible from a mock-only confirm run "
    "(H013: live Gemini confirm required). "
    "Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. "
    "Snapshot updates require --update-baseline plus RESULTS notes."
)


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


def cost_fold_eligible(
    *,
    metric_primary: str,
    otherwise_fold_eligible: bool,
    offline: bool,
    live_llm: bool,
) -> bool:
    """H013: a mock-only cost win is never fold-eligible.

    Detection/confidence folds may still land from oracle mock. Cost folds
    require a live Gemini/Groq confirm pass so H012-style mock/live
    disagreements cannot ship a cheaper pad that later fails frozen ε.
    """
    if not otherwise_fold_eligible:
        return False
    if metric_primary != "cost":
        return True
    if offline or not live_llm:
        return False
    return True


def _live_provider(offline: bool) -> str:
    if offline:
        return "mock"
    if live_calls_enabled():
        return "gemini"
    if groq_live_calls_enabled():
        return "groq"
    return "mock"


def _style_row_summary(row: dict[str, Any]) -> dict[str, Any]:
    confirm = row["paths"]["scout_confirm"]
    return {
        "fixture_id": row["fixture_id"],
        "n_windows": row["n_windows"],
        "ad_hit_rate": row["ad_hit_rate"],
        "time_recall": confirm["time_recall"],
        "time_precision": confirm["time_precision"],
        "residual_strong_cue_rate": confirm["residual_strong_cue_rate"],
        "scout_input_tokens": row["scout_confirm"]["input_tokens"],
        "scout_precision": row["scout_precision"],
    }


def _style_confidence_win(recommended: dict[str, Any], tight: dict[str, Any]) -> bool:
    """TightPromo drops tech-speech windows without labeled-ad recall loss."""
    return (
        int(tight["n_windows"]) < int(recommended["n_windows"])
        and float(tight["time_recall"]) + 1e-12 >= float(recommended["time_recall"])
        and float(tight["ad_hit_rate"]) + 1e-12 >= float(recommended["ad_hit_rate"])
    )


def _cheap_recovery_eval_kwargs(
    variant: str, *, offline: bool, cache_dir: Path, budget: DailyBudget | None = None
) -> dict[str, Any]:
    if variant == "storytelling_phrase":
        return _eval_kwargs(
            offline=offline,
            cache_dir=cache_dir,
            detector=StorytellingScoutDetector(include_scout_extras=True),
            budget=budget,
        )
    if variant == "cheap_midroll_probe":
        return _eval_kwargs(
            offline=offline,
            cache_dir=cache_dir,
            window_postprocess=cheap_midroll_probe,
            budget=budget,
        )
    if variant == "duration_gated_midroll_probe":
        return _eval_kwargs(
            offline=offline,
            cache_dir=cache_dir,
            window_postprocess=duration_gated_midroll_probe,
            budget=budget,
        )
    if variant == "wider_duration_gated_midroll_probe":
        return _eval_kwargs(
            offline=offline,
            cache_dir=cache_dir,
            window_postprocess=wider_duration_gated_midroll_probe,
            budget=budget,
        )
    raise ValueError(f"unknown cheap_recovery variant {variant!r}")


def _eval_kwargs(
    *,
    offline: bool,
    cache_dir: Path,
    detector: Any | None = None,
    window_postprocess: Any | None = None,
    budget: DailyBudget | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "sweep": [],
        "cache_dir": cache_dir,
        "detector": detector,
        "window_postprocess": window_postprocess,
        "confirm_mock_mode": "oracle",
        "budget": budget,
    }
    if not offline and any_live_confirm_enabled():
        kwargs["confirm_model"] = default_live_confirm_model()
    return kwargs


def _experiment_params(item: Hypothesis) -> dict[str, Any]:
    params = item.experiment.get("params") or {}
    return dict(params) if isinstance(params, dict) else {}


def _run_cue_pattern(
    item: Hypothesis,
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
    budget: DailyBudget | None = None,
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    detector = TightPromoCueDetector(include_scout_extras=True)
    results = evaluate_fn(
        config=RECOMMENDED_CONFIG,
        **_eval_kwargs(
            offline=offline, cache_dir=cache_dir, detector=detector, budget=budget
        ),
    )
    return (
        results,
        [score_candidate(item.id, results)],
        {"detector": "TightPromoCueDetector"},
    )


def _run_style_golden_promo(
    item: Hypothesis,
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
    budget: DailyBudget | None = None,
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    params = _experiment_params(item)
    detector = TightPromoCueDetector(include_scout_extras=True)
    results = evaluate_fn(
        config=RECOMMENDED_CONFIG,
        **_eval_kwargs(
            offline=offline, cache_dir=cache_dir, detector=detector, budget=budget
        ),
    )
    fixture_names = params.get("fixtures")
    names = (
        [str(name) for name in fixture_names]
        if isinstance(fixture_names, list)
        else None
    )
    eval_episode_kwargs: dict[str, Any] = {
        "cache_dir": cache_dir,
        "confirm_mock_mode": "oracle",
        "budget": budget,
    }
    style_rows: list[dict[str, Any]] = []
    wins: list[bool] = []
    for episode in style_golden_fixtures(names):
        rec_row = evaluate_episode(episode, RECOMMENDED_CONFIG, **eval_episode_kwargs)
        tight_row = evaluate_episode(
            episode,
            RECOMMENDED_CONFIG,
            detector=detector,
            **eval_episode_kwargs,
        )
        rec_summary = _style_row_summary(rec_row)
        tight_summary = _style_row_summary(tight_row)
        win = _style_confidence_win(rec_summary, tight_summary)
        wins.append(win)
        style_rows.append(
            {
                "fixture_id": episode.fixture_id,
                "recommended": rec_summary,
                "tight_promo": tight_summary,
                "confidence_win": win,
                "windows_dropped": int(rec_summary["n_windows"])
                - int(tight_summary["n_windows"]),
            }
        )
    extras = {
        "detector": "TightPromoCueDetector",
        "style_comparison": style_rows,
        "style_confidence_win": bool(wins) and all(wins),
        "promotes_to_corpus": False,
        "production_promo_pattern_unchanged": True,
    }
    return results, [score_candidate(item.id, results)], extras


def _run_cheap_recovery(
    item: Hypothesis,
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
    budget: DailyBudget | None = None,
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    params = _experiment_params(item)
    raw_variants = params.get("variants")
    variants = (
        [str(name) for name in raw_variants]
        if isinstance(raw_variants, list) and raw_variants
        else ["storytelling_phrase", "cheap_midroll_probe"]
    )
    candidates: list[RankedCandidate] = []
    first_results: dict[str, Any] | None = None
    for variant in variants:
        variant_results = evaluate_fn(
            config=RECOMMENDED_CONFIG,
            **_cheap_recovery_eval_kwargs(
                variant, offline=offline, cache_dir=cache_dir, budget=budget
            ),
        )
        if first_results is None:
            first_results = variant_results
        candidates.append(score_candidate(f"{item.id}-{variant}", variant_results))
    results = first_results or evaluate_fn(
        config=RECOMMENDED_CONFIG,
        **_eval_kwargs(offline=offline, cache_dir=cache_dir, budget=budget),
    )
    return results, candidates, {"variants": variants}


def _run_pad_sweep(
    item: Hypothesis,
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
    budget: DailyBudget | None = None,
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    params = _experiment_params(item)
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
    for spec in params.get("extra_configs") or []:
        if not isinstance(spec, dict):
            continue
        extra_cfgs.append(
            ScoutConfig(
                threshold=float(spec.get("threshold", 0.5)),
                pad_seconds=float(spec.get("pad_seconds", 15.0)),
                pad_segments=int(spec.get("pad_segments", 3)),
                include_scout_extras=bool(spec.get("include_scout_extras", True)),
            )
        )
    sweep_eval = evaluate_fn(
        config=RECOMMENDED_CONFIG,
        sweep=extra_cfgs,
        cache_dir=cache_dir,
        confirm_mock_mode="oracle",
        budget=budget,
        **(
            {"confirm_model": default_live_confirm_model()}
            if not offline and any_live_confirm_enabled()
            else {}
        ),
    )
    candidates: list[RankedCandidate] = []
    for row in sweep_eval.get("sweep", []):
        wrapped = results_with_macro(sweep_eval, row["config"], row["macro"])
        label = (
            "extras={include_scout_extras} t={threshold} pad={pad_seconds}/"
            "{pad_segments}".format(**row["config"])
        )
        candidates.append(score_candidate(label, wrapped))
    return sweep_eval, candidates, {}


def _run_golden_ingest(
    item: Hypothesis,
    baseline_results: dict[str, Any],
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
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
    scored = score_candidate(item.id, baseline_results)
    extras = {
        "templates_validated": checked,
        "promotes_to_corpus": False,
    }
    return baseline_results, [scored], extras


def _run_process_gate(
    item: Hypothesis,
    baseline_results: dict[str, Any],
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    """Ledger process rule. Cost-fold live-confirm is enforced in fold policy."""
    params = _experiment_params(item)
    scored = score_candidate(item.id, baseline_results)
    extras = {
        "rule": str(params.get("rule") or item.statement),
        "requires_live_confirm_for_cost_fold": bool(
            params.get("requires_live_confirm_for_cost_fold", True)
        ),
        "enforced_in_fold_policy": True,
        "promotes_to_corpus": False,
    }
    return baseline_results, [scored], extras


def _self_promo_eval_gap() -> dict[str, Any]:
    """Corpus v1 labels only the external sponsor on self_promo_vs_sponsor."""
    episode = self_promo_vs_sponsor()
    span_start = 3 * 60.0
    span_end = span_start + 15.0
    labeled_overlap = sum(
        overlap_seconds(ad.start, ad.end, span_start, span_end)
        for ad in episode.labeled_ads
    )
    return {
        "fixture_id": episode.fixture_id,
        "self_promo_span": {"start": span_start, "end": span_end},
        "labeled_overlap_seconds": labeled_overlap,
        "self_promo_labeled_as_ad": labeled_overlap > 0.5,
        "include_self_promo": bool(RECOMMENDED_CONFIG.include_self_promo),
        "n_labeled_ads": len(episode.labeled_ads),
    }


def _run_eval_alignment(
    item: Hypothesis,
    baseline_results: dict[str, Any],
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    """Record the self-promo eval gap. Do not rewrite the frozen snapshot."""
    scored = score_candidate(item.id, baseline_results)
    gap = _self_promo_eval_gap()
    extras = {
        "rule": "align_eval_self_promo_as_ad",
        "policy": (
            "self-promo, network/sister-show promos, first-party app plugs, "
            "and membership/donation asks count as ads"
        ),
        "eval_excludes_self_promo": (
            not gap["self_promo_labeled_as_ad"] and not gap["include_self_promo"]
        ),
        "silent_baseline_change": False,
        "requires_update_baseline": True,
        "gap": gap,
        "promotes_to_corpus": False,
        "fold_eligible": False,
    }
    return baseline_results, [scored], extras


def _run_detection_gap(
    item: Hypothesis,
    baseline_results: dict[str, Any],
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    """Seeded detection miss; stay open until an offline golden exists."""
    params = _experiment_params(item)
    scored = score_candidate(item.id, baseline_results)
    extras = {
        "gap": str(params.get("gap") or item.statement),
        "offline_testable": bool(params.get("offline_testable", True)),
        "production_change": False,
        "fold_eligible": False,
        "promotes_to_corpus": False,
    }
    return baseline_results, [scored], extras


def _dispatch_experiment(
    kind: str,
    item: Hypothesis,
    baseline_results: dict[str, Any],
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
    budget: DailyBudget | None,
) -> tuple[dict[str, Any], list[RankedCandidate], dict[str, Any]]:
    """Run the experiment implementation for `kind`."""
    eval_kinds = {
        "cue_pattern": _run_cue_pattern,
        "style_golden_promo": _run_style_golden_promo,
        "cheap_recovery": _run_cheap_recovery,
        "pad_sweep": _run_pad_sweep,
    }
    baseline_kinds = {
        "golden_ingest": _run_golden_ingest,
        "process_gate": _run_process_gate,
        "eval_alignment": _run_eval_alignment,
        "detection_gap": _run_detection_gap,
    }
    runner = eval_kinds.get(kind)
    if runner is not None:
        return runner(
            item,
            evaluate_fn=evaluate_fn,
            offline=offline,
            cache_dir=cache_dir,
            budget=budget,
        )
    baseline_runner = baseline_kinds.get(kind)
    if baseline_runner is not None:
        return baseline_runner(item, baseline_results)
    results = evaluate_fn(
        config=RECOMMENDED_CONFIG,
        **_eval_kwargs(offline=offline, cache_dir=cache_dir, budget=budget),
    )
    return results, [score_candidate(item.id, results)], {}


def run_hypothesis_experiment(
    item: Hypothesis,
    baseline_results: dict[str, Any],
    *,
    evaluate_fn: EvaluateFn,
    offline: bool,
    cache_dir: Path,
    budget: DailyBudget | None = None,
) -> dict[str, Any]:
    kind = str(item.experiment.get("kind") or "offline_eval")
    baseline_macro = baseline_results["recommended"]["macro"]
    extras: dict[str, Any] = {"kind": kind}
    results, candidates, extra = _dispatch_experiment(
        kind,
        item,
        baseline_results,
        evaluate_fn=evaluate_fn,
        offline=offline,
        cache_dir=cache_dir,
        budget=budget,
    )
    extras.update(extra)

    ranked = rank_candidates(candidates)
    winner = next((row for row in ranked if not row.rejected), None)
    process_only = kind in {"golden_ingest", "process_gate"}
    seed_only = kind in SEED_ONLY_KINDS
    improved = False
    if kind == "style_golden_promo":
        improved = bool(extras.get("style_confidence_win")) and (
            winner is not None and not winner.rejected
        )
    elif winner is not None and not process_only and not seed_only:
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
    live_llm = bool(results.get("live_llm") or results.get("live_gemini"))
    otherwise_fold_eligible = status == "accepted"
    fold_ok = cost_fold_eligible(
        metric_primary=item.metric_primary,
        otherwise_fold_eligible=otherwise_fold_eligible,
        offline=offline,
        live_llm=live_llm,
    )
    if otherwise_fold_eligible and not fold_ok:
        extras["mock_only_cost_win"] = True
        extras["fold_held_reason"] = "live_confirm_required_for_cost_fold"
        status, verdict = "measured", "no_win"
    if seed_only:
        status, verdict = "open", "seeded"
        fold_ok = False
    extras["ranking"] = format_ranking(ranked)
    extras["improved_primary"] = improved
    extras["fold_eligible"] = fold_ok and status == "accepted"
    extras["requires_live_confirm_for_cost_fold"] = True
    extras["production_flag"] = bool(DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM)
    scored_json = representative.to_json()
    if kind == "style_golden_promo":
        scored_json["style_confidence_win"] = extras.get("style_confidence_win")
        scored_json["style_comparison"] = extras.get("style_comparison")
        scored_json["promotes_to_corpus"] = extras.get("promotes_to_corpus")
    return {
        "hypothesis_id": item.id,
        "status": status,
        "verdict": verdict,
        "scored": scored_json,
        "candidates": [row.to_json() for row in ranked],
        "extras": extras,
        "live_llm": live_llm,
        "corpus_version": results.get("corpus_version"),
    }


@contextmanager
def live_confirm_flags_cleared() -> Iterator[None]:
    """Drop live confirm flags so oracle baseline cannot spend or cache-poison.

    `GeminiConfirmClient.confirm_window` goes live whenever
    `PODLY_*_CONFIRM_LIVE` is set, even if the caller asked for oracle mock.
    Baseline eval must always be mock; `--live` restores the flags afterward.
    """
    saved: dict[str, str] = {}
    for name in (GEMINI_LIVE_ENV, GROQ_LIVE_ENV):
        value = os.environ.pop(name, None)
        if value is not None:
            saved[name] = value
    try:
        yield
    finally:
        os.environ.update(saved)


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


def _run_hypothesis_with_offline_guard(
    item: Hypothesis,
    baseline_results: dict[str, Any],
    *,
    evaluate_fn: EvaluateFn,
    cache_dir: Path,
    budget: DailyBudget | None,
    experiment_offline: bool,
) -> dict[str, Any]:
    if experiment_offline:
        with live_confirm_flags_cleared():
            return run_hypothesis_experiment(
                item,
                baseline_results,
                evaluate_fn=evaluate_fn,
                offline=True,
                cache_dir=cache_dir,
                budget=budget,
            )
    return run_hypothesis_experiment(
        item,
        baseline_results,
        evaluate_fn=evaluate_fn,
        offline=False,
        cache_dir=cache_dir,
        budget=budget,
    )


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
    with live_confirm_flags_cleared():
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
    experiment_offline = offline or live_blocked is not None
    spend_path = run_root / "spend.json"
    for item in picked:
        try:
            row = _run_hypothesis_with_offline_guard(
                item,
                baseline_results,
                evaluate_fn=eval_impl,
                cache_dir=cache,
                budget=cap,
                experiment_offline=experiment_offline,
            )
        except BudgetExceeded:
            live_blocked = "budget_exhausted"
            experiment_offline = True
            write_budget(cap, spend_path)
            break
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
            next_status = str(row["status"])
            if next_status not in VALID_STATUSES:
                raise ValueError(f"invalid status {next_status!r}")
            update_hypothesis(
                ledger,
                item.id,
                status=cast(HypothesisStatus, next_status),
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
        write_budget(cap, spend_path)

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
        "fold_policy": FOLD_POLICY,
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
