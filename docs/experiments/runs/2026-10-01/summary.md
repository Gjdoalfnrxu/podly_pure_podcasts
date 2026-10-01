# Daily experiment run 2026-10-01

Generated at `2026-10-01T15:59:41.784502+00:00`. Mode: **offline** (provider `mock`). Live Groq/Gemini **skipped** (`GROQ_API_KEY` / `GEMINI_API_KEY` not in environment).

- Daily budget: `$0.50` (spent `$0.0000`)
- Production flag `enable_bow_scout_gemini_confirm`: `false`
- Feed default strategy: `llm`
- Baseline gates (pre-fold, 9-fixture snapshot): **passed**
- `gates.json`: **not loosened**
- Fold: **H005 corpus promotion** (`soft_skills_style_interview` into corpus v1 + snapshot). TightPromo stays experiment-only.

## Spend

| Source | USD |
| --- | ---: |
| Groq | $0.0000 (no key) |
| Gemini | $0.0000 (no key) |
| **Session total (billable)** | **$0.0000** |

## Hypotheses this run

| ID | Status | Verdict | Fold-eligible | Notes |
| --- | --- | --- | --- | --- |
| `H005` | accepted | fold_eligible | true | Style golden: recommended 2 windows → TightPromo 1; labeled-ad recall 1.0. Promoted synthetic builder to corpus v1. |

Seeded but not the day's pick: `H006` (news-briefing style golden), `H007` (duration-gated cue-sparse recovery), `H008` (pad=12/2 micro-sweep). `--check` showed all three gate-passing / fold-eligible on the 9-fixture snapshot; they remain **open** for a later dedicated run against the new 10-fixture snapshot.

## H005 style fixture (offline oracle)

| Detector | Windows | Hit | Confirm recall | Scout tok | Scout precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| recommended CueDetector | 2 | 1.0 | 1.0 | 705 | 0.111 |
| TightPromoCueDetector | 1 | 1.0 | 1.0 | 325 | 0.286 |

Residual strong-cue rate unchanged (0.0208) because leftover scan uses production CueDetector, not TightPromo. Confidence win is the dropped tech-speech window.

## Frozen 9-fixture macros (H005 eval, pre-promotion)

| Metric | Value |
| --- | ---: |
| `scout_confirm_mean_time_f1` | 0.8889 |
| `scout_confirm_mean_time_recall` | 0.8889 |
| `scout_confirm_mean_time_precision` | 1.0000 |
| `scout_mean_ad_hit_rate` | 0.8889 |
| `scout_mean_false_negative_rate` | 0.1111 |
| `mean_token_reduction_pct` | 93.32 |
| `sum_scout_input_tokens` | 3901.00 |
| `scout_confirm_mean_residual_strong_cue_rate` | 0.0039 |

TightPromo on frozen v1 matched these macros (same as H001 offline `no_win`); movement is on the new style golden.

## Snapshot after H005 fold (n=10, recommended config)

| Metric | Pre (n=9) | Post (n=10) |
| --- | ---: | ---: |
| confirm F1 / recall / hit | 0.8889 | 0.9000 |
| confirm precision | 1.0000 | 1.0000 |
| FN rate | 0.1111 | 0.1000 |
| residual strong-cue rate | 0.0039 | 0.0056 |
| token↓ % | 93.32 | 92.80 |
| scout tokens | 3901 | 4606 |

Mean recall rose because the new fixture is a labeled-ad hit; residual rose because unlabeled `code <word>` leftovers are now in the mean. Tolerances in `gates.json` are unchanged. Production flag still `False`.

## Next open (ranked)

`H006`, `H007`, `H008`

accepted = fold-eligible on the experiment package only. Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.
