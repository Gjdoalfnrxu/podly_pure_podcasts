#!/usr/bin/env python3
"""Daily hypothesis → experiment → gate loop.

Offline by default (no API keys). Does not open a PR, does not flip
production AdClassifier defaults, and does not loosen frozen gates.

  PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py
  PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --check
  PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --hypothesis H001
  PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --ingest path.json \\
      --source-style news_briefing --staging-dir docs/experiments/golden/staging

Live confirm (spends money, cache-first):

  export PODLY_DAILY_BUDGET=0.50
  export GEMINI_API_KEY=...          # and/or GROQ_API_KEY
  # Cloud Agents live runs: GROQ_KEY is accepted as an alias of GROQ_API_KEY
  # (canonical GROQ_API_KEY wins if both are set).
  export GROQ_KEY=...                # optional alias for GROQ_API_KEY
  export PODLY_GEMINI_CONFIRM_LIVE=true   # and/or PODLY_GROQ_CONFIRM_LIVE=true
  PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --live
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from podcast_processor.experiments.budget import default_cache_dir
from podcast_processor.experiments.daily_loop import DEFAULT_RUNS_DIR, run_daily_loop
from podcast_processor.experiments.golden_ingest import ingest_golden
from podcast_processor.experiments.hypothesis_ledger import DEFAULT_LEDGER_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode: offline eval + ledger validate; do not update hypotheses.json",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Force mock confirm even if live env flags are set",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Allow Groq/Gemini confirm when env flags + budget remain",
    )
    parser.add_argument(
        "--hypothesis",
        action="append",
        dest="hypotheses",
        default=None,
        help="Run this hypothesis id (repeatable). Default: all ranked open ids",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of ranked open hypotheses to run",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Run date YYYY-MM-DD (default: UTC today)",
    )
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=DEFAULT_LEDGER_DIR,
        help="Directory with hypotheses.json",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Parent directory for dated run artifacts",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Disk cache for confirm prompts (default: docs/experiments/runs/.cache)",
    )
    parser.add_argument(
        "--ingest",
        type=Path,
        default=None,
        help="Validate and stage a labeled transcript JSON (no API calls)",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=Path("docs/experiments/golden/staging"),
        help="Destination for --ingest (gitignored)",
    )
    parser.add_argument(
        "--source-style",
        default="unspecified",
        help="Label for --ingest (e.g. news_briefing, interview)",
    )
    args = parser.parse_args()

    if args.ingest is not None:
        report = ingest_golden(
            args.ingest, args.staging_dir, source_style=args.source_style
        )
        print(
            json.dumps(
                {
                    "fixture_id": report.fixture_id,
                    "n_segments": report.n_segments,
                    "n_labeled_ads": report.n_labeled_ads,
                    "sha256": report.sha256,
                    "output_path": str(report.output_path),
                    "source_style": report.source_style,
                    "promoted_to_corpus": False,
                },
                indent=2,
            )
        )
        print(
            "Staged only. Promotion still requires "
            "run_bow_scout_eval.py --write-corpus --update-baseline "
            "after human review. Never commit API keys."
        )
        return 0

    offline = True
    if args.live and not args.check:
        offline = False
    if args.offline:
        offline = True

    summary = run_daily_loop(
        ledger_root=args.ledger_dir,
        runs_dir=args.runs_dir,
        run_date=args.date,
        offline=offline,
        limit=args.limit,
        hypothesis_ids=args.hypotheses,
        update_ledger=not args.check,
        cache_dir=args.cache_dir or default_cache_dir(),
    )
    print(json.dumps({k: summary[k] for k in summary if k != "baseline"}, indent=2))
    print(f"Wrote {summary['run_dir']}")
    print(summary["baseline"]["ranking_line"])
    if summary["production"]["enable_bow_scout_gemini_confirm"]:
        print("ERROR: production scout flag is on; this is a contract violation.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
