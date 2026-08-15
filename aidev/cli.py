"""``aidev`` command line.

    aidev run --repo ~/jokertest --prompt tasks/doctor.md --phase implement
    aidev report <run-id>|last
    aidev list
    aidev stats
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import __version__, reporter, runner
from .storage import (
    Database,
    RunStore,
    default_data_dir,
    find_running_run,
    list_run_dirs,
    make_run_id,
    read_live,
    read_telemetry,
    resolve_run_dir,
)
from .telemetry import PHASES, Telemetry

# Indirection so tests can drive the watch loop without real time passing.
_sleep = time.sleep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aidev",
        description="Telemetry runner for Claude Code sessions (v0.1).",
    )
    parser.add_argument("--version", action="version", version="aidev {0}".format(__version__))
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="where runs and aidev.db live (default: $AIDEV_DATA_DIR or ./data)",
    )
    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="run Claude Code and collect telemetry")
    run_cmd.add_argument("--repo", type=Path, required=True, help="repository Claude works in")
    run_cmd.add_argument("--prompt", type=Path, required=True, help="prompt file (markdown)")
    run_cmd.add_argument("--phase", choices=PHASES, default="implement")
    run_cmd.add_argument("--project", default=None, help="project label (default: repo dir name)")
    run_cmd.add_argument("--task", default=None, help="task label (default: prompt file stem)")
    run_cmd.add_argument("--max-turns", type=int, default=80)
    run_cmd.add_argument("--model", default=None)
    run_cmd.add_argument(
        "--permission-mode",
        default=None,
        help="passed to claude, e.g. acceptEdits / bypassPermissions",
    )
    run_cmd.add_argument("--allowed-tools", default=None, help="passed to claude --allowedTools")
    run_cmd.add_argument(
        "--disallowed-tools",
        default=None,
        help="comma separated tools to block, passed to claude --disallowedTools",
    )
    run_cmd.add_argument(
        "--safety",
        choices=sorted(runner.SAFETY_PROFILES),
        default="default",
        help="readonly blocks every mutating tool (Bash/Edit/Write/Task/...)",
    )
    run_cmd.add_argument("--resume", default=None, help="resume an existing session id")
    run_cmd.add_argument("--claude-bin", default="claude", help="claude executable (default: claude)")
    run_cmd.add_argument(
        "--claude-arg",
        action="append",
        default=[],
        dest="claude_args",
        help="extra raw argument for claude (repeatable)",
    )
    run_cmd.add_argument("--no-live", action="store_true", help="disable the live panel")
    run_cmd.add_argument(
        "--dry-run", action="store_true", help="print the command that would run and exit"
    )
    run_cmd.set_defaults(func=cmd_run)

    report_cmd = sub.add_parser("report", help="print the report of a stored run")
    report_cmd.add_argument("run_id", nargs="?", default="last")
    report_cmd.add_argument("--json", action="store_true", help="dump telemetry.json instead")
    report_cmd.set_defaults(func=cmd_report)

    watch_cmd = sub.add_parser(
        "watch", help="follow a running run from another terminal (read only)"
    )
    watch_cmd.add_argument(
        "run_id",
        nargs="?",
        default="last",
        help="run id, prefix, or 'last' (default: the newest RUNNING run)",
    )
    watch_cmd.add_argument(
        "--interval", type=float, default=0.4, help="refresh seconds, clamped to 0.3-0.5"
    )
    watch_cmd.add_argument("--once", action="store_true", help="draw a single frame and exit")
    watch_cmd.set_defaults(func=cmd_watch)

    list_cmd = sub.add_parser("list", help="list stored runs")
    list_cmd.add_argument("-n", "--limit", type=int, default=20)
    list_cmd.set_defaults(func=cmd_list)

    stats_cmd = sub.add_parser("stats", help="phase breakdown across recent runs")
    stats_cmd.add_argument("-n", "--limit", type=int, default=20)
    stats_cmd.set_defaults(func=cmd_stats)

    return parser


# ------------------------------------------------------------------- commands

def cmd_run(args: argparse.Namespace) -> int:
    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        print("error: --repo not found: {0}".format(repo), file=sys.stderr)
        return 2

    prompt_path = _resolve_prompt(args.prompt, repo)
    if prompt_path is None:
        print("error: --prompt not found: {0}".format(args.prompt), file=sys.stderr)
        return 2
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not prompt_text.strip():
        print("error: prompt file is empty: {0}".format(prompt_path), file=sys.stderr)
        return 2

    project = args.project or repo.name
    task = args.task or prompt_path.stem
    data_dir = _data_dir(args)
    run_id = make_run_id(task)

    cfg = runner.RunConfig(
        repo=repo,
        prompt_path=prompt_path,
        prompt=prompt_text,
        phase=args.phase,
        project=project,
        task=task,
        max_turns=args.max_turns,
        model=args.model,
        permission_mode=args.permission_mode,
        allowed_tools=args.allowed_tools,
        disallowed_tools=args.disallowed_tools,
        safety_profile=args.safety,
        resume_session=args.resume,
        claude_cmd=[args.claude_bin],
        extra_args=list(args.claude_args or []),
    )

    if args.dry_run:
        print(" ".join(runner.build_command(cfg)))
        print("cwd: {0}".format(repo))
        print("run dir: {0}".format(data_dir / "runs" / run_id))
        return 0

    telemetry = Telemetry(
        run_id=run_id,
        project=project,
        phase=args.phase,
        repo=str(repo),
        prompt_path=str(prompt_path),
        task=task,
        safety_profile=args.safety,
        disallowed_tools=runner.resolve_disallowed_tools(cfg) or "",
    )
    store = RunStore(data_dir / "runs", run_id)
    live = reporter.LiveReporter(enabled=not args.no_live)

    with store:
        store.write_prompt(prompt_text)
        store.write_run_json(_run_meta(cfg, telemetry, command=runner.build_command(cfg)))

        def on_event(event: Dict[str, Any], tel: Telemetry) -> None:
            snapshot = tel.snapshot()
            is_result = event.get("type") == "result"
            live.update(snapshot, force=is_result)
            # Publish for `aidev watch`. Throttled, except when usage turns FINAL.
            store.write_live(snapshot, force=is_result)

        first_snapshot = telemetry.snapshot()
        live.update(first_snapshot, force=True)
        store.write_live(first_snapshot, force=True)
        try:
            result = runner.execute(cfg, store, telemetry, on_event=on_event)
        except runner.ClaudeNotFound as exc:
            telemetry.finish("failed", None)
            store.write_telemetry(telemetry.to_dict())
            store.write_live(telemetry.snapshot(), force=True)
            print("error: {0}".format(exc), file=sys.stderr)
            return 127

        telemetry.finish(result.status, result.exit_code)
        live.finish(telemetry.snapshot())
        data = telemetry.to_dict()
        store.write_telemetry(data)
        store.write_run_json(_run_meta(cfg, telemetry, command=result.command))
        # Last: a watcher that sees a terminal status must find telemetry.json.
        store.write_live(telemetry.snapshot(), force=True)

    with Database(data_dir / "aidev.db") as db:
        db.save_run(data)

    print(reporter.render_final(data))
    print("saved: {0}".format(store.dir))
    return result.exit_code if result.exit_code is not None else 1


def cmd_report(args: argparse.Namespace) -> int:
    runs_root = _data_dir(args) / "runs"
    run_dir = resolve_run_dir(runs_root, args.run_id)
    if run_dir is None:
        print("error: no run matching '{0}' in {1}".format(args.run_id, runs_root), file=sys.stderr)
        return 2
    telemetry_path = run_dir / "telemetry.json"
    if not telemetry_path.exists():
        print("error: {0} has no telemetry.json".format(run_dir), file=sys.stderr)
        return 2
    data = json.loads(telemetry_path.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(reporter.render_final(data))
        print("run dir: {0}".format(run_dir))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Read-only follower. Never computes telemetry, never touches the runner."""
    runs_root = _data_dir(args) / "runs"
    run_dir = resolve_watch_target(runs_root, args.run_id)
    if run_dir is None:
        print("error: no run to watch in {0}".format(runs_root), file=sys.stderr)
        return 2

    interval = min(0.5, max(0.3, args.interval))
    live = reporter.LiveReporter(interval=0.0)
    payload: Optional[Dict[str, Any]] = None
    try:
        while True:
            fresh = read_live(run_dir)
            if fresh is not None:
                payload = fresh  # a bad read just keeps the previous frame
            if payload is None:
                # No live.json at all: an old run, or one that never started.
                data = read_telemetry(run_dir)
                if data is not None:
                    print(reporter.render_final(data))
                    return 0
                print("waiting for {0}/live.json ...".format(run_dir))
                if args.once:
                    return 0
                _sleep(interval)
                continue

            live.frame(reporter.render_watch(payload), force=True)
            if payload.get("status") != "running":
                break
            if args.once:
                return 0
            _sleep(interval)
    except KeyboardInterrupt:
        live.clear()
        print("\nwatch stopped. The run itself is unaffected.", file=sys.stderr)
        return 130

    live.clear()
    data = read_telemetry(run_dir)
    if data is not None:
        print(reporter.render_final(data))
    else:
        print("\nrun finished ({0}) but telemetry.json is not readable".format(payload.get("status")))
    return 0


