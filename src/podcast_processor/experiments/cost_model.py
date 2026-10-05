"""Token/cost estimates: full AdClassifier walk vs scout-window Gemini confirm.

Uses the same ~4 chars/token fallback as TokenRateLimiter and AdClassifier.
Prices are documented estimates for offline comparison, not live billing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Template

from podcast_processor.experiments.gemini_confirm import (
    CONFIRM_SYSTEM_PROMPT,
    estimate_message_tokens,
    estimate_tokens_from_text,
    prompt_hash,
    window_user_prompt,
)
from podcast_processor.experiments.types import (
    ScoutSegment,
    ScoutWindow,
    TokenEstimate,
)
from podcast_processor.prompt import transcript_excerpt_for_prompt
from podcast_processor.transcribe import Segment
from shared import defaults as DEFAULTS

# Approximate USD / million tokens. Update when a live Gemini run is budgeted.
GROQ_GPT_OSS_120B_INPUT_USD_PER_M = 0.15
GROQ_GPT_OSS_120B_OUTPUT_USD_PER_M = 0.60
GEMINI_25_FLASH_INPUT_USD_PER_M = 0.15
GEMINI_25_FLASH_OUTPUT_USD_PER_M = 0.60
GEMINI_31_FLASH_LITE_INPUT_USD_PER_M = 0.25
GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_M = 1.50

# Typical JSON response size when the chunk/window has 0-few ads.
DEFAULT_OUTPUT_TOKENS_PER_CALL = 250


@dataclass(frozen=True)
class ModelPrices:
    name: str
    input_usd_per_million: float
    output_usd_per_million: float


DEFAULT_CLASSIFIER_PRICES = ModelPrices(
    name=DEFAULTS.LLM_DEFAULT_MODEL,
    input_usd_per_million=GROQ_GPT_OSS_120B_INPUT_USD_PER_M,
    output_usd_per_million=GROQ_GPT_OSS_120B_OUTPUT_USD_PER_M,
)
GEMINI_25_FLASH_PRICES = ModelPrices(
    name="gemini/gemini-2.5-flash",
    input_usd_per_million=GEMINI_25_FLASH_INPUT_USD_PER_M,
    output_usd_per_million=GEMINI_25_FLASH_OUTPUT_USD_PER_M,
)
GEMINI_31_FLASH_LITE_PRICES = ModelPrices(
    name="gemini/gemini-3.1-flash-lite",
    input_usd_per_million=GEMINI_31_FLASH_LITE_INPUT_USD_PER_M,
    output_usd_per_million=GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_M,
)
# Eval token-USD estimates stay on the frozen 2.5-flash row so snapshot
# macros do not move. Live accounting looks up the configured model.
DEFAULT_GEMINI_PRICES = GEMINI_25_FLASH_PRICES

_GEMINI_PRICES_BY_MODEL: dict[str, ModelPrices] = {
    GEMINI_25_FLASH_PRICES.name: GEMINI_25_FLASH_PRICES,
    "gemini-2.5-flash": GEMINI_25_FLASH_PRICES,
    GEMINI_31_FLASH_LITE_PRICES.name: GEMINI_31_FLASH_LITE_PRICES,
    "gemini-3.1-flash-lite": GEMINI_31_FLASH_LITE_PRICES,
}


def gemini_prices_for_model(model: str | None) -> ModelPrices:
    """Return the Gemini price row for a litellm model id.

    Unknown SKUs fall back to the 2.5-flash eval row rather than inventing
    a rate. Accounting for a live confirm must pass the configured model.
    """
    if not model:
        return DEFAULT_GEMINI_PRICES
    normalized = model.strip()
    if normalized in _GEMINI_PRICES_BY_MODEL:
        return _GEMINI_PRICES_BY_MODEL[normalized]
    sku = normalized.split("/")[-1]
    for key, prices in _GEMINI_PRICES_BY_MODEL.items():
        if sku == key.split("/")[-1]:
            return prices
    return DEFAULT_GEMINI_PRICES


def usd_for_tokens(input_tokens: int, output_tokens: int, prices: ModelPrices) -> float:
    return (
        input_tokens / 1_000_000.0 * prices.input_usd_per_million
        + output_tokens / 1_000_000.0 * prices.output_usd_per_million
    )


def load_classifier_system_prompt(
    path: str | Path = "src/system_prompt.txt",
) -> str:
    prompt_path = Path(path)
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    # Fallback: keep estimates working if cwd is not the repo root.
    from podcast_processor.prompt import generate_system_prompt

    return generate_system_prompt()


def load_user_prompt_template(
    path: str | Path = "src/user_prompt.jinja",
) -> Template:
    template_path = Path(path)
    if template_path.exists():
        return Template(template_path.read_text(encoding="utf-8"))
    return Template(
        'You are analyzing "{{podcast_title}}", a podcast about {{podcast_topic}}.\n'
        "Return only the JSON contract described in the system prompt using the "
        "transcript excerpt below.\n\n{{transcript}}\n"
    )


def _to_prompt_segments(segments: list[ScoutSegment]) -> list[Segment]:
    return [
        Segment(start=seg.start_time, end=seg.end_time, text=seg.text)
        for seg in segments
    ]


def render_classifier_user_prompt(
    segments: list[ScoutSegment],
    podcast_title: str,
    podcast_topic: str,
    includes_start: bool,
    includes_end: bool,
    template: Template | None = None,
) -> str:
    tmpl = template or load_user_prompt_template()
    return tmpl.render(
        podcast_title=podcast_title,
        podcast_topic=podcast_topic,
        transcript=transcript_excerpt_for_prompt(
            segments=_to_prompt_segments(segments),
            includes_start=includes_start,
            includes_end=includes_end,
        ),
    )


def adclassifier_chunk_plan(
    n_segments: int,
    num_segments_per_prompt: int = DEFAULTS.PROCESSING_NUM_SEGMENTS_TO_INPUT_TO_PROMPT,
    max_overlap_segments: int = DEFAULTS.PROCESSING_MAX_OVERLAP_SEGMENTS,
) -> list[tuple[int, int]]:
    """Return (start_index, end_index_exclusive) for each AdClassifier LLM call.

    Mirrors AdClassifier._step / _compute_next_overlap_segments when no ads
    are identified (the always-paid full-transcript walk). Overlap is the last
    ceil(chunk_len/2) segments, capped at max_overlap_segments.
    """
    if n_segments <= 0:
        return []
    if num_segments_per_prompt <= 0:
        raise ValueError("num_segments_per_prompt must be positive")

    chunks: list[tuple[int, int]] = []
    current_index = 0
    overlap_count = 0
    while current_index < n_segments:
        new_count = min(num_segments_per_prompt, n_segments - current_index)
        chunk_start = max(0, current_index - overlap_count)
        chunk_end = current_index + new_count
        chunks.append((chunk_start, chunk_end))
        chunk_len = chunk_end - chunk_start
        current_index += new_count
        if current_index >= n_segments:
            break
        base_tail = max(1, math.ceil(chunk_len / 2))
        overlap_count = min(max_overlap_segments, base_tail)
    return chunks


def estimate_adclassifier_tokens(
    segments: list[ScoutSegment],
    podcast_title: str,
    podcast_topic: str,
    system_prompt: str | None = None,
    num_segments_per_prompt: int = DEFAULTS.PROCESSING_NUM_SEGMENTS_TO_INPUT_TO_PROMPT,
    max_overlap_segments: int = DEFAULTS.PROCESSING_MAX_OVERLAP_SEGMENTS,
    prices: ModelPrices = DEFAULT_CLASSIFIER_PRICES,
    output_tokens_per_call: int = DEFAULT_OUTPUT_TOKENS_PER_CALL,
) -> TokenEstimate:
    sys_prompt = (
        system_prompt if system_prompt is not None else load_classifier_system_prompt()
    )
    template = load_user_prompt_template()
    chunks = adclassifier_chunk_plan(
        len(segments), num_segments_per_prompt, max_overlap_segments
    )
    input_tokens = 0
    chunk_tokens: list[int] = []
    for start, end in chunks:
        chunk = segments[start:end]
        user_prompt = render_classifier_user_prompt(
            segments=chunk,
            podcast_title=podcast_title,
            podcast_topic=podcast_topic,
            includes_start=start == 0,
            includes_end=end >= len(segments),
            template=template,
        )
        tokens = estimate_tokens_from_text(sys_prompt) + estimate_tokens_from_text(
            user_prompt
        )
        chunk_tokens.append(tokens)
        input_tokens += tokens
    output_tokens = output_tokens_per_call * len(chunks)
    return TokenEstimate(
        calls=len(chunks),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=usd_for_tokens(input_tokens, output_tokens, prices),
        details={
            "chunk_index_ranges": chunks,
            "chunk_input_tokens": chunk_tokens,
            "num_segments_per_prompt": num_segments_per_prompt,
            "max_overlap_segments": max_overlap_segments,
            "model": prices.name,
        },
    )


def estimate_scout_confirm_tokens(
    windows: list[ScoutWindow],
    podcast_title: str,
    podcast_topic: str,
    prices: ModelPrices = DEFAULT_GEMINI_PRICES,
    output_tokens_per_call: int = DEFAULT_OUTPUT_TOKENS_PER_CALL,
    cached_hashes: set[str] | None = None,
) -> TokenEstimate:
    input_tokens = 0
    output_tokens = 0
    calls = 0
    cached_calls = 0
    window_tokens: list[int] = []
    for window in windows:
        messages = [
            {"role": "system", "content": CONFIRM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": window_user_prompt(window, podcast_title, podcast_topic),
            },
        ]
        cache_key = prompt_hash(prices.name, messages)
        tokens = estimate_message_tokens(messages)
        window_tokens.append(tokens)
        if cached_hashes is not None and cache_key in cached_hashes:
            cached_calls += 1
            continue
        calls += 1
        input_tokens += tokens
        output_tokens += output_tokens_per_call
        if cached_hashes is not None:
            cached_hashes.add(cache_key)
    return TokenEstimate(
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=usd_for_tokens(input_tokens, output_tokens, prices),
        details={
            "windows": len(windows),
            "cached_calls": cached_calls,
            "window_input_tokens": window_tokens,
            "model": prices.name,
        },
    )


def token_reduction_pct(full: TokenEstimate, scout: TokenEstimate) -> float:
    if full.input_tokens <= 0:
        return 0.0
    return 100.0 * (1.0 - scout.input_tokens / full.input_tokens)
