"""Gemini confirm+boundary interface over scout windows.

Live calls are opt-in. CI and the offline harness always use the mock:

- GEMINI_API_KEY must be set AND
- PODLY_GEMINI_CONFIRM_LIVE=true
  before litellm is invoked.

Model name comes from GEMINI_CONFIRM_MODEL (default gemini/gemini-2.5-flash).
Responses are cached by sha256(model + messages) so repeat episodes are free.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from podcast_processor.cue_detector import CueDetector
from podcast_processor.experiments.types import (
    AdSpan,
    ConfirmResult,
    LabeledAd,
    ScoutWindow,
)

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_MODEL_ENV = "GEMINI_CONFIRM_MODEL"
GEMINI_LIVE_ENV = "PODLY_GEMINI_CONFIRM_LIVE"
DEFAULT_GEMINI_MODEL = "gemini/gemini-2.5-flash"
GROQ_API_KEY_ENV = "GROQ_API_KEY"
GROQ_MODEL_ENV = "GROQ_CONFIRM_MODEL"
GROQ_LIVE_ENV = "PODLY_GROQ_CONFIRM_LIVE"
DEFAULT_GROQ_CONFIRM_MODEL = "groq/openai/gpt-oss-120b"

MockMode = Literal["echo", "oracle", "none", "cue_only"]

CONFIRM_SYSTEM_PROMPT = """You confirm whether a short podcast transcript window contains advertisements and, if so, mark ad boundaries.

CRITICAL: distinguish external sponsor ads from technical discussion and self-promotion.

Return strict JSON:
{"is_ad": <bool>, "ad_spans": [{"start": <seconds.float>, "end": <seconds.float>, "confidence": <0.0-1.0>}], "content_type": "<promotional_external|educational/self_promo|technical_discussion|transition|none>", "confidence": <0.0-1.0>}

If there is no ad, return {"is_ad": false, "ad_spans": [], "content_type": "none", "confidence": 0.0}.
Only mark spans that fall inside the provided window. Include 1-5s transition bumpers that belong to the ad block.
"""


def default_gemini_model() -> str:
    return os.environ.get(GEMINI_MODEL_ENV, DEFAULT_GEMINI_MODEL)


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def live_calls_enabled() -> bool:
    """Gemini live confirm. Requires GEMINI_API_KEY and PODLY_GEMINI_CONFIRM_LIVE."""
    has_key = bool(os.environ.get(GEMINI_API_KEY_ENV, "").strip())
    return has_key and _flag_enabled(GEMINI_LIVE_ENV)


def groq_live_calls_enabled() -> bool:
    """Groq live confirm. Requires GROQ_API_KEY and PODLY_GROQ_CONFIRM_LIVE."""
    has_key = bool(os.environ.get(GROQ_API_KEY_ENV, "").strip())
    return has_key and _flag_enabled(GROQ_LIVE_ENV)


def any_live_confirm_enabled() -> bool:
    return live_calls_enabled() or groq_live_calls_enabled()


def default_groq_confirm_model() -> str:
    return os.environ.get(GROQ_MODEL_ENV, DEFAULT_GROQ_CONFIRM_MODEL)


def default_live_confirm_model() -> str:
    """Prefer Gemini when its live flag is on; otherwise Groq if enabled."""
    if live_calls_enabled():
        return default_gemini_model()
    if groq_live_calls_enabled():
        return default_groq_confirm_model()
    return default_gemini_model()


def prompt_hash(model: str, messages: list[dict[str, str]]) -> str:
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(b"\n")
    for message in messages:
        digest.update(message.get("role", "").encode("utf-8"))
        digest.update(b":")
        digest.update(message.get("content", "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def estimate_tokens_from_text(text: str) -> int:
    """Match AdClassifier / TokenRateLimiter fallback: ~4 characters per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_message_tokens(messages: list[dict[str, str]]) -> int:
    return estimate_tokens_from_text(
        "".join(message.get("content", "") for message in messages)
    )


_CONFIRM_HIGHLIGHTER = CueDetector(include_scout_extras=True)


