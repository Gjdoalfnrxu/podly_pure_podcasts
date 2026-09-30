"""Offline CueDetector-scout + Gemini-confirm experiment.

Not wired into PodcastProcessor or Feed defaults. Safe to import from tests
and scripts; live Gemini calls are opt-in via env vars. Agents should follow
docs/experiments/AGENT_EVAL.md before changing this package or production
classifier/cue/audio defaults.
"""
