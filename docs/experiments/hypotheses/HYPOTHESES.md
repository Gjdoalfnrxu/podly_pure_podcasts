# Hypothesis ledger

Machine-readable source: `docs/experiments/hypotheses/hypotheses.json`.
User priorities (absolute): **confidence** (no regressions) → **detection** (F1/recall) → **cost** (token reduction).

The daily runner loads **open** rows in that order, runs offline eval vs the frozen snapshot by default, and never loosens `gates.json`.

Updated at `2026-10-06T15:59:04.098029+00:00` (UTC).

| ID | Primary | Status | Statement | Last result |
| --- | --- | --- | --- | --- |
| `H001` | confidence | measured | Tighten CueDetector promo so bare `code <word>` (tech speech: code review, code path) is not a strong cue, while `use code SAVE20` / `promo CODE` still flag real ads. | no_win, 2026-09-30 (`docs/experiments/runs/2026-09-30/H001.json`) |
| `H002` | detection | rejected | Recover cue-sparse host-reads (Away-style brand story, no URL/CTA/phone) without paying a full AdClassifier walk. | failed_gates, 2026-09-30 (`docs/experiments/runs/2026-09-30/H002.json`) |
| `H003` | cost | measured | Re-rank pad/threshold/extras sweeps under frozen gate constraints; keep recommended config unless a survivor strictly beats it on detection then cost. | no_win, 2026-09-30 (`docs/experiments/runs/2026-09-30/H003.json`) |
| `H004` | confidence | measured | Add a secret-safe process for real-transcript goldens in The Daily / Soft Skills style (news briefing + interview host-reads) without leaking API keys into git. | process_ok, 2026-09-30 (`docs/experiments/runs/2026-09-30/H004.json`) |
| `H005` | confidence | accepted | Promote Soft Skills-style synthetic goldens (H004 templates, not copyrighted episode text) into corpus builders so TightPromo vs recommended shows offline confidence movement: fewer promo FPs on unlabeled `code <word>` tech speech, no labeled-ad recall drop. | fold_eligible, 2026-10-01 (`docs/experiments/runs/2026-10-01/H005.json`) |
| `H006` | confidence | accepted | Add a news-briefing-style golden with side-by-side unlabeled bare `code <word>` tech speech and a labeled `use code SAVE…` / promo CTA so the H001 TightPromo detector can be measured offline on Daily-style structure. | fold_eligible, 2026-10-01 (`docs/experiments/runs/2026-10-01/H006.json`) |
| `H007` | detection | accepted | Recover cue-sparse host-reads with a duration-gated midroll probe that stays within +10% of snapshot scout tokens=3901 (limit ~4291), cheaper than H002 storytelling/midroll-probe. | fold_eligible, 2026-10-01 (`docs/experiments/runs/2026-10-01/H007.json`) |
| `H008` | cost | accepted | Micro-variant pad/threshold sweep (t=0.45 pad=15/3 extras; t=0.5 pad=12/2 extras) under frozen gates; keep recommended unless a survivor strictly beats F1 then recall then tokens. | fold_eligible, 2026-10-01 (`docs/experiments/runs/2026-10-01/H008.json`) |
| `H009` | detection | accepted | Widen the duration-gated midroll probe from 40s (H007 half_window=20) to 70s (half_window=35) so cue_sparse_storytelling time recall rises from 0.571 toward 1.0 without exceeding +10% of snapshot scout tokens (5337, limit ~5871), still skipping short ad-free interviews. | fold_eligible, 2026-10-02 (`docs/experiments/runs/2026-10-02/H009.json`) |
| `H010` | confidence | measured | Use TightPromoCueDetector as the experiment-package recommended scout detector now that both Soft Skills-style and news-briefing-style goldens are in corpus v1, so unlabeled `code <word>` tech-speech FPs drop offline while labeled use-code ads stay hits. | no_win, 2026-10-02 (`docs/experiments/runs/2026-10-02/H010.json`) |
| `H011` | cost | rejected | Use TightPromoCueDetector as the experiment-package recommended scout detector on cost (not confidence): H010 showed residual/precision unchanged, but TightPromo cuts scout tokens with F1/recall/hit tied on corpus v1. | rejected, 2026-10-06 (`docs/experiments/runs/2026-10-06/H011.json`) |
| `H012` | cost | measured | Micro-variant pad/threshold sweep under the post-H009 70s probe (t=0.5 pad=10/2 extras; t=0.5 pad=8/1 extras); keep recommended unless a survivor strictly beats F1 then recall then tokens. | no_win, 2026-10-05 (`docs/experiments/runs/2026-10-05/H012.json`) |
| `H013` | confidence | measured | Require a live Gemini confirm gate pass (not mock-only oracle) before any cost hypothesis is fold-eligible, because H012 mock pad 8/1 passed frozen ε while live recall 0.862 / F1 0.915 failed. | process_ok, 2026-10-06 (`docs/experiments/runs/2026-10-06/H013.json`) |
| `H014` | confidence | measured | Decide whether self/network/membership promos count as ads before any TightPromo cost fold (H011). Live 2026-10-02: Soft Skills 531 window ~1927–1962s dropped by TightPromo was Gemini is_ad / educational/self_promo. | process_ok, 2026-10-06 (`docs/experiments/runs/2026-10-06/H014.json`) |
| `H015` | confidence | open | Align corpus v1 / scout scoring with the H014 policy so self-promo, network/sister-show promos, first-party app plugs, and membership/donation asks are labeled ad-positive (self_promo_vs_sponsor currently labels only the external sponsor; RECOMMENDED_CONFIG.include_self_promo is false). | — |
| `H016` | detection | open | Recover opening/t=0 preroll host-reads the scout misses because cues sit after t=0 or the duration-gated probe only fires midroll; add an offline synthetic golden with a cue-sparse opening read at t=0 so a cheap preroll probe can be scored without live spend. | — |
| `H017` | detection | open | Flag NPR-style membership asks and underwriting reads ('support for this podcast comes from', member-station plugs) as ads under the H014 policy; add an offline synthetic underwriting/membership golden. CueDetector has no membership/underwriting patterns (self_promo is my/our book\|course\|newsletter…; scout extras cover brought to you by but not NPR underwriting). | — |

