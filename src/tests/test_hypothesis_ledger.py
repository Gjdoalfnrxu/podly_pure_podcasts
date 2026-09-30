"""Hypothesis ledger load, rank, and markdown render (no API keys)."""

from __future__ import annotations

from pathlib import Path

import pytest

from podcast_processor.experiments.hypothesis_ledger import (
    METRIC_PRIORITY,
    load_ledger,
    parse_ledger,
    rank_open_hypotheses,
    render_hypotheses_markdown,
    update_hypothesis,
    write_ledger,
)


def test_committed_ledger_seeds_and_priority_order() -> None:
    ledger = load_ledger()
    assert ledger.priorities == list(METRIC_PRIORITY)
    ids = [item.id for item in ledger.hypotheses]
    assert ids == ["H001", "H002", "H003", "H004"]
    ranked = rank_open_hypotheses(ledger.hypotheses)
    assert [item.id for item in ranked] == ["H001", "H004", "H002", "H003"]
    assert all(item.status == "open" for item in ranked)
    assert {item.metric_primary for item in ledger.hypotheses} <= set(METRIC_PRIORITY)


def test_markdown_matches_json() -> None:
    ledger = load_ledger()
    rendered = render_hypotheses_markdown(ledger)
    committed = Path("docs/experiments/hypotheses/HYPOTHESES.md").read_text(
        encoding="utf-8"
    )
    assert committed == rendered


def test_update_last_result_roundtrip(tmp_path: Path) -> None:
    ledger = load_ledger()
    from podcast_processor.experiments.hypothesis_ledger import LastResult

    update_hypothesis(
        ledger,
        "H001",
        status="measured",
        last_result=LastResult(
            run_date="2026-09-30",
            run_dir=str(tmp_path),
            verdict="no_win",
            passed_gates=True,
            score={"rejected": False},
        ),
    )
    json_file, md_file = write_ledger(ledger, tmp_path)
    reloaded = load_ledger(tmp_path)
    item = next(row for row in reloaded.hypotheses if row.id == "H001")
    assert item.status == "measured"
    assert item.last_result is not None
    assert item.last_result.verdict == "no_win"
    assert json_file.exists()
    assert "H001" in md_file.read_text(encoding="utf-8")
    assert rank_open_hypotheses(reloaded.hypotheses)[0].id == "H004"


def test_rejects_bad_primary() -> None:
    payload = load_ledger().to_json()
    payload["hypotheses"][0]["metric_primary"] = "vibes"
    with pytest.raises(ValueError, match="metric_primary"):
        parse_ledger(payload)
