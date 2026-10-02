#!/usr/bin/env python3
"""Fully automated gold baseline for podcast ad-removal eval.

Independent of production AdClassifier. Locked gold family:
general_podcast_ads. Sampling unit = show.

Does not set enable_bow_scout_gemini_confirm. GROQ_KEY is unused; Gemini
judge uses GEMINI_API_KEY / GEMINI_KEY / GOOGLE_API_KEY /
GOOGLE_GENERATIVE_AI_API_KEY or dry-runs.

  PYTHONPATH=src uv run python scripts/experiments/run_auto_gold_baseline.py
  PYTHONPATH=src uv run python scripts/experiments/run_auto_gold_baseline.py --offline
  PYTHONPATH=src uv run python scripts/experiments/run_auto_gold_baseline.py --download \\
      --enable-dsp --enable-fingerprint --whisper-mode local --judge-mode auto
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from podcast_processor.experiments.auto_gold.pipeline import (
    AutoGoldConfig,
    run_auto_gold,
)
from podcast_processor.experiments.auto_gold.shows import default_shows_path
from shared import defaults as DEFAULTS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Run directory (default docs/experiments/auto_gold/runs/<utc-date>)",
    )
    parser.add_argument(
        "--shows",
        type=Path,
        default=None,
        help="shows.json path (default docs/experiments/auto_gold/shows.json)",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download 1 recent episode per show (large). Default is RSS-only.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="No network: validate the catalog and fill the report with skips.",
    )
    parser.add_argument(
        "--whisper-mode",
        choices=("auto", "local", "stub"),
        default="auto",
        help="auto uses local Whisper when importable, else stubs (Cloud VM).",
    )
    parser.add_argument(
        "--judge-mode",
        choices=("auto", "gemini", "dry-run"),
        default="auto",
        help="auto uses Gemini/Google key if present, else dry-run. Never GROQ_KEY.",
    )
    parser.add_argument(
        "--enable-dsp",
        action="store_true",
        help="Optional ffmpeg silencedetect candidates (needs --download).",
    )
    parser.add_argument(
        "--enable-fingerprint",
        action="store_true",
        help="Optional preroll near-dupe fingerprint (needs --download).",
    )
    parser.add_argument(
        "--no-dai-probes",
        action="store_true",
        help="Do not add 33%/66% duration probes on DAI hosts.",
    )
    parser.add_argument(
        "--genre",
        action="append",
        dest="genres",
        default=None,
        help="Restrict genres (repeatable). Disables representative check.",
    )
    parser.add_argument(
        "--show-id",
        action="append",
        dest="show_ids",
        default=None,
        help="Restrict to these show ids (repeatable). Disables representative check.",
    )
    parser.add_argument(
        "--max-download-mb",
        type=float,
        default=None,
        help="Abort an episode download above this many megabytes.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM:
        print(
            "enable_bow_scout_gemini_confirm is unexpectedly true; "
            "refusing to run (production flag must stay false).",
            file=sys.stderr,
        )
        return 2

    date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
    output_dir = args.output_dir or Path("docs/experiments/auto_gold/runs") / date_dir
    filtered = bool(args.genres or args.show_ids)
    max_bytes = None
    if args.max_download_mb is not None:
        max_bytes = int(args.max_download_mb * 1024 * 1024)

    result = run_auto_gold(
        AutoGoldConfig(
            output_dir=output_dir,
            shows_path=args.shows or default_shows_path(),
            download=args.download,
            offline=args.offline,
            whisper_mode=args.whisper_mode,
            judge_mode=args.judge_mode,
            enable_dsp=args.enable_dsp,
            enable_fingerprint=args.enable_fingerprint,
            include_dai_probes=not args.no_dai_probes,
            max_download_bytes=max_bytes,
            genres=set(args.genres) if args.genres else None,
            show_ids=set(args.show_ids) if args.show_ids else None,
            require_representative=not filtered,
        )
    )
    print(
        json.dumps(
            {
                "gold_family": result.gold_family,
                "n_shows": len(result.shows),
                "genres": sorted({row.show.genre for row in result.shows}),
                "whisper": result.whisper_backend,
                "judge": result.judge_mode,
                "gemini_key": result.gemini_key_present,
                "groq_unused": result.groq_key_present_but_unused,
                "blocked": result.blocked_steps,
                "output_dir": str(output_dir),
                "report": str(output_dir / "BASELINE_REPORT.md"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