def resolve_watch_target(runs_root: Path, run_id: Optional[str]) -> Optional[Path]:
    """``last``/empty means: the newest RUNNING run, else simply the newest one."""
    if run_id in (None, "", "last", "latest", "-"):
        return find_running_run(runs_root) or resolve_run_dir(runs_root, "last")
    return resolve_run_dir(runs_root, run_id)


def cmd_list(args: argparse.Namespace) -> int:
    data_dir = _data_dir(args)
    db_path = data_dir / "aidev.db"
    if db_path.exists():
        with Database(db_path) as db:
            print(reporter.render_run_list(db.recent_runs(args.limit)))
        return 0
    dirs = list_run_dirs(data_dir / "runs")[-args.limit :]
    for path in dirs:
        print(path.name)
    if not dirs:
        print("(no runs yet)")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = _data_dir(args) / "aidev.db"
    if not db_path.exists():
        print("(no runs yet)")
        return 0
    with Database(db_path) as db:
        print(reporter.render_phase_stats(db.phase_totals(args.limit), args.limit))
    return 0


# -------------------------------------------------------------------- helpers

def _data_dir(args: argparse.Namespace) -> Path:
    return (args.data_dir or default_data_dir()).expanduser()


def _resolve_prompt(prompt: Path, repo: Path) -> Optional[Path]:
    candidates = [prompt.expanduser()]
    if not prompt.is_absolute():
        candidates.append((Path.cwd() / prompt).resolve())
        candidates.append((repo / prompt).resolve())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _run_meta(cfg: runner.RunConfig, telemetry: Telemetry, command: List[str]) -> Dict[str, Any]:
    return {
        "run_id": telemetry.run_id,
        "aidev_version": __version__,
        "status": telemetry.status,
        "started_at": telemetry.started_at_iso,
        "finished_at": telemetry.finished_at_iso,
        "session_id": telemetry.session_id,
        "exit_code": telemetry.exit_code,
        "command": command,
        "config": cfg.to_dict(),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    reporter.configure_output(sys.stdout)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
