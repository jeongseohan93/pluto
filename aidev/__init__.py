"""ai-dev-orchestrator - telemetry runner and slice pipeline for Claude Code.

v0.1 scope: run Claude Code headless, collect stream-json line by line,
preserve raw events, aggregate tool/file telemetry, print a terminal report.
v0.2 adds the slice pipeline: one requirement driven through
plan -> approval gate -> implement -> test, with state.json in the target repo.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
