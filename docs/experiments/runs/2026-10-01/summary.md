# Daily experiment run 2026-10-01

Generated at `2026-10-01T17:05:12.168728+00:00`. Mode: **offline** (provider `mock`) on this Cloud Agent. Live Groq skipped (Cloudflare 1010). Gemini live catch-up ran on a different machine (tip `ade73440e24f18bba300eb5a860910cb494f0936`; box spend **$0.0049** Gemini, Whisper $0).

- Daily budget: `$0.50` (this offline session spent `$0.0000`)
- Production flag `enable_bow_scout_gemini_confirm`: `false`
- Feed default strategy: `llm`
- Baseline gates (post-H006, 11-fixture snapshot): **passed**
- `gates.json`: **not loosened**
- Folds: **H005** (morning) + **H006** (afternoon) corpus promotions. TightPromo stays experiment-only.

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
| `H005` | accepted | fold_eligible | true | Morning: Soft Skills-style golden into corpus v1 (n=10). |
| `H006` | accepted | fold_eligible | true | Afternoon: news-briefing-style golden into corpus v1 (n=11). Live 2→1 windows; Gemini classified dropped window as `technical_discussion`. |

Seeded but not yet folded: `H007` (duration-gated cue-sparse recovery), `H008` (pad=12/2 micro-sweep). Measured later in this catch-up against the post-H006 snapshot.

## H006 style fixture (offline oracle + live confirm)

| Detector | Windows | Hit | Confirm recall | Scout tok | Scout precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| recommended CueDetector | 2 | 1.0 | 1.0 | 746 | 0.143 |
| TightPromoCueDetector | 1 | 1.0 | 1.0 | 382 | 0.273 |

Live: Gemini kept `use code SAVE50`; dropped window class `technical_discussion`. Real eps still 26→15 (−42.3%). Residual strong-cue rate unchanged (0.0167) because leftover scan uses production CueDetector.

## Snapshot after H006 fold (n=11, recommended config)

| Metric | Pre (n=10, post-H005) | Post (n=11) |
| --- | ---: | ---: |
| confirm F1 / recall / hit | 0.9000 | 0.9091 |
| confirm precision | 1.0000 | 1.0000 |
| FN rate | 0.1000 | 0.0909 |
| residual strong-cue rate | 0.0056 | 0.0066 |
| token↓ % | 92.80 | 91.84 |
| scout tokens | 4606 | 5352 |

Mean recall rose because the new fixture is a labeled-ad hit; scout tokens rose by the briefing windows (746). Tolerances in `gates.json` are unchanged. Production flag still `False`. TightPromo on n=11 corpus uses 4608 scout tokens (drops tech-speech FPs on both style goldens) with the same 0.9091 F1/recall/hit; it stays experiment-only.

## Next open (ranked)

`H007`, `H008`

accepted = fold-eligible on the experiment package only. Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.