## Predicted effects

### `H001`

Tighten CueDetector promo so bare `code <word>` (tech speech: code review, code path) is not a strong cue, while `use code SAVE20` / `promo CODE` still flag real ads.

- Primary metric: `confidence`
- Status: `measured`
- Experiment kind: `cue_pattern`
- Confidence: Fewer promo false positives on technical discussion; residual strong-cue rate on false_positive_content should not rise.
- Detection: No labeled-ad recall/hit drop on corpus v1 (ads already say use code / promo).
- Cost: Slightly fewer confirm windows if tech-speech FPs disappear; token reduction should not fall.
- Notes: Experiment-only detector. Do not edit production cue_detector.promo_pattern until this is accepted and gates stay green. Offline corpus v1: measured/no_win (macros identical to frozen recommended). Live 2026-09-30 confidence win: baseline 26 → TightPromo 15 windows (−42.3%) on 4 real eps (Soft Skills 532/531 drop tech-speech FPs; The Daily window counts unchanged). Keep TightPromoCueDetector in candidates.py; mark accepted after Soft Skills-style goldens land and offline confidence metrics move.

### `H002`

Recover cue-sparse host-reads (Away-style brand story, no URL/CTA/phone) without paying a full AdClassifier walk.

- Primary metric: `detection`
- Status: `rejected`
- Experiment kind: `cheap_recovery`
- Confidence: Must still pass frozen ε. Ad-free episodes must not grow residual strong cues.
- Detection: Lift cue_sparse_storytelling ad-block hit/recall from 0 without dropping other fixtures.
- Cost: At most one extra confirm window per miss, not a 60-segment walk; scout tokens must stay within +10% of snapshot.
- Notes: Padding cannot recover an ad the scout never flags. Production CueDetector extras stay off. 2026-09-30: rejected (ε). Storytelling 4338 / midroll-probe 4707 scout tokens vs baseline 3901 (limit 4291). Live skipped. Do not fold. Revisit only with a recovery that stays within +10% scout tokens.

### `H003`

Re-rank pad/threshold/extras sweeps under frozen gate constraints; keep recommended config unless a survivor strictly beats it on detection then cost.

- Primary metric: `cost`
- Status: `measured`
- Experiment kind: `pad_sweep`
- Confidence: Any config that fails snapshot ε is discarded, even if cheaper.
- Detection: Do not trade F1/recall for tokens. Recommended 0.5/15s/3 extras=on should remain best recall on v1.
- Cost: Among survivors, prefer higher mean_token_reduction_pct / fewer scout tokens.
- Notes: Sweep is experiment-package only. Do not change production neighbor window. 2026-09-30: no_win. Survivor remains frozen recommended extras=True t=0.5 pad=15/3. t=0.4 ties; extras=False fails recall/hit/F1 ε.

