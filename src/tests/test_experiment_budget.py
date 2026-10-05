"""Daily budget cap and cache-is-free accounting."""

from __future__ import annotations

import pytest

from podcast_processor.experiments.budget import (
    DEFAULT_DAILY_BUDGET_USD,
    BudgetExceeded,
    DailyBudget,
    daily_budget_usd,
    estimate_usd,
    load_budget,
    write_budget,
)


def test_default_budget_is_fifty_cents(monkeypatch) -> None:
    monkeypatch.delenv("PODLY_DAILY_BUDGET", raising=False)
    assert daily_budget_usd() == DEFAULT_DAILY_BUDGET_USD
    assert DEFAULT_DAILY_BUDGET_USD == 0.50


def test_cache_hits_do_not_consume_budget() -> None:
    budget = DailyBudget(limit_usd=0.50)
    budget.record(0.10, source="confirm", cached=False, provider="gemini")
    budget.record(0.10, source="confirm", cached=True, provider="gemini")
    assert budget.spent_usd == pytest.approx(0.10)
    assert budget.cached_calls == 1
    assert budget.live_calls == 1
    assert budget.remaining_usd == pytest.approx(0.40)


def test_refuse_when_over_cap() -> None:
    budget = DailyBudget(limit_usd=0.05)
    budget.record(0.04, source="a", provider="groq")
    with pytest.raises(BudgetExceeded, match="PODLY_DAILY_BUDGET"):
        budget.refuse_if_over(0.02, "b")
    assert budget.skipped_live_calls == 1
    assert budget.spent_usd == pytest.approx(0.04)


def test_zero_budget_blocks_live(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PODLY_DAILY_BUDGET", "0")
    path = tmp_path / "spend.json"
    budget = load_budget(path)
    assert budget.limit_usd == 0.0
    assert budget.can_afford(0.01) is False
    write_budget(budget, path)
    reloaded = load_budget(path)
    assert reloaded.limit_usd == 0.0


def test_estimate_usd_positive_for_tokens() -> None:
    usd = estimate_usd(1_000_000, 0, provider="gemini")
    assert usd > 0


def test_gemini_prices_follow_configured_model() -> None:
    from podcast_processor.experiments.cost_model import (
        DEFAULT_GEMINI_PRICES,
        gemini_prices_for_model,
    )
    from podcast_processor.experiments.gemini_confirm import DEFAULT_GEMINI_MODEL

    lite = gemini_prices_for_model("gemini/gemini-3.1-flash-lite")
    flash = gemini_prices_for_model("gemini/gemini-2.5-flash")
    assert DEFAULT_GEMINI_MODEL == "gemini/gemini-3.1-flash-lite"
    assert lite.input_usd_per_million == pytest.approx(0.25)
    assert lite.output_usd_per_million == pytest.approx(1.50)
    assert flash.input_usd_per_million == pytest.approx(0.15)
    assert flash.output_usd_per_million == pytest.approx(0.60)
    assert DEFAULT_GEMINI_PRICES.name == "gemini/gemini-2.5-flash"
    assert estimate_usd(
        1_000_000, 0, provider="gemini", model="gemini/gemini-3.1-flash-lite"
    ) == pytest.approx(0.25)
    assert estimate_usd(
        1_000_000, 0, provider="gemini", model="gemini/gemini-2.5-flash"
    ) == pytest.approx(0.15)
    assert gemini_prices_for_model(DEFAULT_GEMINI_MODEL).name == lite.name
