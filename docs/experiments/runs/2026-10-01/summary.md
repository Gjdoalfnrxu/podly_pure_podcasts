# Daily experiment run 2026-10-01

Generated at `2026-10-01T17:13:31.811413+00:00`. Mode: **offline** (provider `mock`) on this Cloud Agent. Live Groq skipped (Cloudflare 1010). Gemini live catch-up ran on a different machine (tip `ade73440e24f18bba300eb5a860910cb494f0936`; box spend **$0.0049** Gemini, Whisper $0).

- Daily budget: `$0.50` (this offline session spent `$0.0000`)
- Production flag `enable_bow_scout_gemini_confirm`: `false`
- Feed default strategy: `llm`
- Baseline gates (post-H008): **passed**
- `gates.json`: **not loosened**
- Folds: **H005** (morning corpus), **H006** (afternoon corpus), **H007** (probe), **H008** (pad). TightPromo stays experiment-only.

## Spend

| Source | USD |
| --- | ---: |
| Groq (this session) | $0.0000 (not called) |
| Gemini (this session) | $0.0000 (not called) |
| Gemini (box live catch-up) | $0.0049 |
| **Session total (this agent, billable)** | **$0.0000** |

## Hypotheses this run

| ID | Status | Verdict | Fold-eligible | Notes |
| --- | --- | --- | --- | --- |
| `H005` | accepted | fold_eligible | true | Morning: Soft Skills-style golden into corpus v1. |
| `H006` | accepted | fold_eligible | true | Afternoon: news-briefing-style golden into corpus v1. Live 2→1; Gemini `technical_discussion`. |
| `H007` | accepted | fold_eligible | true | Duration-gated 40s probe in eval recommended path. Cue-sparse hit 0→1. |
| `H008` | accepted | fold_eligible | true | Recommended pad 12s/2. Ties detection; scout tokens 5698→5337. |

Seeded for next loop: `H010` (TightPromo as eval detector), `H009` (wider duration-gated probe). `--check` on the post-H008 snapshot (ledger not updated): `H010` measured/`no_win` (gates green; TightPromo tokens 4652 vs 5337 but residual/precision unchanged), `H009` fold_eligible (confirm F1/recall/hit **1.0**, scout tokens 5429 vs 5337, still inside +10%). Left **open** for the next dedicated fold.

## Frozen macros vs this afternoon

| Metric | Pre-afternoon (n=10, post-H005) | After H006 (n=11) | After H007 (probe) | After H008 (pad 12/2) |
| --- | ---: | ---: | ---: | ---: |
| confirm F1 | 0.9000 | 0.9091 | 0.9752 | 0.9752 |
| confirm recall | 0.9000 | 0.9091 | 0.9610 | 0.9610 |
| ad-block hit | 0.9000 | 0.9091 | 1.0000 | 1.0000 |
| FN rate | 0.1000 | 0.0909 | 0.0000 | 0.0000 |
| residual | 0.0056 | 0.0066 | 0.0066 | 0.0066 |
| token↓ % | 92.80 | 91.84 | 91.48 | 92.05 |
| scout tokens | 4606 | 5352 | 5698 | 5337 |

Cue-sparse time coverage is still 57.1% (40s probe vs 35s label). Production flag still `False`.

## Next open (ranked)

`H010`, `H009`

accepted = fold-eligible on the experiment package only. Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.