### `H004`

Add a secret-safe process for real-transcript goldens in The Daily / Soft Skills style (news briefing + interview host-reads) without leaking API keys into git.

- Primary metric: `confidence`
- Status: `measured`
- Experiment kind: `golden_ingest`
- Confidence: Ingest refuses .env, gsk_/AIza/sk- blobs, and api_key fields. Corpus v1 hashes unchanged until an explicit baseline update.
- Detection: New goldens, once promoted, should cover live host-read FP/FN that synthetic v1 misses.
- Cost: No live spend during ingest. Promotion still uses --write-corpus --update-baseline together.
- Notes: Do not commit copyrighted episode text. Staging is gitignored. Never store GROQ_API_KEY or GEMINI_API_KEY next to transcripts. 2026-09-30: process_ok. Templates staged (Soft Skills-style + news-briefing-style); secret rejection works (api_key / AIza… refused). promoted_to_corpus: false. 2026-10-01: follow-up open H005/H006 promote synthetic style goldens (not copyrighted episode text) into corpus builders.

### `H005`

Promote Soft Skills-style synthetic goldens (H004 templates, not copyrighted episode text) into corpus builders so TightPromo vs recommended shows offline confidence movement: fewer promo FPs on unlabeled `code <word>` tech speech, no labeled-ad recall drop.

- Primary metric: `confidence`
- Status: `accepted`
- Experiment kind: `style_golden_promo`
- Confidence: On soft_skills_style_interview, TightPromo drops scout windows on unlabeled code review/path/sample vs production CueDetector, while residual/recall on the labeled use-code preroll stay green.
- Detection: Labeled `use code SOFT20` preroll remains a scout hit for both detectors. Frozen corpus v1 recall/hit/F1 must still pass gates.
- Cost: Fewer confirm windows on tech-speech FPs; scout tokens on frozen v1 should not rise. Corpus promotion is explicit --write-corpus --update-baseline, not an automatic snapshot rewrite.
- Notes: 2026-10-01: accepted. TightPromo 2→1 windows on synthetic soft_skills_style_interview (use code SOFT20 recall 1.0). Folded the synthetic builder into corpus v1 + snapshot; gates.json ε unchanged. Production CueDetector.promo_pattern and enable_bow_scout_gemini_confirm stay off. TightPromo remains experiment-only.

### `H006`

Add a news-briefing-style golden with side-by-side unlabeled bare `code <word>` tech speech and a labeled `use code SAVE…` / promo CTA so the H001 TightPromo detector can be measured offline on Daily-style structure.

- Primary metric: `confidence`
- Status: `accepted`
- Experiment kind: `style_golden_promo`
- Confidence: TightPromo drops windows on unlabeled code review/path while keeping the SAVE50 sponsor window; production CueDetector flags both.
- Detection: Labeled midroll recall/hit stay 1.0 for both detectors. Frozen v1 gates still pass.
- Cost: One fewer confirm window on the briefing fixture. Promotion still requires --write-corpus --update-baseline together.
- Notes: 2026-10-01 afternoon: accepted. TightPromo 2→1 windows on synthetic news_briefing_style_code_cta (use code SAVE50 recall 1.0). Live: Gemini classified the dropped window as technical_discussion; real eps still 26→15 (−42.3%). Folded the synthetic builder into corpus v1 + snapshot; gates.json ε unchanged. Production CueDetector.promo_pattern and enable_bow_scout_gemini_confirm stay off. TightPromo remains experiment-only.

### `H007`

Recover cue-sparse host-reads with a duration-gated midroll probe that stays within +10% of snapshot scout tokens=3901 (limit ~4291), cheaper than H002 storytelling/midroll-probe.

- Primary metric: `detection`
- Status: `accepted`
- Experiment kind: `cheap_recovery`
- Confidence: Must still pass frozen ε. Short ad-free episodes must not gain a probe window.
- Detection: Lift cue_sparse_storytelling ad-block hit/recall from 0 without dropping other fixtures.
- Cost: Skip 10-minute ad_free_interview; use a 40s window on longer empty-scout episodes. Scout tokens must stay ≤4291.
- Notes: 2026-10-01 afternoon: accepted. duration_gated_midroll_probe recovered cue_sparse_storytelling ad-block hit 0→1 (time recall 0.571 on that fixture; macro confirm F1 0.9091→0.9752, hit 0.9091→1.0). Scout tokens 5352→5698 vs post-H006 snapshot (limit 5887; the 4291 figure was the pre-H005 n=9 cap). Folded into eval DEFAULT_WINDOW_POSTPROCESS; snapshot updated; gates.json ε unchanged. Production CueDetector extras stay off.

