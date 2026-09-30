"""Hypothesis ledger: ranked open experiments for the daily loop.

JSON at docs/experiments/hypotheses/hypotheses.json is the machine-readable
source of truth. HYPOTHESES.md is the human table rendered from the same
records. Status updates write both files.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

MetricPrimary = Literal["confidence", "detection", "cost"]
HypothesisStatus = Literal[
    "open",
    "running",
    "measured",
    "accepted",
    "rejected",
    "blocked",
]

# User absolute order: never regress, then catch ads, then spend less.
METRIC_PRIORITY: tuple[MetricPrimary, ...] = ("confidence", "detection", "cost")
PRIMARY_RANK = {name: index for index, name in enumerate(METRIC_PRIORITY)}

VALID_STATUSES: frozenset[str] = frozenset(
    {"open", "running", "measured", "accepted", "rejected", "blocked"}
)
VALID_PRIMARIES: frozenset[str] = frozenset(METRIC_PRIORITY)

DEFAULT_LEDGER_DIR = Path("docs/experiments/hypotheses")
JSON_NAME = "hypotheses.json"
MARKDOWN_NAME = "HYPOTHESES.md"
SCHEMA_VERSION = 1


@dataclass
class PredictedEffect:
    confidence: str = ""
    detection: str = ""
    cost: str = ""


@dataclass
class LastResult:
    run_date: str | None = None
    run_dir: str | None = None
    summary_path: str | None = None
    hypothesis_result_path: str | None = None
    verdict: str | None = None
    passed_gates: bool | None = None
    score: dict[str, Any] | None = None


@dataclass
class Hypothesis:
    id: str
    statement: str
    metric_primary: MetricPrimary
    status: HypothesisStatus
    predicted_effect: PredictedEffect
    experiment: dict[str, Any]
    rank: int | None = None
    last_result: LastResult | None = None
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.last_result is None:
            payload["last_result"] = None
        return payload


@dataclass
class Ledger:
    schema_version: int
    priorities: list[str]
    hypotheses: list[Hypothesis]
    updated_at: str | None = None
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "priorities": list(self.priorities),
            "updated_at": self.updated_at,
            "notes": self.notes,
            "hypotheses": [item.to_json() for item in self.hypotheses],
        }


def ledger_dir(root: Path | None = None) -> Path:
    return Path(root) if root is not None else DEFAULT_LEDGER_DIR


def json_path(root: Path | None = None) -> Path:
    return ledger_dir(root) / JSON_NAME


def markdown_path(root: Path | None = None) -> Path:
    return ledger_dir(root) / MARKDOWN_NAME


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"hypothesis field {key!r} must be a non-empty string")
    return value.strip()


def _parse_predicted(raw: object) -> PredictedEffect:
    if not isinstance(raw, dict):
        raise TypeError("predicted_effect must be an object")
    return PredictedEffect(
        confidence=str(raw.get("confidence") or ""),
        detection=str(raw.get("detection") or ""),
        cost=str(raw.get("cost") or ""),
    )


def _parse_last_result(raw: object) -> LastResult | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("last_result must be an object or null")
    score = raw.get("score")
    if score is not None and not isinstance(score, dict):
        raise TypeError("last_result.score must be an object or null")
    passed = raw.get("passed_gates")
    if passed is not None and not isinstance(passed, bool):
        raise TypeError("last_result.passed_gates must be a bool or null")
    return LastResult(
        run_date=str(raw["run_date"]) if raw.get("run_date") is not None else None,
        run_dir=str(raw["run_dir"]) if raw.get("run_dir") is not None else None,
        summary_path=(
            str(raw["summary_path"]) if raw.get("summary_path") is not None else None
        ),
        hypothesis_result_path=(
            str(raw["hypothesis_result_path"])
            if raw.get("hypothesis_result_path") is not None
            else None
        ),
        verdict=str(raw["verdict"]) if raw.get("verdict") is not None else None,
        passed_gates=passed,
        score=dict(score) if isinstance(score, dict) else None,
    )


def _parse_hypothesis(raw: object) -> Hypothesis:
    if not isinstance(raw, dict):
        raise TypeError("hypothesis rows must be objects")
    metric = _require_str(raw, "metric_primary")
    if metric not in VALID_PRIMARIES:
        raise ValueError(
            f"metric_primary must be one of {sorted(VALID_PRIMARIES)}, got {metric!r}"
        )
    status = _require_str(raw, "status")
    if status not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}"
        )
    experiment = raw.get("experiment") or {}
    if not isinstance(experiment, dict):
        raise TypeError("experiment must be an object")
    rank_raw = raw.get("rank")
    rank: int | None
    if rank_raw is None:
        rank = None
    elif isinstance(rank_raw, int) and not isinstance(rank_raw, bool):
        rank = rank_raw
    else:
        raise TypeError("rank must be an int or null")
    return Hypothesis(
        id=_require_str(raw, "id"),
        statement=_require_str(raw, "statement"),
        metric_primary=metric,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        predicted_effect=_parse_predicted(raw.get("predicted_effect") or {}),
        experiment=dict(experiment),
        rank=rank,
        last_result=_parse_last_result(raw.get("last_result")),
        notes=str(raw.get("notes") or ""),
    )


def parse_ledger(payload: dict[str, Any]) -> Ledger:
    version = payload.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise TypeError("schema_version must be an int")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported hypothesis schema_version {version}")
    priorities_raw = payload.get("priorities") or list(METRIC_PRIORITY)
    if not isinstance(priorities_raw, list) or [str(p) for p in priorities_raw] != list(
        METRIC_PRIORITY
    ):
        raise ValueError(f"priorities must be {list(METRIC_PRIORITY)}")
    raw_list = payload.get("hypotheses")
    if not isinstance(raw_list, list):
        raise TypeError("hypotheses must be a list")
    items = [_parse_hypothesis(row) for row in raw_list]
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate hypothesis ids: {ids}")
    return Ledger(
        schema_version=version,
        priorities=[str(p) for p in priorities_raw],
        hypotheses=items,
        updated_at=str(payload["updated_at"]) if payload.get("updated_at") else None,
        notes=str(payload.get("notes") or ""),
    )


def load_ledger(root: Path | None = None) -> Ledger:
    path = json_path(root)
    if not path.exists():
        raise FileNotFoundError(f"Hypothesis ledger missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("hypotheses.json must be a JSON object")
    return parse_ledger(payload)


def rank_open_hypotheses(items: list[Hypothesis]) -> list[Hypothesis]:
    """Open hypotheses, confidence-first then detection then cost, then id."""
    open_items = [item for item in items if item.status == "open"]
    return sorted(
        open_items,
        key=lambda item: (PRIMARY_RANK[item.metric_primary], item.id),
    )


def hypothesis_by_id(ledger: Ledger, hypothesis_id: str) -> Hypothesis:
    for item in ledger.hypotheses:
        if item.id == hypothesis_id:
            return item
    raise KeyError(f"unknown hypothesis id {hypothesis_id!r}")


def render_hypotheses_markdown(ledger: Ledger) -> str:
    lines = [
        "# Hypothesis ledger",
        "",
        "Machine-readable source: `docs/experiments/hypotheses/hypotheses.json`.",
        "User priorities (absolute): **confidence** (no regressions) → "
        "**detection** (F1/recall) → **cost** (token reduction).",
        "",
        "The daily runner loads **open** rows in that order, runs offline eval "
        "vs the frozen snapshot by default, and never loosens `gates.json`.",
        "",
        f"Updated at `{ledger.updated_at or 'unspecified'}` (UTC).",
        "",
        "| ID | Primary | Status | Statement | Last result |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in ledger.hypotheses:
        last = item.last_result
        last_cell = "—"
        if last is not None and (last.verdict or last.run_date):
            bits = [part for part in (last.verdict, last.run_date) if part]
            last_cell = ", ".join(bits)
            if last.hypothesis_result_path:
                last_cell += f" (`{last.hypothesis_result_path}`)"
        statement = item.statement.replace("|", "\\|")
        lines.append(
            f"| `{item.id}` | {item.metric_primary} | {item.status} | "
            f"{statement} | {last_cell} |"
        )
    lines.extend(["", "## Predicted effects", ""])
    for item in ledger.hypotheses:
        lines.extend(
            [
                f"### `{item.id}`",
                "",
                item.statement,
                "",
                f"- Primary metric: `{item.metric_primary}`",
                f"- Status: `{item.status}`",
                f"- Experiment kind: `{item.experiment.get('kind', 'unknown')}`",
                f"- Confidence: {item.predicted_effect.confidence or '—'}",
                f"- Detection: {item.predicted_effect.detection or '—'}",
                f"- Cost: {item.predicted_effect.cost or '—'}",
            ]
        )
        if item.notes:
            lines.append(f"- Notes: {item.notes}")
        lines.append("")
    lines.extend(
        [
            "## Status values",
            "",
            "- `open` — ranked for the next daily pick; not yet conclusive.",
            "- `running` — in progress in a live/offline run.",
            "- `measured` — has last_result numbers; no fold yet.",
            "- `accepted` — fold-eligible on this branch (experiment package "
            "only). Production `AdClassifier` / flag stay unchanged.",
            "- `rejected` — failed frozen regression ε; do not fold.",
            "- `blocked` — waiting on live budget, golden labels, or review.",
            "",
            "Never set `accepted` for a candidate that failed `compare_to_snapshot`.",
            "Never loosen `docs/experiments/bow_scout_gemini_confirm/baseline/v1/gates.json`.",
            "",
        ]
    )
    if ledger.notes:
        lines.extend(["## Ledger notes", "", ledger.notes, ""])
    return "\n".join(lines)


def write_ledger(ledger: Ledger, root: Path | None = None) -> tuple[Path, Path]:
    target = ledger_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    ledger.updated_at = datetime.now(UTC).isoformat()
    json_file = json_path(root)
    json_file.write_text(
        json.dumps(ledger.to_json(), indent=2) + "\n", encoding="utf-8"
    )
    md_file = markdown_path(root)
    md_file.write_text(render_hypotheses_markdown(ledger), encoding="utf-8")
    return json_file, md_file


def update_hypothesis(
    ledger: Ledger,
    hypothesis_id: str,
    *,
    status: HypothesisStatus | None = None,
    last_result: LastResult | None = None,
    notes: str | None = None,
) -> Hypothesis:
    item = hypothesis_by_id(ledger, hypothesis_id)
    if status is not None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        item.status = status
    if last_result is not None:
        item.last_result = last_result
    if notes is not None:
        item.notes = notes
    return item
