"""Offline eval: production-like AdClassifier vs scout±confirm.

Comparable metrics: time precision/recall/F1, ad-block hit rate, token
estimate, residual CueDetector rate, duration-check stubs. Default path is
fully mocked (no API keys). Live Gemini is opt-in via env flags.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from podcast_processor.experiments.baseline import extract_snapshot
from podcast_processor.experiments.bow_scout import (
    BowScout,
    ScoutConfig,
)
from podcast_processor.experiments.cost_model import (
    DEFAULT_CLASSIFIER_PRICES,
    DEFAULT_GEMINI_PRICES,
    estimate_scout_confirm_tokens,
    token_reduction_pct,
)
from podcast_processor.experiments.fixtures import (
    CORPUS_VERSION,
    all_fixtures,
    corpus_manifest_path,
    sha256_file,
    write_fixture_json,
)
from podcast_processor.experiments.gemini_confirm import (
    GeminiConfirmClient,
    live_calls_enabled,
)
from podcast_processor.experiments.metrics import (
    path_quality_metrics,
    windows_to_spans,
)
from podcast_processor.experiments.production_mock import classify_production_like
from podcast_processor.experiments.types import (
    ConfirmResult,
    EpisodeFixture,
    ScoutWindow,
)
from shared import defaults as DEFAULTS

DEFAULT_SWEEP: list[ScoutConfig] = [
    ScoutConfig(
        threshold=0.5,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=False,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=False,
    ),
    ScoutConfig(
        threshold=1.0,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=False,
    ),
    ScoutConfig(
        threshold=0.5,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=0.0,
        pad_segments=0,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=30.0,
        pad_segments=5,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=1.5,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=True,
    ),
]

RECOMMENDED_CONFIG = ScoutConfig(
    threshold=0.5,
    pad_seconds=15.0,
    pad_segments=3,
    include_scout_extras=True,
)


def labeled_ad_duration(ads: list[Any]) -> float:
    return float(sum(ad.duration() for ad in ads))


def window_duration(windows: list[ScoutWindow]) -> float:
    return float(sum(window.duration() for window in windows))


def ad_hit_rate(ads: list[Any], windows: list[ScoutWindow]) -> float:
    from podcast_processor.experiments.metrics import ad_hit_rate as _hit

    return _hit(ads, windows_to_spans(windows))


def ad_coverage(ads: list[Any], windows: list[ScoutWindow]) -> float:
    from podcast_processor.experiments.metrics import ads_to_spans, time_prf

    return time_prf(ads_to_spans(ads), windows_to_spans(windows))["recall"]


def scout_precision(ads: list[Any], windows: list[ScoutWindow]) -> float:
    from podcast_processor.experiments.metrics import ads_to_spans, time_prf

    return time_prf(ads_to_spans(ads), windows_to_spans(windows))["precision"]


def missed_ads(ads: list[Any], windows: list[ScoutWindow]) -> list[dict[str, Any]]:
    from podcast_processor.experiments.metrics import missed_ads as _missed

    return _missed(ads, windows_to_spans(windows))


def evaluate_episode(
    episode: EpisodeFixture,
    config: ScoutConfig,
    cache_dir: Path | None = None,
    confirm_mock_mode: str = "oracle",
) -> dict[str, Any]:
    scout = BowScout(config=config)
    windows = scout.scout(episode.segments)
    live = live_calls_enabled()
    client = GeminiConfirmClient(
        mock_mode=confirm_mock_mode,  # type: ignore[arg-type]
        cache_dir=cache_dir,
        labeled_ads=episode.labeled_ads,
    )
    confirms = client.confirm_windows(
        windows, episode.podcast_title, episode.podcast_topic
    )
    confirmed_spans = _confirmed_spans(confirms)

    production = classify_production_like(episode)
    full_tokens = production.tokens
    scout_tokens = estimate_scout_confirm_tokens(
        windows, episode.podcast_title, episode.podcast_topic
    )
    repeat_tokens = estimate_scout_confirm_tokens(
        windows,
        episode.podcast_title,
        episode.podcast_topic,
        cached_hashes=_hashes_from_confirms(confirms),
    )

    scout_quality = path_quality_metrics(
        labeled_ads=episode.labeled_ads,
        predicted=windows_to_spans(windows),
        segments=episode.segments,
        source_seconds=float(episode.duration_seconds),
    )
    confirm_quality = path_quality_metrics(
        labeled_ads=episode.labeled_ads,
        predicted=confirmed_spans,
        segments=episode.segments,
        source_seconds=float(episode.duration_seconds),
    )
    production_quality = path_quality_metrics(
        labeled_ads=episode.labeled_ads,
        predicted=production.predicted,
        segments=episode.segments,
        source_seconds=float(episode.duration_seconds),
    )

    return {
        "fixture_id": episode.fixture_id,
        "title": episode.title,
        "n_segments": len(episode.segments),
        "duration_seconds": episode.duration_seconds,
        "n_labeled_ads": len(episode.labeled_ads),
        "labeled_ad_seconds": labeled_ad_duration(episode.labeled_ads),
        "n_windows": len(windows),
        "window_seconds": window_duration(windows),
        # Backward-compatible scout-window keys used by existing unit tests.
        "ad_hit_rate": scout_quality["ad_hit_rate"],
        "ad_coverage": scout_quality["time_recall"],
        "scout_precision": scout_quality["time_precision"],
        "false_negative_risk": scout_quality["false_negative_rate"],
        "missed_ads": scout_quality["missed_ads"],
        "oracle_confirm_hit_rate": confirm_quality["ad_hit_rate"],
        "residual_strong_cues": confirm_quality["residual_strong_cues"],
        "full_classifier": {
            "calls": full_tokens.calls,
            "input_tokens": full_tokens.input_tokens,
            "output_tokens": full_tokens.output_tokens,
            "usd": full_tokens.usd,
            "model": DEFAULT_CLASSIFIER_PRICES.name,
        },
        "scout_confirm_tokens": {
            "calls": scout_tokens.calls,
            "input_tokens": scout_tokens.input_tokens,
            "output_tokens": scout_tokens.output_tokens,
            "usd": scout_tokens.usd,
            "model": DEFAULT_GEMINI_PRICES.name,
            "cached_repeat_input_tokens": repeat_tokens.input_tokens,
            "cached_repeat_usd": repeat_tokens.usd,
        },
        # Alias kept for existing tests/docs.
        "scout_confirm": {
            "calls": scout_tokens.calls,
            "input_tokens": scout_tokens.input_tokens,
            "output_tokens": scout_tokens.output_tokens,
            "usd": scout_tokens.usd,
            "model": DEFAULT_GEMINI_PRICES.name,
            "cached_repeat_input_tokens": repeat_tokens.input_tokens,
            "cached_repeat_usd": repeat_tokens.usd,
        },
        "token_reduction_pct": token_reduction_pct(full_tokens, scout_tokens),
        "usd_reduction_pct": (
            100.0 * (1.0 - scout_tokens.usd / full_tokens.usd)
            if full_tokens.usd
            else 0.0
        ),
        "removal": confirm_quality["duration"],
        "paths": {
            "production_like": {
                **production_quality,
                "n_seed_segments": production.n_seed_segments,
                "n_expanded_segments": production.n_expanded_segments,
                "neighbor_window": production.neighbor_window,
                "input_tokens": full_tokens.input_tokens,
                "calls": full_tokens.calls,
                "usd": full_tokens.usd,
            },
            "scout": scout_quality,
            "scout_confirm": {
                **confirm_quality,
                "live_gemini": live,
                "confirm_mock_mode": confirm_mock_mode if not live else "live",
            },
        },
        "config": _config_dict(config),
        "notes": episode.notes,
    }


def evaluate_all(
    config: ScoutConfig | None = None,
    sweep: list[ScoutConfig] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    recommended = config or RECOMMENDED_CONFIG
    configs = sweep or DEFAULT_SWEEP
    episodes = all_fixtures()
    recommended_rows = [
        evaluate_episode(episode, recommended, cache_dir=cache_dir)
        for episode in episodes
    ]
    sweep_rows: list[dict[str, Any]] = []
    for cfg in configs:
        rows = [
            evaluate_episode(episode, cfg, cache_dir=cache_dir) for episode in episodes
        ]
        sweep_rows.append(
            {
                "config": _config_dict(cfg),
                "macro": _macro_summary(rows),
                "per_fixture": [
                    {
                        "fixture_id": row["fixture_id"],
                        "ad_hit_rate": row["ad_hit_rate"],
                        "ad_coverage": row["ad_coverage"],
                        "scout_precision": row["scout_precision"],
                        "token_reduction_pct": row["token_reduction_pct"],
                        "n_windows": row["n_windows"],
                        "scout_confirm_time_recall": row["paths"]["scout_confirm"][
                            "time_recall"
                        ],
                        "production_time_recall": row["paths"]["production_like"][
                            "time_recall"
                        ],
                    }
                    for row in rows
                ],
            }
        )
    manifest = corpus_manifest_path()
    corpus_hash = sha256_file(manifest) if manifest.exists() else None
    live = live_calls_enabled()
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "live_gemini": live,
        "corpus_version": CORPUS_VERSION,
        "corpus_sha256": corpus_hash,
        "enable_bow_scout_gemini_confirm_default": (
            DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM
        ),
        "assumptions": {
            "token_rule": "len(text)//4, matching AdClassifier/TokenRateLimiter",
            "adclassifier_overlap": "no-ad walk: ceil(chunk/2) capped at 30",
            "chunk_size": DEFAULTS.PROCESSING_NUM_SEGMENTS_TO_INPUT_TO_PROMPT,
            "fade_ms": DEFAULTS.OUTPUT_FADE_MS,
            "gemini_mocked": not live,
            "oracle_confirm": (
                "Gemini quality upper bound: confirm keeps labeled overlap in "
                "scout windows; misses are scout false negatives."
            ),
            "production_like": (
                "Oracle labels (fixture ads) plus CueDetector neighbor "
                "expansion (window=5, extras off) matching AdClassifier."
            ),
            "prices_usd_per_million": {
                "classifier_input": DEFAULT_CLASSIFIER_PRICES.input_usd_per_million,
                "classifier_output": DEFAULT_CLASSIFIER_PRICES.output_usd_per_million,
                "gemini_input": DEFAULT_GEMINI_PRICES.input_usd_per_million,
                "gemini_output": DEFAULT_GEMINI_PRICES.output_usd_per_million,
                "classifier_model": DEFAULT_CLASSIFIER_PRICES.name,
                "gemini_model": DEFAULT_GEMINI_PRICES.name,
                "note": "Estimates for relative comparison, not invoices.",
            },
        },
        "recommended_config": _config_dict(recommended),
        "recommended": {
            "macro": _macro_summary(recommended_rows),
            "per_fixture": recommended_rows,
        },
        "sweep": sweep_rows,
        "follow_up_live_gemini": _follow_up_notes(),
        "ready_to_pr_checklist": _ready_checklist(),
        "comparable_snapshot": None,
    }
    payload["comparable_snapshot"] = extract_snapshot(payload)
    return payload


def write_artifacts(
    results: dict[str, Any],
    output_dir: Path,
    write_fixture_json_files: bool = True,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if write_fixture_json_files:
        fixture_dir = output_dir / "fixtures"
        for episode in all_fixtures():
            write_fixture_json(episode, fixture_dir / f"{episode.fixture_id}.json")
    metrics_path = output_dir / "metrics.json"
    results_path = output_dir / "RESULTS.md"
    metrics_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    results_path.write_text(render_results_markdown(results), encoding="utf-8")
    return results_path, metrics_path


def render_results_markdown(results: dict[str, Any]) -> str:
    rec = results["recommended"]
    macro = rec["macro"]
    cfg = results["recommended_config"]
    lines: list[str] = [
        "# Bag-of-words scout + Gemini confirm: offline results",
        "",
        f"Generated at `{results['generated_at']}` (UTC). "
        "No live Gemini calls were made; confirm is an **oracle mock** that "
        "keeps labeled ad overlap inside scout windows.",
        "",
        "## Hypotheses",
        "",
        "1. `CueDetector.analyze` / `highlight_cues` can localize most true ads "
        "enough that Gemini only needs those windows + small context, not the "
        "full 60-segment AdClassifier walk.",
        "2. Token/cost of scout+confirm is much smaller than the full "
        "AdClassifier walk at comparable recall on available fixtures.",
        "3. Caching by content hash makes repeat episodes nearly free.",
        "",
        "## Recommended scout config (offline)",
        "",
        f"- threshold: `{cfg['threshold']}` (flag if CueDetector score ≥ this)",
        f"- pad_seconds: `{cfg['pad_seconds']}`",
        f"- pad_segments: `{cfg['pad_segments']}`",
        f"- include_scout_extras: `{cfg['include_scout_extras']}` "
        "(optional `sponsored by` / `brought to you by` / `ad break` patterns; "
        "off in production `CueDetector()`)",
        "- include_self_promo: `false` (matches AdClassifier demotion)",
        f"- production flag `enable_bow_scout_gemini_confirm`: "
        f"`{results.get('enable_bow_scout_gemini_confirm_default', False)}` "
        "(Feed/PodcastProcessor stay on the LLM AdClassifier path)",
        "",
        "## Production-like vs scout±confirm (before / after)",
        "",
        "Production-like is an **offline oracle** of the current AdClassifier "
        "walk: labeled ads (perfect LLM) plus CueDetector neighbor expansion "
        "(extras off, window=5). Scout±confirm is CueDetector windows plus "
        "oracle Gemini confirm. Live Gemini is not used in default CI.",
        "",
        f"Frozen corpus `{results.get('corpus_version', 'v1')}` "
        f"({macro['n_fixtures']} fixtures). Agent contract: "
        "`docs/experiments/AGENT_EVAL.md`.",
        "",
        "| Path | Time recall | Time precision | Time F1 | Ad-block hit | FN rate | Residual cue rate | Input tokens |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| Production-like (oracle LLM + neighbor expand) | "
        f"{_pct(macro['production_mean_time_recall'])} | "
        f"{_pct(macro['production_mean_time_precision'])} | "
        f"{_pct(macro['production_mean_time_f1'])} | "
        f"{_pct(macro['production_mean_ad_hit_rate'])} | "
        f"{_pct(1.0 - macro['production_mean_ad_hit_rate'])} | "
        f"{macro['production_mean_residual_strong_cue_rate']:.4f} | "
        f"{macro['sum_full_input_tokens']} |",
        "| Scout windows (pre-confirm) | "
        f"{_pct(macro['scout_mean_time_recall'])} | "
        f"{_pct(macro['scout_mean_time_precision'])} | "
        f"{_pct(macro['scout_mean_time_f1'])} | "
        f"{_pct(macro['scout_mean_ad_hit_rate'])} | "
        f"{_pct(macro['scout_mean_false_negative_rate'])} | "
        "— | — |",
        "| Scout + oracle confirm | "
        f"{_pct(macro['scout_confirm_mean_time_recall'])} | "
        f"{_pct(macro['scout_confirm_mean_time_precision'])} | "
        f"{_pct(macro['scout_confirm_mean_time_f1'])} | "
        f"{_pct(macro['scout_mean_ad_hit_rate'])} | "
        f"{_pct(macro['scout_mean_false_negative_rate'])} | "
        f"{macro['scout_confirm_mean_residual_strong_cue_rate']:.4f} | "
        f"{macro['sum_scout_input_tokens']} |",
        "",
        "## Headline metrics (recommended config)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Scout ad-block hit rate | {_pct(macro['mean_ad_hit_rate'])} |",
        f"| Scout labeled-ad time coverage | {_pct(macro['mean_ad_coverage'])} |",
        f"| Scout window precision (pre-confirm) | {_pct(macro['mean_scout_precision'])} |",
        f"| Scout+confirm time F1 | {_pct(macro['scout_confirm_mean_time_f1'])} |",
        f"| False-negative risk (missed ad blocks) | {_pct(macro['mean_false_negative_risk'])} |",
        f"| Residual strong-cue rate (after confirm cuts) | {macro['scout_confirm_mean_residual_strong_cue_rate']:.4f} |",
        f"| Mean token reduction vs full AdClassifier | {_pct(macro['mean_token_reduction_pct'] / 100.0)} |",
        f"| Mean USD reduction (est., similar $/M) | {_pct(macro['mean_usd_reduction_pct'] / 100.0)} |",
        f"| Full-walk input tokens (sum) | {macro['sum_full_input_tokens']} |",
        f"| Scout+confirm input tokens (sum) | {macro['sum_scout_input_tokens']} |",
        f"| Repeat-episode scout input tokens (cached) | {macro['sum_cached_repeat_input_tokens']} |",
        "",
        "Hit rate is the fraction of **labeled ad blocks** that overlap a scout "
        "window. Coverage is the fraction of **labeled ad seconds** inside those "
        "windows. Precision is labeled-ad seconds / predicted seconds. F1 is "
        "the harmonic mean of time precision and recall after spans are merged.",
        "",
        "## Per-fixture (recommended config)",
        "",
        "| Fixture | Segs | Ad blocks | Hit | Coverage | Precision | Windows | Full tok | Scout tok | Reduction | Missed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rec["per_fixture"]:
        missed = (
            "; ".join(f"{item['kind']} ({item['notes']})" for item in row["missed_ads"])
            or "—"
        )
        lines.append(
            "| {id} | {n} | {ads} | {hit} | {cov} | {prec} | {win} | {full} | {scout} | {red} | {miss} |".format(
                id=row["fixture_id"],
                n=row["n_segments"],
                ads=row["n_labeled_ads"],
                hit=_pct(row["ad_hit_rate"]),
                cov=_pct(row["ad_coverage"]),
                prec=_pct(row["scout_precision"]),
                win=row["n_windows"],
                full=row["full_classifier"]["input_tokens"],
                scout=row["scout_confirm"]["input_tokens"],
                red=f"{row['token_reduction_pct']:.1f}%",
                miss=missed,
            )
        )
    lines.extend(
        [
            "",
            "### Fixture notes",
            "",
        ]
    )
    for row in rec["per_fixture"]:
        lines.append(f"- `{row['fixture_id']}`: {row['notes']}")
    lines.extend(
        [
            "",
            "## False-negative risk",
            "",
            "Production `CueDetector` (no extras) is a **neighbor-expansion** helper "
            "after the LLM already found ads. It does **not** match `sponsor`, "
            "`brought to you by`, `advertisement`, or `ad break` — those live in "
            "chapter-filter strings, not the regexes. Scout extras add a subset of "
            "those phrases without changing default `CueDetector()` behavior.",
            "",
            "Ads the scout still misses at the recommended config:",
            "",
        ]
    )
    any_miss = False
    for row in rec["per_fixture"]:
        for item in row["missed_ads"]:
            any_miss = True
            lines.append(
                f"- `{row['fixture_id']}` {item['kind']} {item['start']:.0f}–{item['end']:.0f}s: {item['notes']}"
            )
    if not any_miss:
        lines.append("- None on this fixture set.")
    lines.extend(
        [
            "",
            "Cue-sparse host-reads (brand story, no URL/CTA/phone/sponsor phrase) "
            "are the main residual risk. Padding cannot recover an ad the scout "
            "never flags. A live Gemini full-walk would still catch these; a "
            "scout-first path will not unless extras grow or a cheap fallback "
            "full pass is kept.",
            "",
            "## Threshold / padding sweep",
            "",
            "| extras | thresh | pad_s | pad_seg | mean hit | mean cov | mean prec | mean tok↓ |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in results["sweep"]:
        c = item["config"]
        m = item["macro"]
        lines.append(
            "| {ex} | {th} | {ps} | {pg} | {hit} | {cov} | {prec} | {red} |".format(
                ex=c["include_scout_extras"],
                th=c["threshold"],
                ps=c["pad_seconds"],
                pg=c["pad_segments"],
                hit=_pct(m["mean_ad_hit_rate"]),
                cov=_pct(m["mean_ad_coverage"]),
                prec=_pct(m["mean_scout_precision"]),
                red=f"{m['mean_token_reduction_pct']:.1f}%",
            )
        )
    lines.extend(
        [
            "",
            "Raising padding increases coverage of ads whose cues sit in the "
            "middle of the block (Gemini then sees the intro). It also lowers "
            "precision and spends more confirm tokens. Threshold 0.5 with extras "
            "and ±15s/±3 segments is the best recall/token trade-off on this set: "
            "it includes transition bumpers (`after the break`) used by the "
            "prompt.py Wildcard example (score 0.5) while self-promo-only lines "
            "(weight 0.4) stay below the cut. Threshold 0.8 drops those "
            "transition-only ads. Cue-sparse brand reads still miss at every "
            "threshold.",
            "",
            "## Caching",
            "",
            "Confirm prompts are keyed by `sha256(model + messages)`. A second "
            "pass over the same scout windows is **0 input tokens** in the cost "
            "model (`cached_repeat_input_tokens` above). Production `ModelCall` "
            "upserts by `(post_id, model_name, first_seq, last_seq)` and does "
            "**not** hash prompt text; a follow-up PR should add content-hash "
            "caching beside or instead of that key if scout windows are reused "
            "across posts with identical audio.",
            "",
            "## Removal verifier (fade formula)",
            "",
            "When `clip_segments_with_fade`'s complex filter succeeds:",
            "",
            "`output_ms ≈ source_ms − Σ ad_ms + 2 × fade_ms × n_cuts`",
            "",
            f"Feed default `fade_ms` is `{DEFAULTS.OUTPUT_FADE_MS}`. "
            "Simple-concat fallback adds no fades. ffmpeg mux jitter of ~56ms "
            "is documented in `src/tests/test_process_audio.py` and is **not** "
            "included in the formula.",
            "",
            "## What a live Gemini run ($0.50/day, cache-first) should measure",
            "",
        ]
    )
    for note in results["follow_up_live_gemini"]:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## Ready to PR (later — this branch does not open one)",
            "",
        ]
    )
    for item in results["ready_to_pr_checklist"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- Agent contract: `docs/experiments/AGENT_EVAL.md`",
            "- Frozen corpus: `src/podcast_processor/experiments/corpus/v1/`",
            "- Frozen snapshot/gates: "
            "`docs/experiments/bow_scout_gemini_confirm/baseline/v1/`",
            "- Experiment package: `src/podcast_processor/experiments/`",
            "- CueDetector extras/score: `src/podcast_processor/cue_detector.py` "
            "(default constructor unchanged)",
            "- Harness: `scripts/experiments/run_bow_scout_eval.py`",
            "- Metrics dump: `metrics.json` (this directory)",
            "",
            "Production `Feed.ad_detection_strategy` stays `llm` and "
            "`enable_bow_scout_gemini_confirm` defaults **false**. "
            "PodcastProcessor does not swap in the experimental classifier.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _confirmed_spans(confirms: list[ConfirmResult]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for result in confirms:
        if not result.is_ad:
            continue
        for span in result.ad_spans:
            spans.append((float(span.start), float(span.end)))
    return spans


def _hashes_from_confirms(confirms: list[ConfirmResult]) -> set[str]:
    return {result.prompt_hash for result in confirms if result.prompt_hash}


def _config_dict(config: ScoutConfig) -> dict[str, Any]:
    return asdict(config)


def _macro_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1

    def mean(key_path: list[str]) -> float:
        total = 0.0
        for row in rows:
            value: Any = row
            for key in key_path:
                value = value[key]
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise TypeError(f"macro metric {key_path} is not numeric: {value!r}")
            total += float(value)
        return total / n

    return {
        "n_fixtures": len(rows),
        # Backward-compatible aliases (scout windows).
        "mean_ad_hit_rate": mean(["ad_hit_rate"]),
        "mean_ad_coverage": mean(["ad_coverage"]),
        "mean_scout_precision": mean(["scout_precision"]),
        "mean_false_negative_risk": mean(["false_negative_risk"]),
        "mean_token_reduction_pct": mean(["token_reduction_pct"]),
        "mean_usd_reduction_pct": mean(["usd_reduction_pct"]),
        "sum_full_input_tokens": sum(
            r["full_classifier"]["input_tokens"] for r in rows
        ),
        "sum_scout_input_tokens": sum(r["scout_confirm"]["input_tokens"] for r in rows),
        "sum_cached_repeat_input_tokens": sum(
            r["scout_confirm"]["cached_repeat_input_tokens"] for r in rows
        ),
        "sum_full_usd": sum(r["full_classifier"]["usd"] for r in rows),
        "sum_scout_usd": sum(r["scout_confirm"]["usd"] for r in rows),
        # Gate metrics.
        "scout_mean_ad_hit_rate": mean(["paths", "scout", "ad_hit_rate"]),
        "scout_mean_time_recall": mean(["paths", "scout", "time_recall"]),
        "scout_mean_time_precision": mean(["paths", "scout", "time_precision"]),
        "scout_mean_time_f1": mean(["paths", "scout", "time_f1"]),
        "scout_mean_false_negative_rate": mean(
            ["paths", "scout", "false_negative_rate"]
        ),
        "scout_confirm_mean_time_recall": mean(
            ["paths", "scout_confirm", "time_recall"]
        ),
        "scout_confirm_mean_time_precision": mean(
            ["paths", "scout_confirm", "time_precision"]
        ),
        "scout_confirm_mean_time_f1": mean(["paths", "scout_confirm", "time_f1"]),
        "scout_confirm_mean_residual_strong_cue_rate": mean(
            ["paths", "scout_confirm", "residual_strong_cue_rate"]
        ),
        "scout_confirm_sum_residual_strong_cues": sum(
            r["paths"]["scout_confirm"]["residual_strong_cues"] for r in rows
        ),
        "production_mean_time_recall": mean(
            ["paths", "production_like", "time_recall"]
        ),
        "production_mean_time_precision": mean(
            ["paths", "production_like", "time_precision"]
        ),
        "production_mean_time_f1": mean(["paths", "production_like", "time_f1"]),
        "production_mean_ad_hit_rate": mean(
            ["paths", "production_like", "ad_hit_rate"]
        ),
        "production_mean_residual_strong_cue_rate": mean(
            ["paths", "production_like", "residual_strong_cue_rate"]
        ),
    }


def _follow_up_notes() -> list[str]:
    return [
        "Run corpus v1 plus 5–10 real Whisper transcripts with human or "
        "current-AdClassifier labels; budget Gemini 2.5 Flash at ≤ $0.50/day "
        "and enable the content-hash cache directory first.",
        "Measure live confirm precision/recall vs oracle: does Gemini drop "
        "false-positive windows (Shopify.com technical mentions, 'check out this "
        "paper') and tighten boundaries inside padded windows?",
        "Measure live false negatives on cue-sparse host-reads; decide whether "
        "a periodic full AdClassifier pass, extra scout phrases, or chapter "
        "metadata should cover that tail.",
        "Compare boundary error (start/end vs labels) against production "
        "BoundaryRefiner / WordBoundaryRefiner — Gemini confirm is intended to "
        "replace some of that second LLM pass, not add a third.",
        "Record actual litellm usage tokens vs the chars/4 estimate; adjust "
        "cost_model prices to the Gemini SKU you actually call.",
        "Do not set enable_bow_scout_gemini_confirm or change Feed defaults "
        "until live recall on real episodes is within the AGENT_EVAL band of "
        "the full AdClassifier walk.",
    ]


def _ready_checklist() -> list[str]:
    return [
        "[x] Frozen golden corpus under "
        "`src/podcast_processor/experiments/corpus/v1/` with MANIFEST hashes.",
        "[x] Committed baseline snapshot + gates under "
        "`docs/experiments/bow_scout_gemini_confirm/baseline/v1/`.",
        "[x] Dual-path harness: production-like AdClassifier mock vs "
        "scout±confirm (time P/R/F1, tokens, residual cues, duration stubs).",
        "[x] Pytest/CI gates fail on recall drop, FN/residual rise, or token "
        "blow-up vs snapshot (see docs/experiments/AGENT_EVAL.md).",
        "[x] Experimental package isolated under "
        "`src/podcast_processor/experiments/` (PodcastProcessor does not swap "
        "classifiers).",
        "[x] CueDetector default constructor / `analyze` keys unchanged for production.",
        "[x] Gemini client mocks by default; live path requires GEMINI_API_KEY + "
        "PODLY_GEMINI_CONFIRM_LIVE=true.",
        "[x] `enable_bow_scout_gemini_confirm` defaults False; Feed strategy stays `llm`.",
        "[ ] Human review of scout extras (`brought to you by`, etc.) before "
        "enabling them in production neighbor expansion.",
        "[ ] Live Gemini confirm on real transcripts ($0.50/day, cache-first).",
        "[ ] Decision on cue-sparse fallback before wiring into PodcastProcessor.",
        "[ ] Alembic not required (no model changes).",
    ]


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"