### `H008`

Micro-variant pad/threshold sweep (t=0.45 pad=15/3 extras; t=0.5 pad=12/2 extras) under frozen gates; keep recommended unless a survivor strictly beats F1 then recall then tokens.

- Primary metric: `cost`
- Status: `accepted`
- Experiment kind: `pad_sweep`
- Confidence: Any config that fails snapshot ε is discarded, even if cheaper.
- Detection: Do not trade F1/recall/hit for tokens. Recommended 0.5/15s/3 extras=on should remain best recall on v1 unless a micro-variant ties detection and spends less.
- Cost: Among survivors, prefer higher mean_token_reduction_pct / fewer scout tokens than 3901.
- Notes: 2026-10-01 afternoon: accepted. extras=True t=0.5 pad=12/2 ties confirm F1/recall/hit vs pad=15/3 after H007 (F1 0.9752, recall 0.9610, hit 1.0) and cuts scout tokens 5698→5337. Folded into RECOMMENDED_CONFIG; snapshot updated; gates.json ε unchanged. Production neighbor window unchanged.

### `H009`

Widen the duration-gated midroll probe from 40s (H007 half_window=20) to 70s (half_window=35) so cue_sparse_storytelling time recall rises from 0.571 toward 1.0 without exceeding +10% of snapshot scout tokens (5337, limit ~5871), still skipping short ad-free interviews.

- Primary metric: `detection`
- Status: `accepted`
- Experiment kind: `cheap_recovery`
- Confidence: Must still pass frozen ε. Short ad-free episodes must not gain a probe window.
- Detection: Lift cue_sparse time recall from 0.571 toward 1.0 without dropping other fixtures' F1/hit.
- Cost: One wider confirm window on long empty-scout episodes. Scout tokens must stay ≤5871.
- Notes: 2026-10-02: accepted and folded. Confirm F1/recall/hit 1.0 (frozen 0.9752/0.9610/1.0), scout tokens 5429 vs 5337 (limit ~5871). Cue-sparse time recall 0.571→1.0; short ad_free_interview stays unprobed. Folded into eval DEFAULT_WINDOW_POSTPROCESS; snapshot updated; gates.json ε unchanged. Production CueDetector extras stay off.

### `H010`

Use TightPromoCueDetector as the experiment-package recommended scout detector now that both Soft Skills-style and news-briefing-style goldens are in corpus v1, so unlabeled `code <word>` tech-speech FPs drop offline while labeled use-code ads stay hits.

- Primary metric: `confidence`
- Status: `measured`
- Experiment kind: `cue_pattern`
- Confidence: Fewer scout windows on unlabeled code-review/path speech on the two style goldens; residual/recall on labeled use-code ads stay green.
- Detection: Labeled-ad F1/recall/hit on frozen v1 must still pass gates.
- Cost: Fewer confirm tokens on tech-speech FPs; scout tokens should not rise.
- Notes: 2026-10-02 proper offline loop: measured/no_win. Gates green (F1 0.9752, recall 0.9610, hit 1.0). TightPromo scout tokens 4652 vs recommended 5337, but residual 0.0066 and confirm precision 1.0 are unchanged vs frozen recommended, so the confidence primary did not win. Do not force-accept. TightPromoCueDetector stays experiment-only. Do not copy TIGHT_PROMO_PATTERN into production CueDetector.promo_pattern. Do not flip enable_bow_scout_gemini_confirm. Cost follow-up is H011.

### `H011`

Use TightPromoCueDetector as the experiment-package recommended scout detector on cost (not confidence): H010 showed residual/precision unchanged, but TightPromo cuts scout tokens with F1/recall/hit tied on corpus v1.

- Primary metric: `cost`
- Status: `rejected`
- Experiment kind: `cue_pattern`
- Confidence: Must still pass frozen ε. Residual/precision may stay tied; do not treat a token-only win as a confidence win.
- Detection: Confirm F1/recall/hit must stay at the post-H009 snapshot (1.0 / 1.0 / 1.0).
- Cost: Fewer confirm tokens on unlabeled tech-speech FPs vs post-H009 scout tokens 5429 (limit ~5972).
- Notes: 2026-10-06: rejected after H014 policy. Live 2026-10-05 Gemini confirm passed frozen ε (F1/recall/hit 1.0, scout tokens 5429→4744) but TightPromo dropped Soft Skills 531 ~1927–1962s that Gemini judged is_ad (educational/self_promo). That window is now a miss. Do not fold TightPromo. 2026-10-05 notes kept for the live numbers. Production CueDetector.promo_pattern and enable_bow_scout_gemini_confirm stay off.

