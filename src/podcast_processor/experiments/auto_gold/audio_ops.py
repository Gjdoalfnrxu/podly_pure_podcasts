"""ffmpeg helpers: duration, chunk extract, optional DSP + fingerprint."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import shutil
import struct
import subprocess
from pathlib import Path

from podcast_processor.experiments.auto_gold.types import CandidateChunk

logger = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class AudioOpsError(RuntimeError):
    """ffmpeg/ffprobe failure."""


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe_duration_seconds(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        completed = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return None
    text = completed.stdout.strip()
    try:
        return float(text)
    except ValueError:
        return None


def extract_chunk_wav(
    src: Path,
    dest: Path,
    start: float,
    end: float,
) -> Path:
    if not shutil.which("ffmpeg"):
        raise AudioOpsError("ffmpeg not on PATH")
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.5, float(end) - float(start))
    cmd = [
        FFMPEG,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(src),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AudioOpsError(f"ffmpeg extract failed: {exc}") from exc
    return dest


def silencedetect_candidates(
    src: Path,
    duration: float | None,
    *,
    noise_db: float = -30.0,
    min_silence: float = 1.5,
    half_window: float = 12.0,
    max_hits: int = 8,
) -> list[CandidateChunk]:
    """Cheap DSP: silence gaps often bound DAI / midroll inserts."""
    if not shutil.which("ffmpeg"):
        return []
    cmd = [
        FFMPEG,
        "-i",
        str(src),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("silencedetect failed: %s", exc)
        return []
    text = (completed.stderr or "") + (completed.stdout or "")
    ends = [
        float(match.group(1))
        for match in re.finditer(r"silence_end:\s*([0-9.]+)", text)
    ]
    chunks: list[CandidateChunk] = []
    for ts in ends[:max_hits]:
        start = max(0.0, ts - half_window)
        end = ts + half_window
        if duration is not None:
            end = min(end, duration)
        if end - start < 1.0:
            continue
        chunks.append(
            CandidateChunk(
                start=start,
                end=end,
                sources=["dsp_silence"],
                notes=f"silencedetect around {ts:.1f}s",
            )
        )
    return chunks


def _pcm_energy_fingerprint(pcm: bytes, n_bins: int = 32) -> tuple[float, ...]:
    if len(pcm) < 4:
        return tuple(0.0 for _ in range(n_bins))
    n_samples = len(pcm) // 2
    samples = struct.unpack("<" + "h" * n_samples, pcm[: n_samples * 2])
    bin_size = max(1, n_samples // n_bins)
    values: list[float] = []
    for idx in range(n_bins):
        sl = samples[idx * bin_size : (idx + 1) * bin_size]
        if not sl:
            values.append(0.0)
            continue
        acc = sum(float(s) * float(s) for s in sl) / len(sl)
        values.append(math.sqrt(acc))
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return tuple(v / norm for v in values)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return float(sum(x * y for x, y in zip(a, b, strict=False)))


def _extract_pcm(src: Path, start: float, duration: float = 8.0) -> bytes:
    cmd = [
        FFMPEG,
        "-v",
        "error",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "-",
    ]
    completed = subprocess.run(
        cmd, check=True, capture_output=True, timeout=60
    )
    return completed.stdout


def fingerprint_near_dupes(
    src: Path,
    duration: float | None,
    *,
    threshold: float = 0.92,
    step_seconds: float = 45.0,
    window_seconds: float = 8.0,
) -> list[CandidateChunk]:
    """Optional near-dupe: later windows that match the preroll fingerprint.

    Uses coarse energy histograms (no chromaprint). Skips if ffmpeg missing.
    """
    if not shutil.which("ffmpeg") or duration is None:
        return []
    try:
        preroll_pcm = _extract_pcm(src, 12.0, window_seconds)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("fingerprint preroll extract failed: %s", exc)
        return []
    reference = _pcm_energy_fingerprint(preroll_pcm)
    chunks: list[CandidateChunk] = []
    t = 90.0
    while t + window_seconds < float(duration):
        try:
            pcm = _extract_pcm(src, t, window_seconds)
        except (OSError, subprocess.SubprocessError):
            t += step_seconds
            continue
        score = cosine(reference, _pcm_energy_fingerprint(pcm))
        if score >= threshold:
            chunks.append(
                CandidateChunk(
                    start=t,
                    end=t + window_seconds,
                    sources=["fingerprint_near_dupe"],
                    notes=f"cosine={score:.3f} vs preroll fingerprint",
                )
            )
        t += step_seconds
    return chunks


def chunk_id(show_id: str, start: float, end: float) -> str:
    digest = hashlib.sha1(f"{show_id}:{start:.2f}:{end:.2f}".encode()).hexdigest()[:10]
    return f"{show_id}_{int(start)}_{int(end)}_{digest}"
