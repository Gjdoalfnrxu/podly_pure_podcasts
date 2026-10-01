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
    assert ids == [
        "H001",
        "H002",
        "H003",
        "H004",
        "H005",
        "H006",
        "H007",
        "H008",
        "H009",
        "H010",
    ]
    by_id = {item.id: item for item in ledger.hypotheses}
    assert by_id["H001"].status == "measured"
    assert by_id["H002"].status == "rejected"
    assert by_id["H003"].status == "measured"
    assert by_id["H004"].status == "measured"
    assert by_id["H005"].status == "accepted"
    assert by_id["H006"].status == "accepted"
    assert by_id["H007"].status == "accepted"
    assert by_id["H008"].status == "accepted"
    assert by_id["H009"].status == "open"
    assert by_id["H010"].status == "open"
    assert by_id["H001"].last_result is not None
    assert by_id["H001"].last_result.score is not None
    assert by_id["H001"].last_result.score.get("live_baseline_windows") == 26
    assert by_id["H001"].last_result.score.get("live_tight_windows") == 15
    ranked = rank_open_hypotheses(ledger.hypotheses)
    assert [item.id for item in ranked] == ["H010", "H009"]
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
    open_ids = [row.id for row in rank_open_hypotheses(reloaded.hypotheses)]
    assert "H001" not in open_ids
    assert "H010" in open_ids


def test_rejects_bad_primary() -> None:
    payload = load_ledger().to_json()
    payload["hypotheses"][0]["metric_primary"] = "vibes"
    with pytest.raises(ValueError, match="metric_primary"):
        parse_ledger(payload)
