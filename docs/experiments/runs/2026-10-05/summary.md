# Daily experiment run 2026-10-05

Generated at `2026-10-05T15:52:27.058534+00:00`. Mode: **live** (provider `gemini`, model `gemini/gemini-3.1-flash-lite`) on a separate machine. This agent recorded the ledger offline from those artifacts. Production flag `enable_bow_scout_gemini_confirm`: `false`. `gates.json`: **not loosened**.

- Daily budget: `$0.50`. `spend.json` shows `$0.0000` / `live_calls=0` because live confirms were not wired into `DailyBudget.record` (accounting bug; ~72 new Gemini cache entries were written). Fixed in code this day; do not treat the zeros as actual spend.
- Baseline gates: **passed**
- Folds this run: **none**. H011 fold held. H012 no_win.

## Hypotheses this run

| ID | Status | Verdict | Fold-eligible | Notes |
| --- | --- | --- | --- | --- |
| `H011` | measured | fold_eligible | true (held) | Live confirm F1/recall/hit **1.0**, TightPromo scout tokens **5429→4744**. Do **not** fold: 2026-10-02 live real episodes dropped Soft Skills 531 ~1927–1962s that Gemini judged `is_ad` (`educational/self_promo`). Pending policy on self/network/membership promos. |
| `H012` | measured | no_win | false | Mock `--check` said pad 8/1 (5043 tok) fold_eligible. **Live** pad 8/1 fails `scout_confirm_mean_time_recall` **0.862** and `scout_confirm_mean_time_f1` **0.915**. Survivors do not beat frozen recommended **0.5/12s/2**. |

accepted = fold-eligible on the experiment package only. Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.

## Next open (ranked)

`H013` (confidence: require live-confirm gate pass before any cost hypothesis is fold-eligible). `H014` is **blocked** on the self-promo policy decision (not fold-ready).
