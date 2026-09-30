#!/usr/bin/env python3
"""Run the offline bow-scout + Gemini-confirm eval and write RESULTS.md.

Does not call Gemini. Safe without API keys.

  PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from podcast_processor.experiments.eval_harness import evaluate_all, write_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/experiments/bow_scout_gemini_confirm"),
        help="Directory for RESULTS.md, metrics.json, and fixture JSON dumps",
    )
    args = parser.parse_args()
    results = evaluate_all()
    results_path, metrics_path = write_artifacts(results, args.output_dir)
    macro = results["recommended"]["macro"]
    print(f"Wrote {results_path}")
    print(f"Wrote {metrics_path}")
    print(
        "macro hit={hit:.3f} coverage={cov:.3f} tok_reduction={red:.1f}%".format(
            hit=macro["mean_ad_hit_rate"],
            cov=macro["mean_ad_coverage"],
            red=macro["mean_token_reduction_pct"],
        )
    )


if __name__ == "__main__":
    main()
