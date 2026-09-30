"""Confidence-first ranking of scout±confirm candidate configs.

Priorities (absolute, never inverted):

1. **Confidence / no regressions.** Any candidate that fails frozen snapshot
   ε (`compare_to_snapshot` / `gates.json`) is rejected. Rejected candidates
   are not ranked and must not be folded.
2. **Detection.** Among survivors, maximize confirm-path time F1, then time
   recall, then scout ad-block hit rate.
3. **Spend.** Then maximize mean token reduction vs the full AdClassifier
   walk; break remaining ties with fewer scout+confirm input tokens.

The frozen snapshot and gate tolerances are never loosened by this scorer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from podcast_processor.experiments.baseline import (
    GateFailure,
    compare_to_snapshot,
    extract_snapshot,
    format_gate_failures,
)


@dataclass(frozen=True)
class RankedCandidate:
    name: str
    rejected: bool
    gate_failures: tuple[GateFailure, ...]
    detection_f1: float
    detection_recall: float
    ad_hit_rate: float
    token_reduction_pct: float
    scout_input_tokens: float
    sort_key: tuple[float, ...]
    config: dict[str, Any] = field(default_factory=dict)
    macro: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rejected": self.rejected,
            "gate_failures": [
                {
                    "metric": item.metric,
                    "actual": item.actual,
                    "baseline": item.baseline,
                    "limit": item.limit,
                    "message": item.message,
                }
                for item in self.gate_failures
            ],
            "detection_f1": self.detection_f1,
            "detection_recall": self.detection_recall,
            "ad_hit_rate": self.ad_hit_rate,
            "token_reduction_pct": self.token_reduction_pct,
            "scout_input_tokens": self.scout_input_tokens,
            "config": self.config,
        }


def ranking_key(macro: dict[str, Any]) -> tuple[float, ...]:
    """Lexicographic key; smaller is better after the gate filter.

    Detection terms are negated so higher F1/recall/hit sort first. Token
    reduction is negated so more reduction sorts first. Token count is raw
    so fewer scout tokens sort first.
    """
    return (
        -float(macro["scout_confirm_mean_time_f1"]),
        -float(macro["scout_confirm_mean_time_recall"]),
        -float(macro["scout_mean_ad_hit_rate"]),
        -float(macro["mean_token_reduction_pct"]),
        float(macro["sum_scout_input_tokens"]),
    )


def score_candidate(
    name: str,
    results: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    gates: dict[str, Any] | None = None,
    root: Any | None = None,
) -> RankedCandidate:
    failures = compare_to_snapshot(results, snapshot=snapshot, gates=gates, root=root)
    snap = extract_snapshot(results)
    macro = snap["macro"]
    rejected = bool(failures)
    return RankedCandidate(
        name=name,
        rejected=rejected,
        gate_failures=tuple(failures),
        detection_f1=float(macro["scout_confirm_mean_time_f1"]),
        detection_recall=float(macro["scout_confirm_mean_time_recall"]),
        ad_hit_rate=float(macro["scout_mean_ad_hit_rate"]),
        token_reduction_pct=float(macro["mean_token_reduction_pct"]),
        scout_input_tokens=float(macro["sum_scout_input_tokens"]),
        sort_key=ranking_key(macro),
        config=dict(results.get("recommended_config") or {}),
        macro=dict(macro),
    )


def rank_candidates(candidates: list[RankedCandidate]) -> list[RankedCandidate]:
    """Survivors first (best detection, then cost), then rejected (unstable)."""
    survivors = [item for item in candidates if not item.rejected]
    rejected = [item for item in candidates if item.rejected]
    survivors.sort(key=lambda item: item.sort_key)
    rejected.sort(key=lambda item: (item.name, item.sort_key))
    return survivors + rejected


def best_survivor(candidates: list[RankedCandidate]) -> RankedCandidate | None:
    ranked = rank_candidates(candidates)
    for item in ranked:
        if not item.rejected:
            return item
    return None


def format_ranking(candidates: list[RankedCandidate]) -> str:
    ranked = rank_candidates(candidates)
    if not ranked:
        return "No candidates to rank."
    lines = ["Confidence-first ranking (survivors only are fold-eligible):"]
    place = 0
    for item in ranked:
        if item.rejected:
            lines.append(
                f"- REJECT {item.name}: {format_gate_failures(list(item.gate_failures))}"
            )
            continue
        place += 1
        lines.append(
            f"- #{place} {item.name}: F1={item.detection_f1:.4f} "
            f"recall={item.detection_recall:.4f} hit={item.ad_hit_rate:.4f} "
            f"tok↓={item.token_reduction_pct:.2f}% "
            f"scout_tok={item.scout_input_tokens:.0f}"
        )
    winner = best_survivor(candidates)
    if winner is None:
        lines.append("No survivor: keep the frozen recommended config; do not fold.")
    else:
        lines.append(f"Fold-eligible winner: {winner.name}")
    return "\n".join(lines)
