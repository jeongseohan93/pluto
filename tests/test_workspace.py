import os
import shutil
import subprocess

import pytest

from aidev import pipeline, workspace

BASE_BRANCH = "windows-handoff-20260808"
GIT_ID = ["-c", "user.email=t@t", "-c", "user.name=t"]


def git(path, *args, check=True):
    return subprocess.run(
        ["git"] + GIT_ID + ["-C", str(path)] + list(args),
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "jokertest"
    path.mkdir()
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    git(path, "symbolic-ref", "HEAD", "refs/heads/{0}".format(BASE_BRANCH))
    (path / "README.md").write_text("jokertest\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path


def plan_for(repo, tmp_path, slice_id="20260816-doctor", base=None):
    return workspace.plan_workspace(
        repo, slice_id, "slice/" + slice_id, base=base, root=tmp_path / "wt"
    )


# ------------------------------------------------------------------ planning


def test_base_defaults_to_the_current_head_never_to_main(repo, tmp_path):
    git(repo, "branch", "main")
    plan = plan_for(repo, tmp_path)

    assert plan.base == BASE_BRANCH
    assert plan.base_commit == workspace.resolve_commit(repo, "HEAD")
    assert plan.branch == "slice/20260816-doctor"
    assert plan.path == tmp_path / "wt" / "20260816-doctor"


def test_an_explicit_base_wins_and_an_unknown_one_is_refused(repo, tmp_path):
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "only-on-main.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "on main")
    git(repo, "checkout", "-q", BASE_BRANCH)

    plan = plan_for(repo, tmp_path, base="main")
    assert plan.base == "main"
    assert plan.base_commit == workspace.resolve_commit(repo, "main")
    assert plan.base_commit != workspace.resolve_commit(repo, BASE_BRANCH)

    with pytest.raises(workspace.GitError):
        plan_for(repo, tmp_path, base="no-such-branch")


def test_a_detached_head_has_no_base_branch_but_still_has_a_commit(repo, tmp_path):
    git(repo, "checkout", "-q", "--detach", "HEAD")
    plan = plan_for(repo, tmp_path)
    assert plan.base is None
    assert plan.base_commit


def test_an_unborn_head_is_refused(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    with pytest.raises(workspace.GitError) as exc:
        plan_for(empty, tmp_path)
    assert "commit" in str(exc.value)


def test_the_default_root_sits_beside_the_repo(repo):
    root = workspace.default_worktree_root(repo)
    assert root.name == repo.name + workspace.DEFAULT_ROOT_SUFFIX
    assert root.parent == repo.parent  # never nested inside the repo it belongs to


# ------------------------------------------------------------------ blocking


def test_nothing_blocks_a_fresh_workspace(repo, tmp_path):
    assert workspace.blocking_reasons(repo, plan_for(repo, tmp_path)) == []


def test_an_occupied_path_blocks_and_says_what_is_there(repo, tmp_path):
    plan = plan_for(repo, tmp_path)
    plan.path.mkdir(parents=True)
    (plan.path / "leftover.txt").write_text("someone else's work\n", encoding="utf-8")

    reasons = workspace.blocking_reasons(repo, plan)
    assert len(reasons) == 1
    assert "already exists" in reasons[0]
    assert "leftover.txt" in reasons[0]
    assert "never reuses or cleans" in reasons[0]


def test_a_registered_worktree_at_that_path_names_its_branch(repo, tmp_path):
    plan = plan_for(repo, tmp_path, slice_id="20260816-other")
    workspace.worktree_add(repo, plan.path, "someone-elses-branch", plan.base_commit)

    reasons = workspace.blocking_reasons(repo, plan)
    assert any("already exists" in reason and "someone-elses-branch" in reason for reason in reasons)


def test_an_existing_branch_blocks_on_its_own(repo, tmp_path):
    plan = plan_for(repo, tmp_path)
    git(repo, "branch", plan.branch)
    reasons = workspace.blocking_reasons(repo, plan)
    assert len(reasons) == 1
    assert "branch already exists" in reasons[0]


def test_a_registration_without_a_directory_blocks_and_asks_for_a_human(repo, tmp_path):
    plan = plan_for(repo, tmp_path)
    ws = workspace.create(repo, plan)
    shutil.rmtree(str(ws.path))

    reasons = workspace.blocking_reasons(repo, plan)
    assert any("missing on disk" in reason for reason in reasons)
    # the branch is still there too, and we never prune on someone's behalf
    assert any("branch already exists" in reason for reason in reasons)


# ------------------------------------------------------------------ listing


def test_list_worktrees_reads_paths_branches_and_leftovers(repo, tmp_path):
    live = plan_for(repo, tmp_path, slice_id="20260816-live")
    workspace.create(repo, live)
    dead = plan_for(repo, tmp_path, slice_id="20260816-dead")
    workspace.create(repo, dead)
    shutil.rmtree(str(dead.path))

    entries = workspace.list_worktrees(repo)
    by_name = {entry.path.name: entry for entry in entries}
    assert entries[0].path.name == repo.name  # the main worktree comes first
    assert by_name["20260816-live"].branch == "slice/20260816-live"
    assert by_name["20260816-live"].head
    assert workspace.find_worktree(repo, live.path) is not None
    assert workspace.find_worktree(repo, tmp_path / "nowhere") is None

    stale = workspace.stale_worktrees(repo)
    if stale:  # 'prunable' annotations need git >= 2.36
        assert [entry.path.name for entry in stale] == ["20260816-dead"]
        assert stale[0].prunable


def test_verify_notices_a_gone_or_moved_worktree(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    assert workspace.verify(repo, ws) is None

    moved = workspace.Workspace(
        path=ws.path, branch="slice/somewhere-else", base=ws.base, base_commit=ws.base_commit
    )
    assert "is on" in (workspace.verify(repo, moved) or "")

    shutil.rmtree(str(ws.path))
    assert "gone" in (workspace.verify(repo, ws) or "")


# ------------------------------------------------------------------ removal
#
# Measured 2026-08-18: 'git worktree remove' unregistered a worktree and then
# failed to delete it, leaving a directory git no longer knew about - which the
# same command could never remove afterwards.


def test_removal_blockers_name_a_lock_and_the_command_that_clears_it(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    assert workspace.removal_blockers(repo, ws.path) == []

    git(repo, "worktree", "lock", str(ws.path))
    reasons = workspace.removal_blockers(repo, ws.path)
    assert len(reasons) == 1
    assert "locked" in reasons[0] and "worktree unlock" in reasons[0]

    git(repo, "worktree", "unlock", str(ws.path))
    assert workspace.removal_blockers(repo, ws.path) == []
    # an unregistered path blocks nothing: there is no git operation to attempt
    assert workspace.removal_blockers(repo, tmp_path / "nowhere") == []


def test_removal_blockers_notice_a_registration_with_nothing_behind_it(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    shutil.rmtree(str(ws.path))

    reasons = workspace.removal_blockers(repo, ws.path)
    assert len(reasons) == 1
    assert "missing on disk" in reasons[0] and "worktree prune" in reasons[0]


def test_repair_measures_whether_the_registration_really_came_back(repo, tmp_path):
    """The answer is measured with find_worktree, never assumed from git's exit code."""
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    admin = (ws.path / ".git").read_text(encoding="utf-8").split("gitdir:", 1)[-1].strip()

    # a worktree whose link is intact is trivially repairable
    assert workspace.repair_worktree(repo, ws.path) is True

    # and one whose admin directory is gone is not - which is not an error
    shutil.rmtree(admin, ignore_errors=True)
    assert workspace.find_worktree(repo, ws.path) is None
    assert workspace.repair_worktree(repo, ws.path) is False


def test_remove_tree_reports_instead_of_raising(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))

    assert workspace.remove_tree(tmp_path / "never-existed") is None  # already the goal
    assert workspace.remove_tree(ws.path) is None
    assert not ws.path.exists()

    a_file = tmp_path / "not-a-directory.txt"
    a_file.write_text("x\n", encoding="utf-8")
    assert isinstance(workspace.remove_tree(a_file), str)  # reported, not raised


# ---------------------------------------------------------------- committing


def test_commit_all_keeps_the_message_and_returns_the_new_head(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    (ws.path / "thing.py").write_text("x = 1\n", encoding="utf-8")

    sha = workspace.commit_all(ws.path, "slice(20260816-doctor): implement")
    assert sha == workspace.head_commit(ws.path)
    assert workspace.log_subjects(repo, "{0}..{1}".format(ws.base_commit, ws.branch)) == [
        "slice(20260816-doctor): implement"
    ]
    assert workspace.is_clean(ws.path)

    # an empty stage still gets its commit, so the branch has one per stage
    second = workspace.commit_all(ws.path, "slice(20260816-doctor): test")
    assert second != sha
    assert len(workspace.log_subjects(repo, "{0}..{1}".format(ws.base_commit, ws.branch))) == 2


def test_commit_all_can_force_a_gitignored_history_path(repo, tmp_path):
    (repo / ".gitignore").write_text(".aidev/\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore .aidev")

    ws = workspace.create(repo, plan_for(repo, tmp_path))
    history = ws.path / ".aidev" / "history" / "20260816-doctor"
    history.mkdir(parents=True)
    (history / "requirement.md").write_text("# doctor\n", encoding="utf-8")

    workspace.commit_all(ws.path, "slice(20260816-doctor): requirement",
                         force_paths=(".aidev/history/20260816-doctor",))
    listed = git(repo, "ls-tree", "-r", "--name-only", ws.branch).stdout
    assert ".aidev/history/20260816-doctor/requirement.md" in listed


def test_delete_branch_returns_the_commit_it_removed(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    (ws.path / "thing.py").write_text("x = 1\n", encoding="utf-8")
    sha = workspace.commit_all(ws.path, "slice(20260816-doctor): implement")

    workspace.worktree_remove(repo, ws.path)
    assert workspace.delete_branch(repo, ws.branch) == sha
    assert not workspace.branch_exists(repo, ws.branch)
    # the sha is what makes it recoverable, which is why it is returned
    assert workspace.resolve_commit(repo, sha) == sha


# -------------------------------------------------------------------- merge


def test_merge_lands_on_a_non_main_base(repo, tmp_path):
    ws = workspace.create(repo, plan_for(repo, tmp_path))
    (ws.path / "thing.py").write_text("x = 1\n", encoding="utf-8")
    sha = workspace.commit_all(ws.path, "slice(20260816-doctor): implement")

    result = workspace.merge_branch(repo, ws.branch, "slice(20260816-doctor): merge")
    assert result.ok
    assert result.conflicts == []
    assert (repo / "thing.py").read_text(encoding="utf-8") == "x = 1\n"
    contains = git(repo, "branch", "--contains", sha).stdout
    assert BASE_BRANCH in contains
    # --no-ff, so the merge itself is a commit a human can point at
    assert "slice(20260816-doctor): merge" in git(repo, "log", "--format=%s", "-1").stdout


def test_a_merge_conflict_is_aborted_and_reported_never_resolved(repo, tmp_path):
    (repo / "shared.txt").write_text("original\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "shared")

    ws = workspace.create(repo, plan_for(repo, tmp_path))
    (ws.path / "shared.txt").write_text("the slice's version\n", encoding="utf-8")
    workspace.commit_all(ws.path, "slice(20260816-doctor): implement")

    (repo / "shared.txt").write_text("the human's version\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "meanwhile")
    before = workspace.head_commit(repo)

    result = workspace.merge_branch(repo, ws.branch, "slice(20260816-doctor): merge")
    assert not result.ok
    assert result.conflicts == ["shared.txt"]
    # the repo is exactly as it was: no merge commit, no conflict markers left
    assert workspace.head_commit(repo) == before
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "the human's version\n"
    assert workspace.is_clean(repo)
    assert workspace.branch_exists(repo, ws.branch)


# ----------------------------------------------------------------- identity


def test_identity_is_injected_only_when_git_has_none(repo, monkeypatch):
    """A machine with an identity keeps it; one without still gets to commit."""
    monkeypatch.setattr(workspace, "_IDENTITY_CACHE", {})
    git(repo, "config", "user.name", "someone")
    git(repo, "config", "user.email", "someone@example.com")
    assert workspace.identity_args(repo) == []

    # A global identity would mask the other branch, so the lookup itself is what
    # gets emptied - this is the fresh-machine / CI case.
    monkeypatch.setattr(workspace, "_IDENTITY_CACHE", {})
    monkeypatch.setattr(workspace, "_config_value", lambda _repo, _name: "")
    args = workspace.identity_args(repo)
    assert "user.name=aidev" in args and "user.email=aidev@localhost" in args


# ------------------------------------------------------- declared commands


def test_split_command_keeps_windows_paths_intact():
    assert pipeline.split_command("npm ci --prefix backend") == ["npm", "ci", "--prefix", "backend"]
    quoted = pipeline.split_command('"C:\\Program Files\\node\\npm.cmd" ci')
    assert quoted[0] == "C:\\Program Files\\node\\npm.cmd" if os.name == "nt" else True
    if os.name == "nt":
        # POSIX splitting would eat the separators of a real Windows path
        assert pipeline.split_command(r"C:\tools\python.exe -V")[0] == r"C:\tools\python.exe"


def test_setup_tool_rules_grant_the_exact_and_the_prefix_form():
    rules = pipeline.setup_tool_rules("npm ci --prefix backend")
    assert rules == ("Bash(npm ci --prefix backend)", "Bash(npm ci:*)")
    assert pipeline.setup_tool_rules("bootstrap") == ("Bash(bootstrap)", "Bash(bootstrap:*)")
    assert pipeline.setup_tool_rules(None) == ()
    # setup: is one command among others now, granted by the same function
    assert pipeline.command_tool_rules("npm run test:guards") == (
        "Bash(npm run test:guards)",
        "Bash(npm run:*)",
    )


@pytest.mark.parametrize(
    "front, expected",
    [
        ("approval: none", None),
        ("approval: none\nsetup: npm ci --prefix backend", "npm ci --prefix backend"),
        ("setup: npm ci   # 백엔드 의존성", "npm ci"),
    ],
)
def test_setup_is_read_from_front_matter(front, expected):
    fields, _ = pipeline.parse_front_matter("---\n{0}\n---\nbody\n".format(front))
    assert pipeline.resolve_setup(fields) == expected


@pytest.mark.parametrize(
    "front",
    [
        "setup:",
        "setup: npm ci && rm -rf /",
        "setup: npm ci | tee log",
        "setup: npm ci; echo done",
        "setup: npm ci > out.txt",
        "setup: echo `whoami`",
        "setup: echo $(whoami)",
    ],
)
def test_a_setup_line_that_needs_a_shell_is_refused(front):
    fields, _ = pipeline.parse_front_matter("---\n{0}\n---\nbody\n".format(front))
    with pytest.raises(pipeline.PipelineError):
        pipeline.resolve_setup(fields)


def front_matter(front):
    fields, _ = pipeline.parse_front_matter("---\n{0}\n---\nbody\n".format(front))
    return fields


@pytest.mark.parametrize(
    "front, expected",
    [
        ("approval: none", ()),
        ("test_commands: npm run test:guards", ("npm run test:guards",)),
        (
            "test_commands: npm run test:guards, npm run lint",
            ("npm run test:guards", "npm run lint"),
        ),
        # repeated lines accumulate; setup: beside it stays last-wins
        (
            "setup: npm ci\ntest_commands: npm run test:guards\ntest_commands: npx vitest run",
            ("npm run test:guards", "npx vitest run"),
        ),
        ("test_commands: pytest -q   # 파이썬 쪽", ("pytest -q",)),
        # the same command twice is one rule, not two
        ("test_commands: pytest, pytest", ("pytest",)),
    ],
)
def test_test_commands_are_read_from_front_matter(front, expected):
    assert pipeline.resolve_test_commands(front_matter(front)) == expected


@pytest.mark.parametrize(
    "front",
    [
        "test_commands:",
        "test_commands: ,",
        "test_commands: npm test && rm -rf /",
        "test_commands: npm test | tee log",
        "test_commands: echo $(whoami)",
    ],
)
def test_a_test_command_that_needs_a_shell_is_refused(front):
    """Same rule as setup: a chained command could never match a permission rule."""
    with pytest.raises(pipeline.PipelineError):
        pipeline.resolve_test_commands(front_matter(front))


def test_repeated_lines_only_accumulate_for_the_keys_that_declared_it():
    fields = front_matter("approval: plan\napproval: none\nsetup: a\nsetup: b")
    assert fields["approval"] == "none"  # last-wins, unchanged
    assert fields["setup"] == "b"


@pytest.mark.parametrize(
    "front, expected",
    [
        ("approval: none", (None, {})),
        ("max_turns: 120", (120, {})),
        ("max_turns: implement=140", (None, {"implement": 140})),
        ("max_turns: implement=140, test=60", (None, {"implement": 140, "test": 60})),
        ("max_turns: 100, implement=140", (100, {"implement": 140})),
        # a dash and an underscore are the same key
        ("max-turns: implement=140", (None, {"implement": 140})),
        ("max_turns: implement=140   # 큰 slice", (None, {"implement": 140})),
        # the same stage twice with the same number is not a contradiction
        ("max_turns: implement=140 implement=140", (None, {"implement": 140})),
    ],
)
def test_resolve_max_turns_syntax(front, expected):
    assert pipeline.resolve_max_turns(front_matter(front)) == expected


@pytest.mark.parametrize(
    "front",
    [
        "max_turns:",  # meant to do something, does nothing
        "max_turns: planz=10",  # a typo must not be a stage nobody runs
        "max_turns: abc",
        "max_turns: 0",
        "max_turns: -5",
        "max_turns: implement=0",
        "max_turns: implement=140, implement=60",  # two answers for one stage
    ],
)
def test_a_max_turns_line_that_cannot_be_honoured_is_refused(front):
    with pytest.raises(pipeline.PipelineError):
        pipeline.resolve_max_turns(front_matter(front))


def test_the_stage_option_form_refuses_a_bare_number():
    """--max-turns-stage sets one stage; a bare number is what --max-turns is for."""
    assert pipeline.parse_turn_tokens(["implement=140"], allow_base=False) == (
        None,
        {"implement": 140},
    )
    with pytest.raises(pipeline.PipelineError):
        pipeline.parse_turn_tokens(["140"], allow_base=False)


def test_plan_scale_counts_files_and_tests():
    text = (
        "| `aidev/pipeline.py` | front matter |\n"
        "| `aidev/workspace.py` | push |\n"
        "| `./aidev/pipeline.py` | the same file again |\n"
        r"| `tests\test_pipeline.py` | new tests |" + "\n"
        "| `tests/test_epic.py` | one test |\n"
        "| `src/__tests__/guards.ts` | a suite |\n"
        "| `web/app.spec.tsx` | another |\n"
        "| `README.md` | prose |\n"
        "Prose that names no path at all.\n"
    )
    scale = pipeline.plan_scale(text)
    # the duplicate './aidev/pipeline.py' is the same file, counted once
    assert scale["files"] == 7
    assert scale["test_files"] == 4
    assert pipeline.plan_scale("") == {"files": 0, "test_files": 0}


def test_only_a_plan_over_the_threshold_gets_a_note():
    small = {"files": 3, "test_files": 1, "budget": 80, "warn": False}
    assert pipeline.plan_scale_note(small) == ""
    assert pipeline.plan_scale_note(None) == ""
    big = {"files": 18, "test_files": 6, "budget": 80, "warn": True}
    note = pipeline.plan_scale_note(big)
    assert "18 file(s), 6 of them tests" in note
    assert "max_turns: implement={0}".format(pipeline.PLAN_SUGGESTED_TURNS) in note
    assert "--max-turns-stage implement={0}".format(pipeline.PLAN_SUGGESTED_TURNS) in note


# ------------------------------------------------------------------- push
#
# --merge --push backs the merge up. Every test here pushes to a bare repo on
# disk, so nothing resolves a hostname.


@pytest.fixture
def bare_remote(repo, tmp_path):
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    git(repo, "remote", "add", "origin", str(path))
    return path


def test_push_branch_pushes_that_branch_and_nothing_else(repo, tmp_path, bare_remote):
    git(repo, "branch", "slice/20260816-doctor")
    workspace.push_branch(repo, "origin", BASE_BRANCH)

    refs = git(repo, "ls-remote", "origin").stdout
    assert "refs/heads/{0}".format(BASE_BRANCH) in refs
    assert "slice/20260816-doctor" not in refs
    # a backup, not a change of configuration
    assert workspace.branch_remote(repo, BASE_BRANCH) is None


def test_remote_lookups_answer_for_a_repo_with_no_remote(repo):
    assert workspace.remote_exists(repo, "origin") is False
    assert workspace.branch_remote(repo, BASE_BRANCH) is None


def test_pushing_to_a_remote_that_is_not_there_raises(repo, tmp_path):
    git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    assert workspace.remote_exists(repo, "origin") is True
    with pytest.raises(workspace.GitError):
        workspace.push_branch(repo, "origin", BASE_BRANCH)
