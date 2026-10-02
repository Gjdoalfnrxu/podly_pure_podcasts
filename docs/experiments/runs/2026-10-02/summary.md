# Daily experiment run 2026-10-02

Generated at `2026-10-02T15:54:41.096321+00:00`. Mode: **offline** (provider `mock`) on this Cloud Agent. Live Groq skipped (no live flags; GROQ_KEY present for non-network tasks only). Spend **$0.0000**.

- Daily budget: `$0.50` (this offline session spent `$0.0000`)
- Production flag `enable_bow_scout_gemini_confirm`: `false`
- Feed default strategy: `llm`
- Baseline gates (post-H008 snapshot): **passed**
- `gates.json`: **not loosened**
- Folds this run: **H009** (wider duration-gated probe) after measurement. **H010** measured/`no_win` — TightPromo stays experiment-only. Production CueDetector.promo_pattern unchanged.

## Spend

| Source | USD |
| --- | ---: |
| Groq (this session) | $0.0000 (not called) |
| Gemini (this session) | $0.0000 (not called) |
| **Session total (this agent, billable)** | **$0.0000** |

## Hypotheses this run

| ID | Status | Verdict | Fold-eligible | Notes |
| --- | --- | --- | --- | --- |
| `H010` | measured | no_win | false | Confidence primary. Gates green; TightPromo scout tokens **4652 vs 5337**, but residual **0.0066** and confirm precision **1.0** unchanged vs frozen recommended. Do **not** force-accept. |
| `H009` | accepted | fold_eligible | true | Detection primary. Confirm F1/recall/hit **1.0** (was 0.9752 / 0.9610 / 1.0). Scout tokens **5429 vs 5337** (limit ~5871). Cue-sparse time recall 0.571→1.0. Fold into eval `DEFAULT_WINDOW_POSTPROCESS`. |

accepted = fold-eligible on the experiment package only. Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.

## Frozen macros vs this run

| Metric | Frozen recommended (post-H008) | H010 TightPromo | H009 wider probe |
| --- | ---: | ---: | ---: |
| confirm F1 | 0.9752 | 0.9752 | **1.0000** |
| confirm recall | 0.9610 | 0.9610 | **1.0000** |
| ad-block hit | 1.0000 | 1.0000 | 1.0000 |
| confirm precision | 1.0000 | 1.0000 | 1.0000 |
| residual | 0.0066 | 0.0066 | 0.0066 |
| token↓ % | 92.05 | 93.31 | 91.95 |
| scout tokens | 5337 | 4652 | 5429 |

H010 token drop is real but the confidence proxy (residual / confirm precision) did not move, so the loop correctly returned `no_win`. H009 detection win is fold-eligible inside the +10% scout-token ε.

Production flag still `False`. Production `CueDetector.promo_pattern` unchanged.

## Next open (ranked)

Seeded after this run's folds: `H011` (TightPromo as eval detector on **cost**), `H012` (pad micro-sweep after the 70s probe).
