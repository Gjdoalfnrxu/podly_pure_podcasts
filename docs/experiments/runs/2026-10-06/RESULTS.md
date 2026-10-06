# 2026-10-06 daily loop

Live H013 ran elsewhere (`run_daily_loop.py --live`, gemini): **0 live calls, $0**, measured/`process_ok`. Frozen recommended F1/recall/hit **1.0**, scout tokens **5429**. Production `enable_bow_scout_gemini_confirm` stayed **false**. `gates.json` not loosened.

This agent (offline):

1. Recorded H013 and implemented `cost_fold_eligible` so a mock-only cost win can never be fold-eligible (unit-tested).
2. Resolved H014: self-promo / network / first-party app plugs / membership asks **count as ads**.
3. Rejected H011 (TightPromo). The Soft Skills 531 ~1927–1962s window Gemini judged `is_ad` (`educational/self_promo`) is a miss under that policy.
4. Did **not** fold. Did **not** rewrite `snapshot.json`. Eval still treats `self_promo_vs_sponsor` first-party lines as unlabeled; that gap is H015 (not a silent baseline change).
5. Seeded H015 (confidence), H016 (detection, t=0 preroll), H017 (detection, NPR underwriting).