### `H012`

Micro-variant pad/threshold sweep under the post-H009 70s probe (t=0.5 pad=10/2 extras; t=0.5 pad=8/1 extras); keep recommended unless a survivor strictly beats F1 then recall then tokens.

- Primary metric: `cost`
- Status: `measured`
- Experiment kind: `pad_sweep`
- Confidence: Any config that fails snapshot ε is discarded, even if cheaper.
- Detection: Do not trade F1/recall/hit for tokens. Post-H009 recommended 0.5/12s/2 extras=on is F1/recall/hit 1.0 on v1.
- Cost: Among survivors, prefer higher mean_token_reduction_pct / fewer scout tokens than 5429.
- Notes: 2026-10-05: use the LIVE result, not mock. Mock --check marked pad 8/1 (5043 tokens) fold_eligible with F1/recall/hit 1.0. Live Gemini confirm rejected pad 8/1: scout_confirm_mean_time_recall 0.862 (limit 0.98 vs baseline 1.0) and scout_confirm_mean_time_f1 0.915 (limit 0.97). Survivors that still pass live gates (pad 10/2, 15/3, t=0.4 pad 15/3) do not beat frozen recommended 0.5/12s/2 (5429 tokens). measured/no_win. Sweep stays experiment-package only. Do not change production neighbor window.

### `H013`

Require a live Gemini confirm gate pass (not mock-only oracle) before any cost hypothesis is fold-eligible, because H012 mock pad 8/1 passed frozen ε while live recall 0.862 / F1 0.915 failed.

- Primary metric: `confidence`
- Status: `measured`
- Experiment kind: `process_gate`
- Confidence: Stops mock-only cost folds from landing a config that later fails live confirm ε. Does not change frozen gates.json.
- Detection: Live confirm F1/recall/hit remain the detection source of truth for fold decisions; mock ranking stays a cheap screen only.
- Cost: Cost survivors stay measured until a live-confirm run also passes frozen ε. No token-only mock win may fold.
- Notes: 2026-10-06 live (separate machine, gemini, 0 live calls, $0): measured/process_ok. Frozen recommended F1/recall/hit 1.0, scout tokens 5429. Fold policy now actually blocks mock-only cost wins (`cost_fold_eligible` in daily_loop.py; unit-tested). Do not flip enable_bow_scout_gemini_confirm. Do not loosen gates.json. Process hypothesis, not a detector/pad fold.

### `H014`

Decide whether self/network/membership promos count as ads before any TightPromo cost fold (H011). Live 2026-10-02: Soft Skills 531 window ~1927–1962s dropped by TightPromo was Gemini is_ad / educational/self_promo.

- Primary metric: `confidence`
- Status: `measured`
- Experiment kind: `process_gate`
- Confidence: Policy: first-party promo is an ad. TightPromo dropping the Soft Skills 531 self-promo window is a miss, not a confidence win.
- Detection: The dropped Soft Skills 531 ~1927–1962s window is an FN under this policy. Corpus v1 still labels only the external sponsor on self_promo_vs_sponsor; alignment is H015, not a silent snapshot rewrite.
- Cost: H011 token drop 5429→4744 stays recorded but must not fold. TightPromo remains experiment-only.
- Notes: 2026-10-06: resolved. User policy decided 2026-10-05: self-promo, network/sister-show promos, first-party app plugs, and membership/donation asks COUNT AS ADS and should be cut. Therefore H011 is rejected. Eval currently excludes self-promo (include_self_promo=false; self_promo_vs_sponsor labels only Notion). Follow-up is H015, not --update-baseline today. Do not copy TIGHT_PROMO_PATTERN into production. Do not flip enable_bow_scout_gemini_confirm.

### `H015`

Align corpus v1 / scout scoring with the H014 policy so self-promo, network/sister-show promos, first-party app plugs, and membership/donation asks are labeled ad-positive (self_promo_vs_sponsor currently labels only the external sponsor; RECOMMENDED_CONFIG.include_self_promo is false).

