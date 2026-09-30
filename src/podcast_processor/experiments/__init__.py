"""Offline CueDetector-scout + Gemini-confirm experiment.

Not wired into PodcastProcessor or Feed defaults. Safe to import from tests
and scripts; live Gemini/Groq calls are opt-in via env vars and a hard daily
budget. Agents should follow docs/experiments/AGENT_EVAL.md (daily loop
contract, frozen gates, fold rules) before changing this package or
production classifier/cue/audio defaults.
"""
