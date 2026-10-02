"""Orchestrate auto gold: sample → candidates → whisper chunks → Gemini judge."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcast_processor.experiments.auto_gold.audio_ops import (
    chunk_id,
    extract_chunk_wav,
    ffmpeg_available,
    fingerprint_near_dupes,
    probe_duration_seconds,
    silencedetect_candidates,
)
from podcast_processor.experiments.auto_gold.candidates import propose_candidates
from podcast_processor.experiments.auto_gold.constants import (
    DEFAULT_JUDGE_BUDGET_USD,
    DEFAULT_WHISPER_MODEL,
    GOLD_FAMILY,
    JUDGE_KEY_ENV_NEEDED,
    SAMPLING_UNIT,
)
from podcast_processor.experiments.auto_gold.downloader import fetch_show_episode
from podcast_processor.experiments.auto_gold.judge import (
    GoldJudge,
    gemini_key_present,
    groq_key_present,
)
from podcast_processor.experiments.auto_gold.report import (
    default_template_path,
    write_report,
)
from podcast_processor.experiments.auto_gold.shows import (
    ShowListError,
    filter_shows,
    load_shows,
    prioritize_genre_coverage,
)
from podcast_processor.experiments.auto_gold.types import (
    CandidateChunk,
    EpisodeRef,
    PipelineResult,
    ShowResult,
    ShowSpec,
)
from podcast_processor.experiments.auto_gold.whisper_chunks import ChunkWhisper
from shared import defaults as DEFAULTS

logger = logging.getLogger(__name__)

GetFn = Callable[..., Any]


@dataclass
class AutoGoldConfig:
    output_dir: Path
    shows_path: Path | None = None
    download: bool = False
    offline: bool = False
    whisper_mode: str = "auto"
    judge_mode: str = "auto"
    enable_dsp: bool = False
    enable_fingerprint: bool = False
    include_dai_probes: bool = True
    max_download_bytes: int | None = None
    max_judge_usd: float = DEFAULT_JUDGE_BUDGET_USD
    whisper_model: str | None = None
    write_docs_report: bool = False
    genres: set[str] | None = None
    show_ids: set[str] | None = None
    require_representative: bool = True
    get: GetFn | None = None
    whisper: ChunkWhisper | None = None
    judge: GoldJudge | None = None


def _offline_episode(show: ShowSpec) -> EpisodeRef:
    return EpisodeRef(
        show=show,
        episode_title=f"[offline] {show.title}",
        audio_url=None,
        duration_seconds=1800.0,
        guid=f"offline:{show.show_id}",
        published=None,
        dai_likely="megaphone.fm" in show.rss_url,
        publisher_markers=[],
        rss_ok=False,
        error="offline mode: RSS not fetched",
    )


def run_auto_gold(config: AutoGoldConfig) -> PipelineResult:
    if GOLD_FAMILY != "general_podcast_ads":
        raise ShowListError("gold family lock broken in constants")
    shows = filter_shows(
        load_shows(config.shows_path),
        genres=config.genres,
        show_ids=config.show_ids,
        require_representative=config.require_representative,
    )
    whisper = config.whisper or ChunkWhisper(
        mode=config.whisper_mode,
        model_name=config.whisper_model or DEFAULT_WHISPER_MODEL,
    )
    judge = config.judge or GoldJudge(
        mode=config.judge_mode,
        max_usd=config.max_judge_usd,
    )
    blocked: list[str] = []
    notes: list[str] = []
    if config.offline:
        blocked.append("rss_fetch (offline)")
        blocked.append("audio_download (offline)")
    elif not config.download:
        blocked.append("audio_download (--download not set)")
    if whisper.backend == "stub":
        blocked.append(f"whisper ({whisper.detail})")
    if judge.mode == "dry-run":
        blocked.append(
            "gemini_judge (dry-run / no Gemini or Google key; "
            f"set {JUDGE_KEY_ENV_NEEDED})"
        )
    if groq_key_present():
        notes.append("GROQ_KEY is set in the environment and is unused by this judge.")
    notes.append(
        f"Gemini judge budget cap ${config.max_judge_usd:.2f}; "
        f"canonical env var {JUDGE_KEY_ENV_NEEDED}."
    )
    if not ffmpeg_available():
        notes.append(
            "ffmpeg/ffprobe missing; chunk extract / DSP / fingerprint skipped."
        )

    results: list[ShowResult] = []
    for show in prioritize_genre_coverage(shows):
        logger.info("auto-gold show %s (%s)", show.show_id, show.genre)
        try:
            results.append(_run_show(show, config, whisper, judge))
        except Exception as exc:  # noqa: BLE001
            logger.exception("auto-gold show %s failed: %s", show.show_id, exc)
            results.append(
                ShowResult(
                    show=show,
                    episode=EpisodeRef(
                        show=show,
                        episode_title="",
                        audio_url=None,
                        duration_seconds=None,
                        guid="",
                        published=None,
                        dai_likely=False,
                        rss_ok=False,
                        error=f"show failed: {exc}",
                    ),
                    audio_path=None,
                    candidates=[],
                    transcripts=[],
                    labels=[],
                    blocked=[f"show failed: {exc}"],
                )
            )
        _write_pipeline_outputs(
            _snapshot_payload(config, whisper, judge, results, blocked, notes),
            config,
        )
        logger.info(
            "auto-gold checkpoint %s/%s after %s",
            len(results),
            len(shows),
            show.show_id,
        )

    payload = _snapshot_payload(config, whisper, judge, results, blocked, notes)
    _write_pipeline_outputs(payload, config)
    return payload


def _snapshot_payload(
    config: AutoGoldConfig,
    whisper: ChunkWhisper,
    judge: GoldJudge,
    results: list[ShowResult],
    blocked: list[str],
    notes: list[str],
) -> PipelineResult:
    return PipelineResult(
        gold_family=GOLD_FAMILY,
        sampling_unit=SAMPLING_UNIT,
        whisper_backend=whisper.backend,
        judge_mode=judge.mode,
        groq_key_present_but_unused=groq_key_present(),
        gemini_key_present=gemini_key_present(),
        production_flag_enable_bow_scout_gemini_confirm=bool(
            DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM
        ),
        shows=results,
        blocked_steps=blocked,
        notes=notes,
        output_dir=config.output_dir,
        judge_spend_usd=float(judge.spent_usd),
        judge_budget_usd=float(judge.max_usd),
        judge_calls=int(judge.n_calls),
        judge_env_needed=JUDGE_KEY_ENV_NEEDED,
    )


def _write_pipeline_outputs(payload: PipelineResult, config: AutoGoldConfig) -> None:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    metrics = config.output_dir / "metrics.json"
    metrics.write_text(json.dumps(payload.as_dict(), indent=2) + "\n", encoding="utf-8")
    write_report(payload, config.output_dir / "BASELINE_REPORT.md")
    if config.write_docs_report:
        write_report(payload, default_template_path())


def _load_show_episode(
    show: ShowSpec,
    config: AutoGoldConfig,
) -> tuple[EpisodeRef, Path | None, list[str]]:
    blocked: list[str] = []
    if config.offline:
        return _offline_episode(show), None, ["offline"]
    episode, audio_path = fetch_show_episode(
        show,
        config.output_dir,
        download=config.download,
        get=config.get,
        max_bytes=config.max_download_bytes,
    )
    if not episode.rss_ok:
        blocked.append(episode.error or "rss failed")
    if config.download and audio_path is None:
        blocked.append(episode.error or "audio missing")
    if audio_path is not None:
        probed = probe_duration_seconds(audio_path)
        if probed is not None:
            episode.duration_seconds = probed
    return episode, audio_path, blocked


def _optional_audio_candidates(
    audio_path: Path | None,
    episode: EpisodeRef,
    config: AutoGoldConfig,
) -> tuple[list[CandidateChunk], list[str]]:
    extra: list[CandidateChunk] = []
    blocked: list[str] = []
    if config.enable_dsp:
        if audio_path is not None:
            extra.extend(silencedetect_candidates(audio_path, episode.duration_seconds))
        else:
            blocked.append("dsp_silence (no audio)")
    if config.enable_fingerprint:
        if audio_path is not None:
            extra.extend(fingerprint_near_dupes(audio_path, episode.duration_seconds))
        else:
            blocked.append("fingerprint (no audio)")
    return extra, blocked


def _extract_wav(
    audio_path: Path | None,
    dest: Path,
    chunk: CandidateChunk,
) -> tuple[Path | None, str | None]:
    if audio_path is None or not ffmpeg_available():
        return None, None
    try:
        return extract_chunk_wav(audio_path, dest, chunk.start, chunk.end), None
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract failed %s: %s", dest.name, exc)
        return None, f"extract {chunk.start:.0f}-{chunk.end:.0f}s"


def _run_show(
    show: ShowSpec,
    config: AutoGoldConfig,
    whisper: ChunkWhisper,
    judge: GoldJudge,
) -> ShowResult:
    episode, audio_path, show_blocked = _load_show_episode(show, config)
    extra, extra_blocked = _optional_audio_candidates(audio_path, episode, config)
    show_blocked.extend(extra_blocked)
    candidates = propose_candidates(
        episode,
        extra=extra,
        include_dai_probes=config.include_dai_probes,
    )

    transcripts = []
    labels = []
    wav_dir = config.output_dir / "chunks" / show.show_id
    for chunk in candidates:
        dest = wav_dir / f"{chunk_id(show.show_id, chunk.start, chunk.end)}.wav"
        wav_path, extract_error = _extract_wav(audio_path, dest, chunk)
        if extract_error:
            show_blocked.append(extract_error)
        transcript = whisper.transcribe_chunk(chunk, wav_path)
        transcripts.append(transcript)
        labels.append(judge.judge(transcript))

    return ShowResult(
        show=show,
        episode=episode,
        audio_path=audio_path,
        candidates=candidates,
        transcripts=transcripts,
        labels=labels,
        blocked=show_blocked,
    )
