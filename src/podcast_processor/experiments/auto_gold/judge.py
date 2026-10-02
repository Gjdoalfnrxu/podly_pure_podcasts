"""Gemini gold judge on chunk transcripts. GROQ_KEY is never used."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from podcast_processor.experiments.auto_gold.constants import DEFAULT_GEMINI_MODEL
from podcast_processor.experiments.auto_gold.types import ChunkTranscript, JudgeLabel
from shared.env import GROQ_KEY_ENV, gemini_api_key, groq_api_key

CompletionFn = Callable[..., Any]

GOLD_JUDGE_SYSTEM_PROMPT = """You label whether a short podcast audio-chunk transcript contains external advertisements.

This is gold-standard labeling for an ad-removal eval, not production classification.
Distinguish external sponsor ads from technical discussion and self-promotion.

Return strict JSON:
{"is_ad": <bool>, "ad_spans": [{"start": <seconds.float>, "end": <seconds.float>, "confidence": <0.0-1.0>}], "content_type": "<promotional_external|educational/self_promo|technical_discussion|transition|none>", "confidence": <0.0-1.0>}

If there is no ad, return {"is_ad": false, "ad_spans": [], "content_type": "none", "confidence": 0.0}.
Only mark spans that fall inside the provided window. Include 1-5s transition bumpers that belong to the ad block.
Times are absolute episode seconds.
"""

SKIP_NO_KEY = (
    "no GEMINI_API_KEY / GEMINI_KEY / GOOGLE_API_KEY / "
    "GOOGLE_GENERATIVE_AI_API_KEY; dry-run skip. GROQ_KEY is unused."
)
SKIP_NO_TRANSCRIPT = "chunk has no Whisper transcript; judge skipped"


def groq_key_present() -> bool:
    return bool(groq_api_key())


def gemini_key_present() -> bool:
    return bool(gemini_api_key())


def resolve_judge_mode(mode: str) -> str:
    wanted = (mode or "auto").strip().lower()
    if wanted == "dry-run":
        return "dry-run"
    if wanted == "gemini":
        if not gemini_key_present():
            raise RuntimeError(
                "--judge-mode gemini requested but no Gemini/Google key is set. "
                "GROQ_KEY is ignored. Use --judge-mode dry-run."
            )
        return "gemini"
    if wanted != "auto":
        raise ValueError(f"unknown judge mode {mode!r}")
    return "gemini" if gemini_key_present() else "dry-run"


class GoldJudge:
    def __init__(
        self,
        mode: str = "auto",
        model: str | None = None,
        completion_fn: CompletionFn | None = None,
    ) -> None:
        self.requested_mode = mode
        self.model = (
            model or os.environ.get("GEMINI_CONFIRM_MODEL") or DEFAULT_GEMINI_MODEL
        )
        self.completion_fn = completion_fn
        if completion_fn is not None:
            self.mode = "injected"
        else:
            self.mode = resolve_judge_mode(mode)

    def judge(self, transcript: ChunkTranscript) -> JudgeLabel:
        if transcript.skipped or not transcript.text.strip():
            return JudgeLabel(
                is_ad=False,
                ad_spans=[],
                content_type="none",
                confidence=0.0,
                skipped=True,
                skip_reason=SKIP_NO_TRANSCRIPT
                if not transcript.skip_reason
                else f"{SKIP_NO_TRANSCRIPT} ({transcript.skip_reason})",
                model=self.model,
            )
        if self.completion_fn is not None:
            return self._complete(transcript, self.completion_fn)
        if self.mode == "dry-run":
            extra = ""
            if groq_key_present():
                extra = f" {GROQ_KEY_ENV} is set but unused for this judge."
            return JudgeLabel(
                is_ad=False,
                ad_spans=[],
                content_type="none",
                confidence=0.0,
                skipped=True,
                skip_reason=SKIP_NO_KEY + extra,
                model=self.model,
            )
        return self._live(transcript)

    def _live(self, transcript: ChunkTranscript) -> JudgeLabel:
        key = gemini_api_key()
        os.environ.setdefault("GEMINI_API_KEY", key)
        import litellm

        def _call(**kwargs: Any) -> Any:
            return litellm.completion(**kwargs)

        return self._complete(transcript, _call)

    def _complete(self, transcript: ChunkTranscript, fn: CompletionFn) -> JudgeLabel:
        messages = [
            {"role": "system", "content": GOLD_JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(transcript)},
        ]
        response = fn(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        return parse_judge_json(content, model=self.model)


def _user_prompt(transcript: ChunkTranscript) -> str:
    chunk = transcript.chunk
    excerpts = (
        "\n".join(
            f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}" for seg in transcript.segments
        )
        or transcript.text
    )
    return (
        f"Window {chunk.start:.1f}s-{chunk.end:.1f}s "
        f"(sources: {', '.join(chunk.sources)}).\n"
        f"Return only the JSON contract.\n\n{excerpts}\n"
    )


def parse_judge_json(content: str, model: str) -> JudgeLabel:
    try:
        loaded: Any = json.loads(content)
    except json.JSONDecodeError:
        return JudgeLabel(
            is_ad=False,
            ad_spans=[],
            content_type="none",
            confidence=0.0,
            skipped=False,
            skip_reason=None,
            model=model,
            raw_response=content,
        )
    payload = loaded[0] if isinstance(loaded, list) and loaded else loaded
    if not isinstance(payload, dict):
        payload = {}
    spans = []
    for span in payload.get("ad_spans") or []:
        if not isinstance(span, dict) or "start" not in span or "end" not in span:
            continue
        spans.append(
            {
                "start": float(span["start"]),
                "end": float(span["end"]),
                "confidence": float(span.get("confidence") or 0.0),
            }
        )
    is_ad = bool(payload.get("is_ad") or spans)
    return JudgeLabel(
        is_ad=is_ad,
        ad_spans=spans,
        content_type=str(
            payload.get("content_type") or ("promotional_external" if is_ad else "none")
        ),
        confidence=float(payload.get("confidence") or 0.0),
        skipped=False,
        skip_reason=None,
        model=model,
        raw_response=content,
    )
