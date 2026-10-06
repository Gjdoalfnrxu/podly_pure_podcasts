# Daily experiment run 2026-10-06

Generated at `2026-10-06T15:59:04.098029+00:00`. Live H013 ran on a separate machine (provider `gemini`, 0 confirm calls, **$0**). This agent recorded that result and applied the offline policy follow-up. Production flag `enable_bow_scout_gemini_confirm`: `false`. `gates.json`: **not loosened**. Snapshot: **not rewritten**.

- Daily budget: `$0.50` (spent `$0.0000`)
- Baseline gates: **passed** (frozen recommended F1/recall/hit **1.0**, scout tokens **5429**)
- Folds this run: **none**

## Hypotheses this run

| ID | Status | Verdict | Fold-eligible | Notes |
| --- | --- | --- | --- | --- |
| `H013` | measured | process_ok | false | Live-confirm-required-for-cost-fold is now enforced in `daily_loop.cost_fold_eligible`. A mock-only cost win can never be fold-eligible. |
| `H014` | measured | process_ok | false | Policy: self-promo, network/sister-show promos, first-party app plugs, membership/donation asks **count as ads** and should be cut. |
| `H011` | rejected | rejected | false | TightPromo drops Soft Skills 531 ~1927–1962s (Gemini `is_ad` / `educational/self_promo`). That is now a miss. Do **not** fold TightPromo. |

accepted = fold-eligible on the experiment package only. Cost hypotheses are never fold-eligible from a mock-only confirm run (H013: live Gemini confirm required). Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.

## Next open (ranked)

`H015` (confidence: align eval labels with the self-promo-as-ad policy). `H016` (detection: t=0 preroll). `H017` (detection: NPR membership/underwriting).