- Primary metric: `confidence`
- Status: `open`
- Experiment kind: `eval_alignment`
- Confidence: Stops frozen F1/recall/hit 1.0 from hiding first-party promo misses. Do not silently rewrite snapshot.json; relabel first, measure the miss, then --update-baseline only with RESULTS notes and only if it does not mask a regression.
- Detection: After relabel, scout+confirm recall/hit on first-party promo should be scored. Current goldens treat newsletter/patreon/course lines as content.
- Cost: Labeling extra ad seconds may add confirm windows. Token rise must stay inside frozen +10% ε or the candidate is rejected.
- Notes: Seeded 2026-10-06 after H014. Offline-testable. Do not fold and do not run --update-baseline until a detector change keeps gates green on the relabeled corpus. Production AdClassifier demotion of self-promo is unchanged.

### `H016`

Recover opening/t=0 preroll host-reads the scout misses because cues sit after t=0 or the duration-gated probe only fires midroll; add an offline synthetic golden with a cue-sparse opening read at t=0 so a cheap preroll probe can be scored without live spend.

- Primary metric: `detection`
- Status: `open`
- Experiment kind: `detection_gap`
- Confidence: Must still pass frozen ε. Short ad-free interviews must not gain a t=0 probe window.
- Detection: Lift cue-sparse opening preroll hit/recall from 0 without dropping other fixtures. Related follow-up: ad lead-in boundaries cut late (padding starts after the bumper).
- Cost: At most one extra confirm window on long episodes that currently return empty scout at t=0. Scout tokens must stay within +10% of snapshot 5429.
- Notes: Seeded 2026-10-06. H009's 70s probe is midroll-only and skips short episodes. Offline golden first; do not fold until gates pass. Production CueDetector extras stay off.

### `H017`

Flag NPR-style membership asks and underwriting reads ('support for this podcast comes from', member-station plugs) as ads under the H014 policy; add an offline synthetic underwriting/membership golden. CueDetector has no membership/underwriting patterns (self_promo is my/our book|course|newsletter…; scout extras cover brought to you by but not NPR underwriting).

- Primary metric: `detection`
- Status: `open`
- Experiment kind: `detection_gap`
- Confidence: Must still pass frozen ε. Ad-free interviews must not grow residual strong cues from generic 'support' speech.
- Detection: Hit/recall on a synthetic NPR-style membership/underwriting block should rise from 0 without dropping labeled sponsor fixtures.
- Cost: One extra confirm window per underwriting read is acceptable; scout tokens must stay within +10% of 5429.
- Notes: Seeded 2026-10-06 after H014 (membership/donation asks count as ads). Offline-testable with a synthetic golden. Do not edit production CueDetector until accepted and gates stay green. Related: ad lead-in boundaries cut late is a later detection follow-up, not folded here.

## Status values

- `open` — ranked for the next daily pick; not yet conclusive.
- `running` — in progress in a live/offline run.
- `measured` — has last_result numbers; no fold yet.
- `accepted` — fold-eligible on this branch (experiment package only). Production `AdClassifier` / flag stay unchanged.
- `rejected` — failed frozen regression ε; do not fold.
- `blocked` — waiting on live budget, golden labels, or review.

Never set `accepted` for a candidate that failed `compare_to_snapshot`.
Never loosen `docs/experiments/bow_scout_gemini_confirm/baseline/v1/gates.json`.

## Ledger notes

2026-10-06 offline follow-up (this agent) + live H013 (separate machine, gemini, 0 live calls, $0). H013 measured/process_ok; live-confirm-required-for-cost-fold is now enforced in daily_loop.cost_fold_eligible (mock-only cost wins never fold_eligible). H014 resolved: first-party/self/network/membership promos COUNT AS ADS. H011 rejected (TightPromo drops Soft Skills 531 ~1927–1962s self-promo ad). No fold. Frozen gates.json / snapshot.json unchanged. enable_bow_scout_gemini_confirm stays false. Seeded H015 (confidence: align eval labels with the policy; not a silent baseline change), H016 (detection: t=0 preroll), H017 (detection: NPR membership/underwriting). 2026-10-05 live: H012 measured/no_win; H011 was held then rejected today. 2026-10-02 weekday offline: H010 measured/no_win. H009 folded. 2026-10-01 afternoon: H006/H007/H008 folded. 2026-10-01 morning: H005 accepted. 2026-09-30 live: H001 measured (26→15), H002 rejected, H003 no_win, H004 process_ok.
