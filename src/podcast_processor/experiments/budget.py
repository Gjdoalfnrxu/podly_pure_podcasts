"""Hard daily spend cap for optional live Groq/Gemini confirm calls.

Offline eval never spends. Live calls require explicit env flags, a remaining
budget, and a disk cache (repeat prompts are free).

Env:
  PODLY_DAILY_BUDGET     default 0.50 USD
  PODLY_EXPERIMENT_CACHE_DIR   confirm JSON cache (default under runs/.cache)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from podcast_processor.experiments.cost_model import (
    DEFAULT_CLASSIFIER_PRICES,
    DEFAULT_GEMINI_PRICES,
    usd_for_tokens,
)

BUDGET_ENV = "PODLY_DAILY_BUDGET"
CACHE_DIR_ENV = "PODLY_EXPERIMENT_CACHE_DIR"
DEFAULT_DAILY_BUDGET_USD = 0.50
DEFAULT_CACHE_DIR = Path("docs/experiments/runs/.cache")


class BudgetExceeded(RuntimeError):
    """Raised when a live call would exceed the remaining daily budget."""


def daily_budget_usd() -> float:
    raw = os.environ.get(BUDGET_ENV, str(DEFAULT_DAILY_BUDGET_USD)).strip()
    if not raw:
        return DEFAULT_DAILY_BUDGET_USD
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{BUDGET_ENV} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{BUDGET_ENV} must be >= 0, got {value}")
    return value


def default_cache_dir() -> Path:
    override = os.environ.get(CACHE_DIR_ENV, "").strip()
    return Path(override) if override else DEFAULT_CACHE_DIR


def estimate_usd(
    input_tokens: int,
    output_tokens: int,
    *,
    provider: str = "gemini",
) -> float:
    prices = DEFAULT_GEMINI_PRICES if provider != "groq" else DEFAULT_CLASSIFIER_PRICES
    return usd_for_tokens(input_tokens, output_tokens, prices)


@dataclass
class BudgetEvent:
    usd: float
    source: str
    cached: bool
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    skipped: bool = False
    reason: str = ""


@dataclass
class DailyBudget:
    """UTC-day spend ledger. Cache hits record $0 and do not consume budget."""

    limit_usd: float = DEFAULT_DAILY_BUDGET_USD
    spent_usd: float = 0.0
    date: str = field(default_factory=lambda: datetime.now(UTC).date().isoformat())
    events: list[BudgetEvent] = field(default_factory=list)
    skipped_live_calls: int = 0
    cached_calls: int = 0
    live_calls: int = 0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def can_afford(self, usd: float) -> bool:
        if usd <= 0:
            return True
        return self.spent_usd + usd <= self.limit_usd + 1e-12

    def record(
        self,
        usd: float,
        *,
        source: str,
        cached: bool = False,
        provider: str = "mock",
        input_tokens: int = 0,
        output_tokens: int = 0,
        skipped: bool = False,
        reason: str = "",
    ) -> BudgetEvent:
        event = BudgetEvent(
            usd=0.0 if cached or skipped else float(usd),
            source=source,
            cached=cached,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            skipped=skipped,
            reason=reason,
        )
        if skipped:
            self.skipped_live_calls += 1
        elif cached:
            self.cached_calls += 1
        else:
            self.spent_usd += event.usd
            self.live_calls += 1
        self.events.append(event)
        return event

    def refuse_if_over(self, usd: float, source: str) -> None:
        if not self.can_afford(usd):
            self.record(
                usd,
                source=source,
                skipped=True,
                reason=(
                    f"would spend ${usd:.4f} with ${self.remaining_usd:.4f} "
                    f"remaining of ${self.limit_usd:.2f}"
                ),
            )
            raise BudgetExceeded(
                f"PODLY_DAILY_BUDGET ${self.limit_usd:.2f} exhausted "
                f"(spent ${self.spent_usd:.4f}, need ${usd:.4f} for {source})"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "limit_usd": self.limit_usd,
            "spent_usd": self.spent_usd,
            "remaining_usd": self.remaining_usd,
            "live_calls": self.live_calls,
            "cached_calls": self.cached_calls,
            "skipped_live_calls": self.skipped_live_calls,
            "events": [
                {
                    "usd": event.usd,
                    "source": event.source,
                    "cached": event.cached,
                    "provider": event.provider,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "skipped": event.skipped,
                    "reason": event.reason,
                }
                for event in self.events
            ],
        }


def load_budget(path: Path, *, limit_usd: float | None = None) -> DailyBudget:
    cap = daily_budget_usd() if limit_usd is None else limit_usd
    if not path.exists():
        return DailyBudget(limit_usd=cap)
    payload = json.loads(path.read_text(encoding="utf-8"))
    budget = DailyBudget(
        limit_usd=float(payload.get("limit_usd", cap)),
        spent_usd=float(payload.get("spent_usd", 0.0)),
        date=str(payload.get("date") or datetime.now(UTC).date().isoformat()),
        skipped_live_calls=int(payload.get("skipped_live_calls", 0)),
        cached_calls=int(payload.get("cached_calls", 0)),
        live_calls=int(payload.get("live_calls", 0)),
    )
    # A new CLI invocation may raise the cap via env; never lower a recorded cap
    # below already-spent, but always honor a lower env cap going forward.
    budget.limit_usd = cap
    return budget


def write_budget(budget: DailyBudget, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(budget.to_json(), indent=2) + "\n", encoding="utf-8")
    return path
