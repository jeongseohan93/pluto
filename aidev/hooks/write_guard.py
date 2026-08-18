"""PreToolUse hook: Write may create a file, never rewrite one.

Measured 2026-08-16/17: a stage that changes three lines of a 900-line file by
calling Write re-emits the whole file as *output* tokens - the most expensive
kind there is - and does it again on the next correction. Edit sends the changed
lines. Asking for that in the prompt is a request; this is a mechanism.

The rule is the whole hook: if the path already exists, the call is refused and
the model is told to use Edit. If it does not, Write is exactly the right tool
and the call goes through.

This script is executed by the Claude Code CLI, in the target repository, with
whatever Python is on PATH there. It therefore imports nothing from ``aidev``
and **fails open**: any exception, any input shape it does not recognise, any
future change to the hook schema exits 0. A broken guard must cost the
pipeline nothing more than the guard itself.

    exit 0   allow
    exit 2   block, and stderr goes to the model
"""

from __future__ import annotations

import json
import os
import sys

BLOCK = 2
ALLOW = 0

MESSAGE = """\
Write is blocked for a file that already exists:
    {path}
Use Edit (or MultiEdit) to change it - that sends only the changed lines.
Write is allowed only for creating a new file.
"""


def decide(payload, cwd=None):
    """Should this tool call be blocked? Returns the message, or an empty string.

    @param payload  the hook's JSON input, already parsed
    @param cwd      directory a relative file_path is resolved against
    @flow  tool name -> file_path -> exists? -> message
    주요 내부 변수: target(절대 경로로 만든 대상 파일)
    """
    if not isinstance(payload, dict):
        return ""
    if payload.get("tool_name") != "Write":
        return ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path.strip():
        return ""
    base = cwd or payload.get("cwd") or os.getcwd()
    target = path if os.path.isabs(path) else os.path.join(str(base), path)
    if not os.path.exists(target):
        return ""
    return MESSAGE.format(path=os.path.abspath(target))


def main(argv=None):
    """Read the hook payload from stdin and answer with an exit code.

    @param argv  unused; present so this can be called like any other entry point
    """
    if os.environ.get("AIDEV_WRITE_GUARD", "").strip().lower() in ("off", "0", "false", "no"):
        return ALLOW
    try:
        raw = sys.stdin.read()
        message = decide(json.loads(raw) if raw.strip() else {})
    except Exception:  # fail open, on purpose - see the module docstring
        return ALLOW
    if not message:
        return ALLOW
    sys.stderr.write(message)
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
