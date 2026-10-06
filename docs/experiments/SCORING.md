# Confidence-first ranking

User priorities are **absolute** and never inverted: (1) high confidence /
no regressions, (2) ad detection quality, (3) spend reduction.

Implemented in `src/podcast_processor/experiments/scorer.py`. Used by
`scripts/experiments/run_daily_loop.py`. Frozen ε lives in
`docs/experiments/bow_scout_gemini_confirm/baseline/v1/gates.json`.

## Function

Given candidate eval payloads in the same shape as `evaluate_all()`:

1. **Reject** any candidate for which `compare_to_snapshot` returns a
   non-empty failure list. Rejected candidates are not fold-eligible.
   Do **not** loosen `gates.json` to make them pass.
2. Among **survivors**, sort lexicographically by this key (smaller is
   better after negation):

   ```
   (
     -scout_confirm_mean_time_f1,
     -scout_confirm_mean_time_recall,
     -scout_mean_ad_hit_rate,
     -mean_token_reduction_pct,
     sum_scout_input_tokens,
   )
   ```

   So: maximize confirm-path time F1, then recall, then ad-block hit
   rate; then maximize token reduction vs the full AdClassifier walk;
   then prefer fewer scout+confirm input tokens.
3. If the survivor set is empty, keep the frozen recommended scout
   config. Do not fold. Do not update `snapshot.json`.

## Fold vs baseline update

| Action | When it is allowed |
| --- | --- |
| Fold into experiment package (`RECOMMENDED_CONFIG`, candidate detector) | Survivor exists, primary metric improved, production flag still `False`. **Cost** folds also need a live Gemini/Groq confirm pass (H013); a mock-only cost win is never fold-eligible. |
| Flip `enable_bow_scout_gemini_confirm` / Feed strategy | Never from the daily loop |
| Rewrite `snapshot.json` / corpus v1 | Explicit `--write-corpus --update-baseline` plus RESULTS notes |
| Loosen `gates.json` tolerances | Never |

H012 showed why the cost live-confirm gate exists: mock pad 8/1 passed frozen ε (5043 tok) while live recall 0.862 / F1 0.915 failed. The daily loop (`cost_fold_eligible`) therefore keeps mock-only cost survivors at `measured` / `no_win` until `--live` confirm also passes.

The scorer does not mutate production `CueDetector` or `AdClassifier`.
