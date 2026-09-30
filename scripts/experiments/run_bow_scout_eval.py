#!/usr/bin/env python3
"""Run the offline bow-scout vs production-like eval.

Does not call Gemini unless both GEMINI_API_KEY and
PODLY_GEMINI_CONFIRM_LIVE=true are set.

  PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py
  PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py --check-baseline
  PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py --update-baseline
  PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py --write-corpus
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from podcast_processor.experiments.baseline import (
    DEFAULT_BASELINE_DIR,
    compare_to_snapshot,
    format_gate_failures,
    write_snapshot,
)
from podcast_processor.experiments.eval_harness import evaluate_all, write_artifacts
from podcast_processor.experiments.fixtures import write_corpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/experiments/bow_scout_gemini_confirm"),
        help="Directory for RESULTS.md, metrics.json, and fixture JSON dumps",
    )
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=DEFAULT_BASELINE_DIR,
        help="Directory for frozen snapshot.json + gates.json",
    )
    parser.add_argument(
        "--write-corpus",
        action="store_true",
        help="Regenerate src/podcast_processor/experiments/corpus/v1 from builders",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the frozen snapshot after a deliberate corpus/harness change",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="Exit 1 if eval regresses vs the frozen snapshot (CI --eval)",
    )
    parser.add_argument(
        "--skip-artifacts",
        action="store_true",
        help="Do not rewrite RESULTS.md / metrics.json",
    )
    args = parser.parse_args()

    if args.write_corpus:
        manifest = write_corpus()
        print(f"Wrote corpus {manifest}")

    results = evaluate_all()
    if not args.skip_artifacts:
        results_path, metrics_path = write_artifacts(results, args.output_dir)
        print(f"Wrote {results_path}")
        print(f"Wrote {metrics_path}")

    if args.update_baseline:
        snap = write_snapshot(results, args.baseline_dir)
        print(f"Updated baseline snapshot {snap}")

    macro = results["recommended"]["macro"]
    print(
        "macro scout_hit={hit:.3f} scout_confirm_recall={rec:.3f} "
        "prod_recall={prod:.3f} tok_reduction={red:.1f}%".format(
            hit=macro["scout_mean_ad_hit_rate"],
            rec=macro["scout_confirm_mean_time_recall"],
            prod=macro["production_mean_time_recall"],
            red=macro["mean_token_reduction_pct"],
        )
    )

    if args.check_baseline:
        failures = compare_to_snapshot(results, root=args.baseline_dir)
        print(format_gate_failures(failures))
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