def window_user_prompt(
    window: ScoutWindow, podcast_title: str, podcast_topic: str
) -> str:
    excerpts = "\n".join(
        f"[{seg.start_time}] {_CONFIRM_HIGHLIGHTER.highlight_cues(seg.text)}"
        for seg in window.segments
    )
    return (
        f'You are analyzing "{podcast_title}", a podcast about {podcast_topic}.\n'
        f"Scout window {window.start_time:.1f}s-{window.end_time:.1f}s "
        f"(seq {window.start_seq}-{window.end_seq}). "
        f"Cue types: {', '.join(window.cue_types) or 'none'}.\n"
        f"Return only the JSON contract.\n\n{excerpts}\n"
    )


class GeminiConfirmClient:
    def __init__(
        self,
        model: str | None = None,
        mock_mode: MockMode = "echo",
        cache_dir: Path | str | None = None,
        labeled_ads: list[LabeledAd] | None = None,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.model = model or default_live_confirm_model()
        self.mock_mode: MockMode = mock_mode
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.labeled_ads = labeled_ads or []
        self.completion_fn = completion_fn
        self.cue_detector = CueDetector(include_scout_extras=True)
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def confirm_window(
        self,
        window: ScoutWindow,
        podcast_title: str = "podcast",
        podcast_topic: str = "",
    ) -> ConfirmResult:
        messages = [
            {"role": "system", "content": CONFIRM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": window_user_prompt(window, podcast_title, podcast_topic),
            },
        ]
        cache_key = prompt_hash(self.model, messages)
        cached = self._read_cache(cache_key)
        if cached is not None:
            cached.cached = True
            cached.input_tokens = 0
            cached.output_tokens = 0
            cached.prompt_hash = cache_key
            cached.model = self.model
            return cached

        input_tokens = estimate_message_tokens(messages)
        if (
            live_calls_enabled()
            or groq_live_calls_enabled()
            or self.completion_fn is not None
        ):
            result = self._live_confirm(messages)
        else:
            result = self._mock_confirm(window)

        result.cached = False
        result.model = self.model
        result.prompt_hash = cache_key
        result.input_tokens = input_tokens
        if result.output_tokens <= 0:
            result.output_tokens = estimate_tokens_from_text(result.raw_response) or 80
        self._write_cache(cache_key, result)
        return result

    def confirm_windows(
        self,
        windows: list[ScoutWindow],
        podcast_title: str = "podcast",
        podcast_topic: str = "",
    ) -> list[ConfirmResult]:
        return [
            self.confirm_window(window, podcast_title, podcast_topic)
            for window in windows
        ]

    def _mock_confirm(self, window: ScoutWindow) -> ConfirmResult:
        if self.mock_mode == "none":
            payload = {
                "is_ad": False,
                "ad_spans": [],
                "content_type": "none",
                "confidence": 0.0,
            }
            return ConfirmResult(
                is_ad=False,
                ad_spans=[],
                content_type="none",
                confidence=0.0,
                raw_response=json.dumps(payload),
            )
        if self.mock_mode == "oracle":
            spans = [
                AdSpan(
                    start=max(window.start_time, ad.start),
                    end=min(window.end_time, ad.end),
                    confidence=0.95,
                )
                for ad in self.labeled_ads
                if min(window.end_time, ad.end) - max(window.start_time, ad.start) > 0.5
            ]
            is_ad = bool(spans)
            payload = {
                "is_ad": is_ad,
                "ad_spans": [
                    {"start": s.start, "end": s.end, "confidence": s.confidence}
                    for s in spans
                ],
                "content_type": "promotional_external" if is_ad else "none",
                "confidence": 0.95 if is_ad else 0.0,
            }
            return ConfirmResult(
                is_ad=is_ad,
                ad_spans=spans,
                content_type=payload["content_type"],
                confidence=float(payload["confidence"]),
                raw_response=json.dumps(payload),
            )
        if self.mock_mode == "cue_only":
            joined = " ".join(seg.text for seg in window.segments)
            is_ad = self.cue_detector.has_strong_cue(joined) or bool(
                self.cue_detector.analyze(joined).get("sponsor")
            )
            spans = [AdSpan(window.start_time, window.end_time, 0.8)] if is_ad else []
            payload = {
                "is_ad": is_ad,
                "ad_spans": [
                    {"start": s.start, "end": s.end, "confidence": s.confidence}
                    for s in spans
                ],
                "content_type": "promotional_external" if is_ad else "none",
                "confidence": 0.8 if is_ad else 0.0,
            }
            return ConfirmResult(
                is_ad=is_ad,
                ad_spans=spans,
                content_type=payload["content_type"],
                confidence=float(payload["confidence"]),
                raw_response=json.dumps(payload),
            )

        # echo: treat the whole scout window as an ad (upper-bound token use,
        # no quality claim). Used when labels are unavailable.
        payload = {
            "is_ad": True,
            "ad_spans": [
                {
                    "start": window.start_time,
                    "end": window.end_time,
                    "confidence": 0.7,
                }
            ],
            "content_type": "promotional_external",
            "confidence": 0.7,
        }
        return ConfirmResult(
            is_ad=True,
            ad_spans=[AdSpan(window.start_time, window.end_time, 0.7)],
            content_type="promotional_external",
            confidence=0.7,
            raw_response=json.dumps(payload),
        )

    def _live_confirm(self, messages: list[dict[str, str]]) -> ConfirmResult:
        if self.completion_fn is not None:
            response = self.completion_fn(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        else:
            if groq_live_calls_enabled() and not live_calls_enabled():
                groq_key = os.environ.get(GROQ_API_KEY_ENV, "").strip()
                os.environ.setdefault("GROQ_API_KEY", groq_key)
            else:
                api_key = os.environ.get(GEMINI_API_KEY_ENV, "").strip()
                os.environ.setdefault("GEMINI_API_KEY", api_key)
            import litellm  # lazy: CI / mock path never imports for a live call

            response = litellm.completion(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
            )
        content = response.choices[0].message.content or "{}"
        return _result_from_json(content)

    def _cache_path(self, cache_key: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{cache_key}.json"

    def _read_cache(self, cache_key: str) -> ConfirmResult | None:
        path = self._cache_path(cache_key)
        if path is None or not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ConfirmResult(
            is_ad=bool(payload.get("is_ad")),
            ad_spans=[
                AdSpan(
                    start=float(span["start"]),
                    end=float(span["end"]),
                    confidence=float(span.get("confidence", 0.0)),
                )
                for span in payload.get("ad_spans", [])
            ],
            content_type=payload.get("content_type"),
            confidence=float(payload.get("confidence", 0.0)),
            raw_response=payload.get("raw_response", ""),
            prompt_hash=cache_key,
        )

    def _write_cache(self, cache_key: str, result: ConfirmResult) -> None:
        path = self._cache_path(cache_key)
        if path is None:
            return
        payload = {
            "is_ad": result.is_ad,
            "ad_spans": [
                {"start": span.start, "end": span.end, "confidence": span.confidence}
                for span in result.ad_spans
            ],
            "content_type": result.content_type,
            "confidence": result.confidence,
            "raw_response": result.raw_response,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")


def _empty_confirm(content: str) -> ConfirmResult:
    return ConfirmResult(
        is_ad=False,
        ad_spans=[],
        content_type="none",
        confidence=0.0,
        raw_response=content,
    )


def _payload_dict(payload: object) -> dict[str, Any] | None:
    """Unwrap litellm/Gemini list-wrap (`[{...}]`) into a JSON object."""
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if isinstance(payload, dict):
        return payload
    return None


def _result_from_json(content: str) -> ConfirmResult:
    try:
        loaded = json.loads(content)
    except json.JSONDecodeError:
        return _empty_confirm(content)
    payload = _payload_dict(loaded)
    if payload is None:
        return _empty_confirm(content)
    spans = [
        AdSpan(
            start=float(span["start"]),
            end=float(span["end"]),
            confidence=float(span.get("confidence", 0.0)),
        )
        for span in payload.get("ad_spans", [])
        if isinstance(span, dict) and "start" in span and "end" in span
    ]
    return ConfirmResult(
        is_ad=bool(payload.get("is_ad") or spans),
        ad_spans=spans,
        content_type=payload.get("content_type"),
        confidence=float(payload.get("confidence") or 0.0),
        raw_response=content,
    )
