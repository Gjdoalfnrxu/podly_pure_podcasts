"""Frozen baseline snapshot + regression gates for the scout eval.

The committed snapshot is the source of truth. CI compares a fresh offline
eval against it using documented absolute (percentage-point) and relative
tolerances. Update only with:

  PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py \\
      --update-baseline
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASELINE_DIR = Path("docs/experiments/bow_scout_gemini_confirm/baseline/v1")
SNAPSHOT_NAME = "snapshot.json"
GATES_NAME = "gates.json"

# Absolute percentage-point (or rate) slack unless noted.
DEFAULT_GATES: dict[str, Any] = {
    "corpus_version": "v1",
    "snapshot_id": "bow_scout_gemini_confirm_v1",
    "description": (
        "Fail CI if the scout±confirm path regresses vs the frozen snapshot, "
        "or if the production-like oracle mock loses labeled-ad recall."
    ),
    "tolerances": {
        "scout_time_recall_drop": 0.02,
        "scout_ad_hit_rate_drop": 0.02,
        "scout_time_f1_drop": 0.03,
        "scout_time_precision_drop": 0.05,
        "scout_false_negative_rate_rise": 0.02,
        "scout_residual_strong_cue_rate_rise": 0.01,
        "scout_token_reduction_pct_drop": 2.0,
        "scout_input_tokens_rel_rise": 0.10,
        "production_time_recall_drop": 0.0,
        "production_input_tokens_rel_rise": 0.10,
    },
    "required_production_test_modules": [
        "src/tests/test_cue_detector.py",
        "src/tests/test_ad_classifier.py",
        "src/tests/test_audio_processor.py",
        "src/tests/test_process_audio.py",
    ],
    "notes": (
        "Recall/hit/F1/FN/residual are absolute (e.g. 0.02 = 2 percentage "
        "points). Token-count rise is relative (0.10 = +10%). Token reduction "
        "is in percentage points vs the snapshot mean_token_reduction_pct. "
        "Default CI uses mocks; live Gemini is never required."
    ),
}


@dataclass(frozen=True)
class GateFailure:
    metric: str
    actual: float
    baseline: float
    limit: float
    message: str


def baseline_dir(root: Path | None = None) -> Path:
    return Path(root) if root is not None else DEFAULT_BASELINE_DIR


def snapshot_path(root: Path | None = None) -> Path:
    return baseline_dir(root) / SNAPSHOT_NAME


def gates_path(root: Path | None = None) -> Path:
    return baseline_dir(root) / GATES_NAME


def load_gates(root: Path | None = None) -> dict[str, Any]:
    path = gates_path(root)
    if not path.exists():
        return dict(DEFAULT_GATES)
    payload = json.loads(path.read_text(encoding="utf-8"))
    merged = dict(DEFAULT_GATES)
    merged.update(payload)
    tolerances = dict(DEFAULT_GATES["tolerances"])
    tolerances.update(payload.get("tolerances") or {})
    merged["tolerances"] = tolerances
    return merged


def write_gates(root: Path | None = None, gates: dict[str, Any] | None = None) -> Path:
    target = baseline_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = gates_path(root)
    path.write_text(
        json.dumps(gates or DEFAULT_GATES, indent=2) + "\n", encoding="utf-8"
    )
    return path


def extract_snapshot(results: dict[str, Any]) -> dict[str, Any]:
    """Comparable subset (no timestamps) used as the frozen baseline."""
    rec = results["recommended"]
    macro = rec["macro"]
    per_fixture = []
    for row in rec["per_fixture"]:
        scout_confirm = row["paths"]["scout_confirm"]
        scout = row["paths"]["scout"]
        production = row["paths"]["production_like"]
        per_fixture.append(
            {
                "fixture_id": row["fixture_id"],
                "scout_ad_hit_rate": scout["ad_hit_rate"],
                "scout_time_recall": scout["time_recall"],
                "scout_time_precision": scout["time_precision"],
                "scout_time_f1": scout["time_f1"],
                "scout_false_negative_rate": scout["false_negative_rate"],
                "scout_confirm_time_recall": scout_confirm["time_recall"],
                "scout_confirm_time_precision": scout_confirm["time_precision"],
                "scout_confirm_time_f1": scout_confirm["time_f1"],
                "scout_confirm_residual_strong_cues": scout_confirm[
                    "residual_strong_cues"
                ],
                "scout_confirm_residual_strong_cue_rate": scout_confirm[
                    "residual_strong_cue_rate"
                ],
                "production_time_recall": production["time_recall"],
                "production_time_precision": production["time_precision"],
                "production_time_f1": production["time_f1"],
                "full_input_tokens": row["full_classifier"]["input_tokens"],
                "scout_input_tokens": row["scout_confirm_tokens"]["input_tokens"],
                "token_reduction_pct": row["token_reduction_pct"],
            }
        )
    return {
        "corpus_version": results.get("corpus_version", "v1"),
        "corpus_sha256": results.get("corpus_sha256"),
        "n_fixtures": macro["n_fixtures"],
        "recommended_config": results["recommended_config"],
        "macro": {
            "scout_mean_ad_hit_rate": macro["scout_mean_ad_hit_rate"],
            "scout_mean_time_recall": macro["scout_mean_time_recall"],
            "scout_mean_time_precision": macro["scout_mean_time_precision"],
            "scout_mean_time_f1": macro["scout_mean_time_f1"],
            "scout_mean_false_negative_rate": macro["scout_mean_false_negative_rate"],
            "scout_confirm_mean_time_recall": macro["scout_confirm_mean_time_recall"],
            "scout_confirm_mean_time_precision": macro[
                "scout_confirm_mean_time_precision"
            ],
            "scout_confirm_mean_time_f1": macro["scout_confirm_mean_time_f1"],
            "scout_confirm_mean_residual_strong_cue_rate": macro[
                "scout_confirm_mean_residual_strong_cue_rate"
            ],
            "scout_confirm_sum_residual_strong_cues": macro[
                "scout_confirm_sum_residual_strong_cues"
            ],
            "production_mean_time_recall": macro["production_mean_time_recall"],
            "production_mean_time_f1": macro["production_mean_time_f1"],
            "mean_token_reduction_pct": macro["mean_token_reduction_pct"],
            "sum_full_input_tokens": macro["sum_full_input_tokens"],
            "sum_scout_input_tokens": macro["sum_scout_input_tokens"],
        },
        "per_fixture": per_fixture,
        "live_gemini": bool(results.get("live_gemini", False)),
    }


def write_snapshot(results: dict[str, Any], root: Path | None = None) -> Path:
    target = baseline_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(root)
    path.write_text(
        json.dumps(extract_snapshot(results), indent=2) + "\n", encoding="utf-8"
    )
    write_gates(root)
    return path


def load_snapshot(root: Path | None = None) -> dict[str, Any]:
    path = snapshot_path(root)
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline snapshot missing: {path}. "
            "Create it with run_bow_scout_eval.py --update-baseline"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("baseline snapshot must be a JSON object")
    return payload


def _fail(
    failures: list[GateFailure],
    metric: str,
    actual: float,
    baseline: float,
    limit: float,
    message: str,
) -> None:
    failures.append(
        GateFailure(
            metric=metric,
            actual=actual,
            baseline=baseline,
            limit=limit,
            message=message,
        )
    )


def compare_to_snapshot(
    results: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    gates: dict[str, Any] | None = None,
    root: Path | None = None,
) -> list[GateFailure]:
    snap = snapshot if snapshot is not None else load_snapshot(root)
    spec = gates if gates is not None else load_gates(root)
    tol = spec["tolerances"]
    actual = extract_snapshot(results)
    failures: list[GateFailure] = []

    if actual["n_fixtures"] != snap["n_fixtures"]:
        _fail(
            failures,
            "n_fixtures",
            float(actual["n_fixtures"]),
            float(snap["n_fixtures"]),
            float(snap["n_fixtures"]),
            "Fixture count changed; update the golden corpus and snapshot "
            "together with --write-corpus --update-baseline.",
        )

    a_macro = actual["macro"]
    b_macro = snap["macro"]

    def drop_gate(metric: str, actual_v: float, base_v: float, slack: float) -> None:
        limit = base_v - slack
        if actual_v + 1e-12 < limit:
            _fail(
                failures,
                metric,
                actual_v,
                base_v,
                limit,
                f"{metric} {actual_v:.6f} dropped below baseline {base_v:.6f} − {slack}",
            )

    def rise_gate(metric: str, actual_v: float, base_v: float, slack: float) -> None:
        limit = base_v + slack
        if actual_v - 1e-12 > limit:
            _fail(
                failures,
                metric,
                actual_v,
                base_v,
                limit,
                f"{metric} {actual_v:.6f} rose above baseline {base_v:.6f} + {slack}",
            )

    drop_gate(
        "scout_confirm_mean_time_recall",
        a_macro["scout_confirm_mean_time_recall"],
        b_macro["scout_confirm_mean_time_recall"],
        float(tol["scout_time_recall_drop"]),
    )
    drop_gate(
        "scout_mean_ad_hit_rate",
        a_macro["scout_mean_ad_hit_rate"],
        b_macro["scout_mean_ad_hit_rate"],
        float(tol["scout_ad_hit_rate_drop"]),
    )
    drop_gate(
        "scout_confirm_mean_time_f1",
        a_macro["scout_confirm_mean_time_f1"],
        b_macro["scout_confirm_mean_time_f1"],
        float(tol["scout_time_f1_drop"]),
    )
    drop_gate(
        "scout_confirm_mean_time_precision",
        a_macro["scout_confirm_mean_time_precision"],
        b_macro["scout_confirm_mean_time_precision"],
        float(tol["scout_time_precision_drop"]),
    )
    rise_gate(
        "scout_mean_false_negative_rate",
        a_macro["scout_mean_false_negative_rate"],
        b_macro["scout_mean_false_negative_rate"],
        float(tol["scout_false_negative_rate_rise"]),
    )
    rise_gate(
        "scout_confirm_mean_residual_strong_cue_rate",
        a_macro["scout_confirm_mean_residual_strong_cue_rate"],
        b_macro["scout_confirm_mean_residual_strong_cue_rate"],
        float(tol["scout_residual_strong_cue_rate_rise"]),
    )
    drop_gate(
        "mean_token_reduction_pct",
        a_macro["mean_token_reduction_pct"],
        b_macro["mean_token_reduction_pct"],
        float(tol["scout_token_reduction_pct_drop"]),
    )
    drop_gate(
        "production_mean_time_recall",
        a_macro["production_mean_time_recall"],
        b_macro["production_mean_time_recall"],
        float(tol["production_time_recall_drop"]),
    )

    token_limit = b_macro["sum_scout_input_tokens"] * (
        1.0 + float(tol["scout_input_tokens_rel_rise"])
    )
    if a_macro["sum_scout_input_tokens"] - 1e-12 > token_limit:
        _fail(
            failures,
            "sum_scout_input_tokens",
            float(a_macro["sum_scout_input_tokens"]),
            float(b_macro["sum_scout_input_tokens"]),
            float(token_limit),
            "Scout+confirm input tokens rose more than "
            f"{100 * float(tol['scout_input_tokens_rel_rise']):.0f}% vs baseline.",
        )

    prod_token_limit = b_macro["sum_full_input_tokens"] * (
        1.0 + float(tol["production_input_tokens_rel_rise"])
    )
    if a_macro["sum_full_input_tokens"] - 1e-12 > prod_token_limit:
        _fail(
            failures,
            "sum_full_input_tokens",
            float(a_macro["sum_full_input_tokens"]),
            float(b_macro["sum_full_input_tokens"]),
            float(prod_token_limit),
            "Production-like full-walk tokens rose more than "
            f"{100 * float(tol['production_input_tokens_rel_rise']):.0f}% vs baseline.",
        )

    return failures


def format_gate_failures(failures: list[GateFailure]) -> str:
    if not failures:
        return "All eval gates passed vs frozen baseline."
    lines = ["Eval gates failed vs frozen baseline:"]
    for item in failures:
        lines.append(f"- {item.message}")
    return "\n".join(lines)
