"""ai-dev-orchestrator - telemetry runner and slice pipeline for Claude Code.

v0.1 scope: run Claude Code headless, collect stream-json line by line,
preserve raw events, aggregate tool/file telemetry, print a terminal report.
v0.2 adds the slice pipeline: one requirement driven through
plan -> approval gate -> implement -> test, with state.json in the target repo.
v0.3 adds workspace isolation: every slice runs in a git worktree on its own
branch with one commit per stage, so the user's checkout is only ever written by
an explicit merge.
v0.4 adds the epic planner: one epic is decomposed into a slice list, a human
approves that list once, and the slices then run as a sequential queue - each
one the ordinary v0.3 flow, each cut from the branch of the one before it.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
