# Auto gold baseline report (`general_podcast_ads`)

Locked gold family: **`general_podcast_ads`**. Sampling unit: **show**.
Do not replace this catalog with two finance shows.

This file is a **template**. `scripts/experiments/run_auto_gold_baseline.py`
fills the `AUTO_GOLD_*` sections. Circularity notes below stay as committed
prose.

Production `AdClassifier` is not in this pipeline.
`enable_bow_scout_gemini_confirm` stays false.

## Status

<!-- BEGIN:AUTO_GOLD_STATUS -->
_Not yet run._
<!-- END:AUTO_GOLD_STATUS -->

## Sample (one recent episode per show)

<!-- BEGIN:AUTO_GOLD_SAMPLE -->
_Not yet run._
<!-- END:AUTO_GOLD_SAMPLE -->

## Candidates

High-recall union, independent of production `AdClassifier`: always 0–90s;
publisher markers as positives only; optional fingerprint near-dupe; optional
ffmpeg silencedetect; optional DAI-host midroll probes; merge pad ±1–2s.

<!-- BEGIN:AUTO_GOLD_CANDIDATES -->
_Not yet run._
<!-- END:AUTO_GOLD_CANDIDATES -->

## Whisper (candidate chunks only)

<!-- BEGIN:AUTO_GOLD_WHISPER -->
_Not yet run._
<!-- END:AUTO_GOLD_WHISPER -->

## Judge (Gemini / dry-run; GROQ_KEY unused)

<!-- BEGIN:AUTO_GOLD_JUDGE -->
_Not yet run._
<!-- END:AUTO_GOLD_JUDGE -->

## Blocked steps

On a Cursor cloud VM without torch/Gemini, expect Whisper stub + judge
dry-run. RSS-only still records the representative feed list.

<!-- BEGIN:AUTO_GOLD_BLOCKED -->
_Not yet run._
<!-- END:AUTO_GOLD_BLOCKED -->

## Production flags

<!-- BEGIN:AUTO_GOLD_PRODUCTION_FLAGS -->
_Not yet run._
<!-- END:AUTO_GOLD_PRODUCTION_FLAGS -->

## Circularity (read before treating labels as independent truth)

**Whisper-shaped gold.** Candidates are proposed without a full-episode
transcript. Whisper then runs only on those chunks. Ads that were never
proposed cannot appear in gold. Whisper timestamps, dropped words, and
hallucinated promo language shape the judge input. This gold is therefore
conditioned on the candidate generator + ASR, not on a human full listen.

**Same-family judge.** The default live judge is Gemini 2.5 Flash (or
`GEMINI_CONFIRM_MODEL`). The scout±confirm experiment uses the same model
family. Production `AdClassifier` is also an LLM walk. Measuring confirm or
classifier agreement against this gold is **optimistic**. Keep a human
review step before promoting anything into `corpus/v1`.

**Publisher markers are positives only.** A chapter titled “Sponsor” is a
candidate (and a weak prior). Unmarked time is *not* labeled non-ad.

**Locked family.** `general_podcast_ads` is the only family this runner will
write. Do not mix Daily/Soft-Skills style synthetics into this report.
