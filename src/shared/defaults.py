from __future__ import annotations

# Centralized default values for application configuration.
# Single source of truth for defaults across runtime, DB models, and Pydantic config.

# LLM defaults
LLM_DEFAULT_MODEL = "groq/openai/gpt-oss-120b"
OPENAI_DEFAULT_MAX_TOKENS = 4096
OPENAI_DEFAULT_TIMEOUT_SEC = 300
LLM_DEFAULT_MAX_CONCURRENT_CALLS = 3
LLM_DEFAULT_MAX_RETRY_ATTEMPTS = 5
LLM_ENABLE_TOKEN_RATE_LIMITING = False
LLM_MAX_INPUT_TOKENS_PER_CALL: int | None = None
LLM_MAX_INPUT_TOKENS_PER_MINUTE: int | None = None
ENABLE_BOUNDARY_REFINEMENT = True
ENABLE_WORD_LEVEL_BOUNDARY_REFINDER = False
ENABLE_LLM_CHAPTER_FALLBACK_TAGGING = False

# Whisper defaults
WHISPER_DEFAULT_TYPE = "groq"
WHISPER_LOCAL_MODEL = "base.en"
WHISPER_REMOTE_BASE_URL = "https://api.openai.com/v1"
WHISPER_REMOTE_MODEL = "whisper-1"
WHISPER_REMOTE_LANGUAGE = "en"
WHISPER_REMOTE_TIMEOUT_SEC = 600
WHISPER_REMOTE_CHUNKSIZE_MB = 24

WHISPER_GROQ_MODEL = "whisper-large-v3-turbo"
WHISPER_GROQ_LANGUAGE = "en"
WHISPER_GROQ_MAX_RETRIES = 0

# Processing defaults
PROCESSING_NUM_SEGMENTS_TO_INPUT_TO_PROMPT = 60
PROCESSING_MAX_OVERLAP_SEGMENTS = 30

# Output defaults
OUTPUT_FADE_MS = 3000
OUTPUT_MIN_AD_SEGMENT_SEPARATION_SECONDS = 60
OUTPUT_MIN_AD_SEGMENT_LENGTH_SECONDS = 14
OUTPUT_MIN_CONFIDENCE = 0.8

# App defaults
APP_BACKGROUND_UPDATE_INTERVAL_MINUTE = 30
APP_AUTOMATICALLY_WHITELIST_NEW_EPISODES = True
APP_NUM_EPISODES_TO_WHITELIST_FROM_ARCHIVE_OF_NEW_FEED = 1
APP_POST_CLEANUP_RETENTION_DAYS = 5
APP_ENABLE_PUBLIC_LANDING_PAGE = False
APP_USER_LIMIT_TOTAL: int | None = None
APP_AUTOPROCESS_ON_DOWNLOAD = False
APP_COST_RATE_PER_HOUR = 0.04

# Credits defaults
MINUTES_PER_CREDIT = 60

# Chapter filter defaults
AD_DETECTION_DEFAULT_STRATEGY = "llm"
CHAPTER_FILTER_DEFAULT_STRINGS = (
    "sponsor,advertisement,ad break,promo,brought to you by"
)

# Cloud fast lane (OpenAI-compatible transcription API). Groq whisper-large-v3-turbo:
# $0.04 per audio hour, 10 s minimum billed per request
# (https://console.groq.com/docs/speech-to-text, checked 2026-10-07).
CLOUD_LANE_BASE_URL = "https://api.groq.com/openai/v1"
CLOUD_LANE_MODEL = "whisper-large-v3-turbo"
CLOUD_LANE_USD_PER_HOUR = 0.04
CLOUD_LANE_MIN_BILLED_SECONDS = 10.0
CLOUD_LANE_CHUNKSIZE_MB = 24
CLOUD_LANE_TIMEOUT_SEC = 120
CLOUD_LANE_CONCURRENCY = 2

# Stage pipeline (app/pipeline.py). Env overrides: PODLY_TRANSCRIBE_WORKERS,
# PODLY_LLM_WORKERS, PODLY_AUDIO_CUT_CONCURRENCY.
# Local Whisper uses most of the CPU, so one transcription at a time.
PIPELINE_TRANSCRIBE_WORKERS = 1
# Ad detection + audio cut. Each job's chunks stay sequential; this is how many
# episodes are in the LLM stage at once. Capped at LLM_MAX_CONCURRENT_CALLS when
# that is lower (app/pipeline_workers.py), so no chunk waits on another episode.
PIPELINE_LLM_WORKERS = 4
# A job a restart finds running is re-queued at most this many times; the next
# interruption fails it (an episode that OOM-kills the container must not loop).
PIPELINE_MAX_RESTART_REQUEUES = 2
# ffmpeg re-encode of a 60 min episode measured ~30 s wall / ~47 s CPU
# (upstream 2.5.0 image, ffmpeg 7.1), so cuts run one at a time.
PIPELINE_AUDIO_CUT_CONCURRENCY = 1
