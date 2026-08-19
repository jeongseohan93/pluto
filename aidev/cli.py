"""``aidev`` command line.

    aidev run --repo ~/jokertest --prompt tasks/doctor.md --phase implement
    aidev pipeline --repo ~/jokertest --requirement tasks/doctor.md
    aidev report <run-id>|last
    aidev list
    aidev stats
    aidev graph build --repo ~/jokertest
    aidev graph show run_pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import __version__, graph, pipeline, reporter, runner, verify, workspace
from .storage import (
    Database,
    RunStore,
    default_data_dir,
    find_running_run,
    is_stale,
    list_run_dirs,
    live_age,
    make_run_id,
    read_live,
    read_telemetry,
    resolve_run_dir,
    runs_for_repo,
)
from .telemetry import PHASES, Telemetry

# Indirection so tests can drive the watch loop without real time passing.
_sleep = time.sleep


def build_parser() -> argparse.ArgumentParser:
    """Every parser in one place, watch's --repo and graph's verbs included. Takes no arguments."""
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

    pipeline.add_parser(sub)
    graph.add_parser(sub)

    verify_cmd = sub.add_parser(
        "verify",
        help="run this slice's verification commands and print only a summary",
    )
    verify_cmd.add_argument(
        "--command",
        action="append",
        default=[],
        dest="commands",
        metavar="CMD",
        help="a verification command (repeatable; default: what the slice declared)",
    )
    verify_cmd.add_argument("--cwd", type=Path, default=None, help="where to run them")
    verify_cmd.add_argument(
        "--log-dir", type=Path, default=None, help="where the full output is written"
    )
    verify_cmd.set_defaults(func=cmd_verify)

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
    watch_cmd.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="only consider runs from this repository and its slice worktrees "
        "(applies to 'last'; an explicit run id always wins)",
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
    """One headless session end to end: everything it produced survives in the run dir.

    The order at the close is deliberate - telemetry.json is on disk before the
    final live.json, so a watcher that sees a terminal status can always read it.

    @param args  parsed arguments: --repo, --prompt, --phase, --dry-run, the runner's knobs
    @flow  repo/prompt checks -> RunConfig -> dry-run? print and stop
           : execute, streaming each event to the live view and live.json
           -> finish -> telemetry + run.json -> sqlite -> final report
    주요 내부 변수: cfg(런 설정), store(run dir 기록), telemetry(진행 상태), result(StageRun)
    """
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


def cmd_verify(args: argparse.Namespace) -> int:
    """Run the declared verification commands and print the diet, not the output.

    This is what the implement stage is granted instead of the raw test command.
    The full output goes to a log file; what comes back into the session is the
    failures with their locations and the successes as a number.

    @param args  parsed arguments: --command, --cwd, --log-dir
    @flow  argv or AIDEV_VERIFY_* env -> run_commands -> summarize_for_agent -> exit code
    주요 내부 변수: commands(실행할 명령들), result(VerifyResult)
    """
    commands = list(args.commands or [])
    if not commands:
        declared = os.environ.get("AIDEV_VERIFY_COMMANDS", "")
        try:
            parsed = json.loads(declared) if declared.strip() else []
        except ValueError:
            parsed = []
        commands = [str(item) for item in parsed if str(item).strip()]
    if not commands:
        print(
            "error: no verification command.\n"
            "    'aidev verify' is what a pipeline stage runs: the slice's declared\n"
            "    test_commands reach it through AIDEV_VERIFY_COMMANDS.\n"
            "    Outside a stage, name them: aidev verify --command 'pytest -q'",
            file=sys.stderr,
        )
        return 2
    cwd = args.cwd or Path(os.environ.get("AIDEV_VERIFY_CWD") or Path.cwd())
    log_dir = args.log_dir or (
        Path(os.environ["AIDEV_VERIFY_LOG_DIR"]) if os.environ.get("AIDEV_VERIFY_LOG_DIR") else None
    )
    result = verify.run_commands(commands, Path(cwd), log_dir=log_dir)
    print(verify.summarize_for_agent(result))
    return verify.worst_exit_code(result)


def cmd_report(args: argparse.Namespace) -> int:
    """Re-render a finished run from what it left on disk - nothing is recomputed.

    @param args  parsed arguments: run_id (a prefix is enough), --json, --data-dir
    @flow  resolve the run dir -> no telemetry.json -> exit 2 ; --json -> raw dump : rendered report
    """
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
    """Read-only follower. Never computes telemetry, never touches the runner.

    @param args  parsed arguments: run_id, --repo, --interval, --once
    @flow  pick a target -> frame -> finished? stop : stale? report and stop : poll
    주요 내부 변수: payload(마지막으로 성공한 live.json), interval(폴링 간격)
    """
    runs_root = _data_dir(args) / "runs"
    repo = getattr(args, "repo", None)
    run_dir = resolve_watch_target(runs_root, args.run_id, repo.expanduser() if repo else None)
    if run_dir is None:
        where = "{0}{1}".format(
            runs_root, " for repo {0}".format(repo) if repo else ""
        )
        print("error: no run to watch in {0}".format(where), file=sys.stderr)
        return 2

    interval = min(0.5, max(0.3, args.interval))
    live = reporter.LiveReporter(interval=0.0)
    payload: Optional[Dict[str, Any]] = None
    stalled = False
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
            if is_stale(payload, run_dir):
                # Polling a run nobody is updating is an infinite loop, which is
                # what this did before 2026-08-18. Say so and stop; the run
                # itself is not ours to touch either way.
                stalled = True
                break
            if args.once:
                return 0
            _sleep(interval)
    except KeyboardInterrupt:
        live.clear()
        print("\nwatch stopped. The run itself is unaffected.", file=sys.stderr)
        return 130

    live.clear()
    if stalled:
        print(
            "\nSTALE: no update for {0:.0f}s - there is no runner behind this run, so "
            "there is nothing left to follow.\n  {1}".format(
                live_age(payload, run_dir) or 0.0, run_dir
            )
        )
    data = read_telemetry(run_dir)
    if data is not None:
        print(reporter.render_final(data))
    elif not stalled:
        print("\nrun finished ({0}) but telemetry.json is not readable".format(payload.get("status")))
    return 0


def resolve_watch_target(
    runs_root: Path,
    run_id: Optional[str],
    repo: Optional[Path] = None,
    now: Optional[float] = None,
) -> Optional[Path]:
    """Which run ``watch`` attaches to. An explicit id always wins.

    ``last`` prefers a run that is *actually* running - measured 2026-08-18, it
    attached to one that had been dead for two days, because a killed runner
    leaves live.json saying ``running``. A stale-running run is therefore the
    last thing offered, not the first, and never silently.

    @param runs_root  where runs are stored
    @param run_id     an id, a prefix, ``last``, or nothing
    @param repo       limit the ``last`` search to this repository's runs
    @param now        the current time, for tests
    @flow  explicit id -> live running -> newest not-stale-running -> newest in scope
    주요 내부 변수: candidates(스코프 안의 run들), root(worktree 상위 경로)
    """
    if run_id not in (None, "", "last", "latest", "-"):
        return resolve_run_dir(runs_root, run_id)
    root = workspace.default_worktree_root(repo) if repo is not None else None
    running = find_running_run(runs_root, repo, root, now)
    if running is not None:
        return running
    candidates = runs_for_repo(runs_root, repo, root)
    for path in reversed(candidates):
        live = read_live(path)
        if live is not None and live.get("status") == "running" and is_stale(live, path, now):
            continue  # the dead run this whole ordering exists to skip
        return path
    # Nothing but stale-running runs: still better than "no run to watch", and
    # the loop reports the staleness rather than following it forever.
    return candidates[-1] if candidates else None


def cmd_list(args: argparse.Namespace) -> int:
    """Recent runs, from the index when there is one and from the directories when there is not.

    @param args  parsed arguments: --limit, --data-dir
    @flow  aidev.db present -> rendered table ; absent -> the last N run dir names ; none -> say so
    """
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
    """Where the time and tokens went, by phase. Needs the index - dirs alone cannot answer this.

    @param args  parsed arguments: --limit (how many recent runs feed the totals), --data-dir
    @flow  no aidev.db -> say there are no runs ; else phase totals over the last N runs
    """
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
    """The one entry point every ``aidev`` command arrives through; returns the process exit code.

    Ctrl-C is caught here so an interrupt reads as a line and 130, never as a traceback.

    @param argv  the arguments to parse; ``None`` means take them from the command line
    @flow  configure output -> parse -> no subcommand? help and 1 : dispatch ; Ctrl-C -> 130
    """
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
