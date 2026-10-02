"""Whisper on candidate chunks only. Local path + stub when torch is missing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from podcast_processor.experiments.auto_gold.constants import DEFAULT_WHISPER_MODEL
from podcast_processor.experiments.auto_gold.types import (
    CandidateChunk,
    ChunkTranscript,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

TranscribeFn = Callable[[str, str], list[dict[str, Any]]]

STUB_REASON = (
    "whisper/torch not available on this machine; stubbed. "
    "On Cake, run local Whisper per docs/experiments/auto_gold/CAKE_RUN.md"
)


def _is_real_module(mod: Any) -> bool:
    path = getattr(mod, "__file__", None)
    return isinstance(path, str) and bool(path)


def whisper_import_status() -> tuple[bool, str]:
    try:
        import whisper
    except ImportError as exc:
        return False, f"import whisper failed: {exc}"
    if not _is_real_module(whisper):
        return False, "whisper module is a test mock, not a local install"
    try:
        import torch
    except ImportError as exc:
        return False, f"import torch failed: {exc}"
    if not _is_real_module(torch):
        return False, "torch module is a test mock, not a local install"
    return True, "local whisper+torch importable"


def resolve_whisper_backend(mode: str) -> tuple[str, str]:
    """Return (backend, detail). backend is 'local' or 'stub'."""
    wanted = (mode or "auto").strip().lower()
    ok, detail = whisper_import_status()
    if wanted == "stub":
        return "stub", STUB_REASON
    if wanted == "local":
        if not ok:
            raise RuntimeError(
                f"--whisper-mode local requested but unavailable: {detail}. "
                "Use --whisper-mode stub or run on Cake with local Whisper."
            )
        return "local", detail
    if wanted != "auto":
        raise ValueError(f"unknown whisper mode {mode!r}")
    if ok:
        return "local", detail
    return "stub", f"{STUB_REASON} ({detail})"


class ChunkWhisper:
    """Transcribe extracted candidate WAVs only (never the full episode)."""

    def __init__(
        self,
        mode: str = "auto",
        model_name: str = DEFAULT_WHISPER_MODEL,
        transcribe_fn: TranscribeFn | None = None,
        language: str = "en",
    ) -> None:
        self.mode = mode
        self.model_name = model_name
        self.transcribe_fn = transcribe_fn
        self.language = language
        if transcribe_fn is not None:
            self.backend = "injected"
            self.detail = "callable injected (tests / Cake wrapper)"
        else:
            self.backend, self.detail = resolve_whisper_backend(mode)

    def transcribe_chunk(
        self,
        chunk: CandidateChunk,
        wav_path: Path | None,
    ) -> ChunkTranscript:
        if wav_path is None:
            return ChunkTranscript(
                chunk=chunk,
                audio_path=None,
                skipped=True,
                skip_reason="no extracted wav (download/extract skipped)",
                backend=self.backend,
                text="",
            )
        if self.transcribe_fn is not None:
            rows = self.transcribe_fn(str(wav_path), self.language)
            return _from_rows(chunk, wav_path, rows, backend="injected")
        if self.backend == "stub":
            return ChunkTranscript(
                chunk=chunk,
                audio_path=wav_path,
                skipped=True,
                skip_reason=self.detail,
                backend="stub",
                text="",
            )
        return self._local_whisper(chunk, wav_path)

    def _local_whisper(
        self, chunk: CandidateChunk, wav_path: Path
    ) -> ChunkTranscript:
        import whisper

        logger.info("local whisper model=%s file=%s", self.model_name, wav_path)
        model = whisper.load_model(name=self.model_name)
        result = model.transcribe(str(wav_path), fp16=False, language=self.language)
        rows = list(result.get("segments") or [])
        return _from_rows(chunk, wav_path, rows, backend=f"local:{self.model_name}")


def _from_rows(
    chunk: CandidateChunk,
    wav_path: Path,
    rows: list[dict[str, Any]],
    backend: str,
) -> ChunkTranscript:
    segments: list[TranscriptSegment] = []
    texts: list[str] = []
    offset = float(chunk.start)
    for row in rows:
        start = float(row.get("start") or 0.0) + offset
        end = float(row.get("end") or start) + offset
        text = str(row.get("text") or "").strip()
        segments.append(TranscriptSegment(start=start, end=end, text=text))
        if text:
            texts.append(text)
    return ChunkTranscript(
        chunk=chunk,
        audio_path=wav_path,
        skipped=False,
        skip_reason=None,
        backend=backend,
        text=" ".join(texts),
        segments=segments,
    )
