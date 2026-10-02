# Hypothesis ledger

Machine-readable source: `docs/experiments/hypotheses/hypotheses.json`.
User priorities (absolute): **confidence** (no regressions) → **detection** (F1/recall) → **cost** (token reduction).

The daily runner loads **open** rows in that order, runs offline eval vs the frozen snapshot by default, and never loosens `gates.json`.

Updated at `2026-10-02T15:59:16.247095+00:00` (UTC).

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
| `H011` | cost | open | Use TightPromoCueDetector as the experiment-package recommended scout detector on cost (not confidence): H010 showed residual/precision unchanged, but TightPromo cuts scout tokens with F1/recall/hit tied on corpus v1. | — |
| `H012` | cost | open | Micro-variant pad/threshold sweep under the post-H009 70s probe (t=0.5 pad=10/2 extras; t=0.5 pad=8/1 extras); keep recommended unless a survivor strictly beats F1 then recall then tokens. | — |

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
- Status: `open`
- Experiment kind: `cue_pattern`
- Confidence: Must still pass frozen ε. Residual/precision may stay tied; do not treat a token-only win as a confidence win.
- Detection: Confirm F1/recall/hit must stay at the post-H009 snapshot (1.0 / 1.0 / 1.0).
- Cost: Fewer confirm tokens on unlabeled tech-speech FPs vs post-H009 scout tokens 5429 (limit ~5972).
- Notes: H010 follow-up with cost primary. Fold would set eval recommended detector only. Do not copy TIGHT_PROMO_PATTERN into production CueDetector.promo_pattern. Do not flip enable_bow_scout_gemini_confirm. No new human labels.

### `H012`

Micro-variant pad/threshold sweep under the post-H009 70s probe (t=0.5 pad=10/2 extras; t=0.5 pad=8/1 extras); keep recommended unless a survivor strictly beats F1 then recall then tokens.

- Primary metric: `cost`
- Status: `open`
- Experiment kind: `pad_sweep`
- Confidence: Any config that fails snapshot ε is discarded, even if cheaper.
- Detection: Do not trade F1/recall/hit for tokens. Post-H009 recommended 0.5/12s/2 extras=on is F1/recall/hit 1.0 on v1.
- Cost: Among survivors, prefer higher mean_token_reduction_pct / fewer scout tokens than 5429.
- Notes: Follow-up to H008/H009. Sweep is experiment-package only. Do not change production neighbor window. No new human labels.

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

2026-10-02 weekday offline: H010 measured/no_win (TightPromo tokens 4652 vs 5337; residual/precision unchanged — not accepted). H009 folded (confirm F1/recall/hit 1.0, scout tokens 5429 vs 5337, limit ~5871). Seeded H011 (TightPromo as eval detector on cost) and H012 (pad micro-sweep after 70s probe). Production AdClassifier and enable_bow_scout_gemini_confirm stay off. Frozen gates.json not loosened. TightPromo stays experiment-only. Spend $0. 2026-10-01 afternoon: H006/H007/H008 folded. 2026-10-01 morning: H005 accepted. 2026-09-30 live: H001 measured (26→15), H002 rejected, H003 no_win, H004 process_ok.
