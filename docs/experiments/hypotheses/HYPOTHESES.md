# Hypothesis ledger

Machine-readable source: `docs/experiments/hypotheses/hypotheses.json`.
User priorities (absolute): **confidence** (no regressions) → **detection** (F1/recall) → **cost** (token reduction).

The daily runner loads **open** rows in that order, runs offline eval vs the frozen snapshot by default, and never loosens `gates.json`.

Updated at `unspecified` (UTC).

| ID | Primary | Status | Statement | Last result |
| --- | --- | --- | --- | --- |
| `H001` | confidence | open | Tighten CueDetector promo so bare `code <word>` (tech speech: code review, code path) is not a strong cue, while `use code SAVE20` / `promo CODE` still flag real ads. | — |
| `H002` | detection | open | Recover cue-sparse host-reads (Away-style brand story, no URL/CTA/phone) without paying a full AdClassifier walk. | — |
| `H003` | cost | open | Re-rank pad/threshold/extras sweeps under frozen gate constraints; keep recommended config unless a survivor strictly beats it on detection then cost. | — |
| `H004` | confidence | open | Add a secret-safe process for real-transcript goldens in The Daily / Soft Skills style (news briefing + interview host-reads) without leaking API keys into git. | — |

## Predicted effects

### `H001`

Tighten CueDetector promo so bare `code <word>` (tech speech: code review, code path) is not a strong cue, while `use code SAVE20` / `promo CODE` still flag real ads.

- Primary metric: `confidence`
- Status: `open`
- Experiment kind: `cue_pattern`
- Confidence: Fewer promo false positives on technical discussion; residual strong-cue rate on false_positive_content should not rise.
- Detection: No labeled-ad recall/hit drop on corpus v1 (ads already say use code / promo).
- Cost: Slightly fewer confirm windows if tech-speech FPs disappear; token reduction should not fall.
- Notes: Experiment-only detector. Do not edit production cue_detector.promo_pattern until this is accepted and gates stay green.

### `H002`

Recover cue-sparse host-reads (Away-style brand story, no URL/CTA/phone) without paying a full AdClassifier walk.

- Primary metric: `detection`
- Status: `open`
- Experiment kind: `cheap_recovery`
- Confidence: Must still pass frozen ε. Ad-free episodes must not grow residual strong cues.
- Detection: Lift cue_sparse_storytelling ad-block hit/recall from 0 without dropping other fixtures.
- Cost: At most one extra confirm window per miss, not a 60-segment walk; scout tokens must stay within +10% of snapshot.
- Notes: Padding cannot recover an ad the scout never flags. Production CueDetector extras stay off.

### `H003`

Re-rank pad/threshold/extras sweeps under frozen gate constraints; keep recommended config unless a survivor strictly beats it on detection then cost.

- Primary metric: `cost`
- Status: `open`
- Experiment kind: `pad_sweep`
- Confidence: Any config that fails snapshot ε is discarded, even if cheaper.
- Detection: Do not trade F1/recall for tokens. Recommended 0.5/15s/3 extras=on should remain best recall on v1.
- Cost: Among survivors, prefer higher mean_token_reduction_pct / fewer scout tokens.
- Notes: Sweep is experiment-package only. Do not change production neighbor window.

### `H004`

Add a secret-safe process for real-transcript goldens in The Daily / Soft Skills style (news briefing + interview host-reads) without leaking API keys into git.

- Primary metric: `confidence`
- Status: `open`
- Experiment kind: `golden_ingest`
- Confidence: Ingest refuses .env, gsk_/AIza/sk- blobs, and api_key fields. Corpus v1 hashes unchanged until an explicit baseline update.
- Detection: New goldens, once promoted, should cover live host-read FP/FN that synthetic v1 misses.
- Cost: No live spend during ingest. Promotion still uses --write-corpus --update-baseline together.
- Notes: Do not commit copyrighted episode text. Staging is gitignored. Never store GROQ_API_KEY or GEMINI_API_KEY next to transcripts.

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

Seeded from corpus v1 gaps. Production AdClassifier and enable_bow_scout_gemini_confirm stay off. Frozen gates.json must not be loosened.
