"""The only job of this module: run Claude Code and stream its stdout.

    claude -p --output-format stream-json --verbose --max-turns 80

The prompt goes in through stdin, stdout is read line by line, and every line is
written to ``events.jsonl`` *before* it is parsed. stderr is drained on a helper
thread so a chatty CLI can never deadlock the pipe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import events as ev
from .storage import RunStore
from .telemetry import Telemetry

EventHook = Callable[[Dict[str, Any], Telemetry], None]

# Safety profiles are enforced by Claude Code itself through --disallowedTools,
# not by us. ``readonly`` also blocks Task, because a subagent would otherwise
# inherit the ability to run Bash and edit files.
SAFETY_PROFILES: Dict[str, tuple] = {
    "default": (),
    "readonly": (
        "Bash",
        "BashOutput",
        "KillBash",
        "KillShell",
        "Edit",
        "MultiEdit",
        "Write",
        "NotebookEdit",
        "Task",
        "SlashCommand",
    ),
}


class ClaudeNotFound(RuntimeError):
    pass


@dataclass
class RunConfig:
    repo: Path
    prompt_path: Path
    prompt: str
    phase: str = "implement"
    project: str = ""
    task: str = ""
    max_turns: int = 80
    model: Optional[str] = None
    permission_mode: Optional[str] = None
    allowed_tools: Optional[str] = None
    disallowed_tools: Optional[str] = None
    safety_profile: str = "default"
    resume_session: Optional[str] = None
    claude_cmd: List[str] = field(default_factory=lambda: ["claude"])
    extra_args: List[str] = field(default_factory=list)
    # A settings file the CLI is pointed at with --settings. The pipeline writes
    # one per slice to carry its hooks; the target repo's own .claude/settings.json
    # is never touched.
    settings_path: Optional[str] = None
    # Extra environment for the child. Values are deliberately not stored in
    # run.json - only the key names, so a path or a token cannot leak into it.
    env: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """What run.json records about this run - every field except the prompt.

        ``env`` is deliberately the sorted *key names* and not the values: the
        environment carries paths and could one day carry a token, and run.json
        is written to disk beside the events.
        """
        return {
            "repo": str(self.repo),
            "prompt_path": str(self.prompt_path),
            "phase": self.phase,
            "project": self.project,
            "task": self.task,
            "max_turns": self.max_turns,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "allowed_tools": self.allowed_tools,
            "disallowed_tools": self.disallowed_tools,
            "safety_profile": self.safety_profile,
            "effective_disallowed_tools": resolve_disallowed_tools(self),
            "resume_session": self.resume_session,
            "claude_cmd": self.claude_cmd,
            "extra_args": self.extra_args,
            "settings_path": self.settings_path,
            "env": sorted(self.env),
        }


@dataclass
class RunResult:
    exit_code: Optional[int]
    status: str  # completed | failed | interrupted
    command: List[str]


def resolve_disallowed_tools(cfg: RunConfig) -> Optional[str]:
    """Safety profile tools plus whatever the caller asked for, order preserved."""
    tools: List[str] = []
    for name in SAFETY_PROFILES.get(cfg.safety_profile, ()):
        if name not in tools:
            tools.append(name)
    for name in (cfg.disallowed_tools or "").split(","):
        name = name.strip()
        if name and name not in tools:
            tools.append(name)
    return ",".join(tools) or None


def build_command(cfg: RunConfig) -> List[str]:
    """The headless ``claude`` argv this run needs. Order is the CLI's, not ours.

    @param cfg  everything the run was configured with
    """
    cmd = list(cfg.claude_cmd) + [
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(cfg.max_turns),
    ]
    if cfg.model:
        cmd += ["--model", cfg.model]
    if cfg.permission_mode:
        cmd += ["--permission-mode", cfg.permission_mode]
    if cfg.allowed_tools:
        cmd += ["--allowedTools", cfg.allowed_tools]
    if cfg.settings_path:
        cmd += ["--settings", str(cfg.settings_path)]
    disallowed = resolve_disallowed_tools(cfg)
    if disallowed:
        cmd += ["--disallowedTools", disallowed]
    if cfg.resume_session:
        cmd += ["--resume", cfg.resume_session]
    cmd += list(cfg.extra_args)
    return cmd


def resolve_command(cmd: List[str]) -> List[str]:
    """Absolute-path the executable; wrap .cmd/.bat shims for Windows."""
    if not cmd:
        raise ClaudeNotFound("empty command")
    exe = cmd[0]
    resolved = shutil.which(exe) or (exe if Path(exe).exists() else None)
    if resolved is None:
        raise ClaudeNotFound(
            "'{0}' not found on PATH. Install Claude Code or pass --claude-bin.".format(exe)
        )
    argv = [resolved] + list(cmd[1:])
    if os.name == "nt" and resolved.lower().endswith((".cmd", ".bat")):
        argv = ["cmd", "/c"] + argv
    return argv


def execute(
    cfg: RunConfig,
    store: RunStore,
    telemetry: Telemetry,
    on_event: Optional[EventHook] = None,
) -> RunResult:
    """Run Claude to completion, feeding every event into ``telemetry``.

    @param cfg        the run's configuration, including any extra environment
    @param store      where raw events, stderr and run.json are written
    @param telemetry  the accumulator every event is fed to
    @param on_event   optional per-event hook, used for live views and snapshots
    """
    command = resolve_command(build_command(cfg))

    process = subprocess.Popen(
        command,
        cwd=str(cfg.repo),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        # None keeps the child inheriting this process's environment exactly, so
        # a run that declares nothing behaves as it always did.
        env=({**os.environ, **cfg.env} if cfg.env else None),
    )

    stderr_thread = threading.Thread(
        target=_drain_stderr, args=(process, store), daemon=True
    )
    stderr_thread.start()

    status = "completed"
    try:
        _write_prompt(process, cfg.prompt)
        for line in process.stdout:
            store.write_raw_event(line)  # raw first, always
            event = ev.parse_line(line)
            if event is None:
                telemetry.note_malformed_line()
                continue
            telemetry.ingest(event)
            if on_event is not None:
                on_event(event, telemetry)
        exit_code = process.wait()
    except KeyboardInterrupt:
        status = "interrupted"
        _terminate(process)
        exit_code = process.poll()
    finally:
        if process.stdout is not None:
            process.stdout.close()
        stderr_thread.join(timeout=2.0)

    if status != "interrupted" and (exit_code != 0 or telemetry.is_error):
        status = "failed"
    return RunResult(exit_code=exit_code, status=status, command=command)


def _write_prompt(process: "subprocess.Popen[str]", prompt: str) -> None:
    if process.stdin is None:
        return
    try:
        process.stdin.write(prompt)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass


def _drain_stderr(process: "subprocess.Popen[str]", store: RunStore) -> None:
    if process.stderr is None:
        return
    try:
        for line in process.stderr:
            store.write_stderr(line)
    except (ValueError, OSError):
        pass
    finally:
        try:
            process.stderr.close()
        except (ValueError, OSError):
            pass


def _terminate(process: "subprocess.Popen[str]") -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    except OSError:
        pass
