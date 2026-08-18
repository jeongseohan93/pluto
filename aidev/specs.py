"""함수 명세 규약, checked by machine rather than asked for in a prompt.

From this slice on, a function that is created or changed carries a spec: one
line saying what it does, one ``@param`` line per parameter, and - when it
branches or loops - an ``@flow`` line. ``verify`` checks the slice's own diff for
this and fails the stage when it is missing.

Two rules keep the check honest rather than merely noisy:

* **소급 금지.** Only functions the slice actually touched are looked at. Code
  nobody edited is never a violation, so the convention accumulates naturally
  instead of demanding a repository-wide rewrite up front.
* **A changed function needs a changed spec.** Editing the body and leaving the
  docstring word for word identical is the failure mode this is really aimed at:
  a spec that no longer describes the code is worse than none.

Test files are exempt (a fixture parameter list would drown the report in
``@param`` noise), as are nested functions, dunders, and anything under
``.aidev/``. A file that does not parse is skipped: a checker must never be the
reason a stage fails.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import workspace

# ``@param name`` / ``@param *args`` / ``@param **kwargs``
SPEC_PARAM_RE = re.compile(r"^\s*@param\s+(\*{0,2}[A-Za-z_]\w*)", re.MULTILINE)

_TEST_SEGMENTS = ("test", "tests", "spec", "__tests__")
# Only Python is checked. The convention is written for this repository first,
# and a JS/TS parser is a whole second implementation of this file.
_SUFFIXES = (".py",)
_EXEMPT_DIRS = (".aidev", "node_modules", ".venv", "venv", "build", "dist")

KIND_MISSING = "missing"
KIND_PARAM = "param"
KIND_UNCHANGED = "unchanged"


@dataclass
class Violation:
    """One function that changed without its spec keeping up."""

    file: str
    line: int
    func: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return "{0}:{1} - {2} - {3}: {4}".format(
            self.file, self.line, self.func, self.kind, self.detail
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "file": self.file,
            "line": self.line,
            "func": self.func,
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass
class FuncSpec:
    """A function as the checker sees it: where it is, what it takes, what it says."""

    name: str
    lineno: int
    end_lineno: int
    params: List[str]
    spec: str


def looks_like_test(path: str) -> bool:
    """Is this path a test file? The one rule, shared with ``pipeline.plan_scale``.

    @param path  a repository-relative path, with either separator
    """
    normalised = str(path).replace("\\", "/").lower()
    segments = normalised.split("/")
    if any(segment in _TEST_SEGMENTS for segment in segments):
        return True
    stem = segments[-1].rsplit(".", 1)[0]
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )


def is_exempt(path: str) -> bool:
    """Files the convention deliberately does not reach.

    @param path  a repository-relative path
    """
    normalised = str(path).replace("\\", "/")
    if not normalised.endswith(_SUFFIXES):
        return True
    segments = normalised.split("/")
    if any(segment in _EXEMPT_DIRS for segment in segments[:-1]):
        return True
    return looks_like_test(normalised)


# ------------------------------------------------------------------- the diff

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(diff: str) -> Dict[str, Set[int]]:
    """Which new-side line numbers a unified diff touches, per file.

    ``--unified=0`` is what makes this precise: a hunk header then names exactly
    the lines that changed, so an edit at the bottom of a file cannot be read as
    touching the function above it.

    @param diff  the output of ``git diff --unified=0``
    @flow  +++ header -> current file, @@ header -> that hunk's new lines
    주요 내부 변수: touched(파일->줄 집합), current(지금 읽고 있는 파일)
    """
    touched: Dict[str, Set[int]] = {}
    current: Optional[str] = None
    for line in (diff or "").splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current = None
            else:
                current = path[2:] if path.startswith(("a/", "b/")) else path
                touched.setdefault(current, set())
            continue
        if current is None or not line.startswith("@@"):
            continue
        match = _HUNK_RE.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        # count 0 is a pure deletion: nothing on the new side to attribute.
        touched[current].update(range(start, start + count))
    return touched


def changed_python_files(repo: Path, base: str, head: str) -> Dict[str, Set[int]]:
    """Files this slice edited, with the lines it edited, minus the exempt ones.

    @param repo  the repository (or worktree) to ask git about
    @param base  the commit the slice started from
    @param head  the commit to compare against, normally HEAD
    """
    diff = workspace.diff_unified(repo, base, head, paths=("*.py",))
    return {
        path: lines
        for path, lines in changed_lines(diff).items()
        if lines and not is_exempt(path)
    }


# ---------------------------------------------------------------- the functions


def _comment_block_above(lines: Sequence[str], lineno: int) -> str:
    """The run of ``#`` comments directly above a ``def``, oldest line first."""
    collected: List[str] = []
    index = lineno - 2  # 0-based, the line above the def
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped:
            break
        if not stripped.startswith("#"):
            break
        collected.append(stripped.lstrip("#").strip())
        index -= 1
    return "\n".join(reversed(collected))


def _params(node: ast.AST) -> List[str]:
    args = getattr(node, "args", None)
    if args is None:
        return []
    names: List[str] = []
    for arg in list(getattr(args, "posonlyargs", []) or []) + list(args.args):
        if arg.arg not in ("self", "cls"):
            names.append(arg.arg)
    if args.vararg is not None:
        names.append("*" + args.vararg.arg)
    for arg in args.kwonlyargs:
        names.append(arg.arg)
    if args.kwarg is not None:
        names.append("**" + args.kwarg.arg)
    return names


def functions(text: str) -> List[FuncSpec]:
    """Every top-level or method function in one file, with its spec text.

    Nested functions and dunders are left out: a closure's contract is its
    parent's, and ``__init__``'s is the class docstring's.

    @param text  the file's source
    @flow  ast.parse -> walk classes/functions -> docstring or the comment block above
    주요 내부 변수: found(수집), stack(qualname 접두사)
    """
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return []  # a checker must never be the reason a stage fails
    lines = (text or "").splitlines()
    found: List[FuncSpec] = []

    def visit(node: ast.AST, prefix: str, inside_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                if not inside_function and not _is_dunder(child.name):
                    spec = ast.get_docstring(child) or _comment_block_above(lines, child.lineno)
                    found.append(
                        FuncSpec(
                            name=name,
                            lineno=child.lineno,
                            end_lineno=int(getattr(child, "end_lineno", child.lineno) or child.lineno),
                            params=_params(child),
                            spec=(spec or "").strip(),
                        )
                    )
                visit(child, name + ".", True)
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".", inside_function)
            else:
                visit(child, prefix, inside_function)

    visit(tree, "", False)
    return found


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def normalise(spec: str) -> str:
    """Collapse whitespace so 'the spec changed' means the words changed.

    @param spec  a spec comment or docstring
    """
    return " ".join((spec or "").split())


# ---------------------------------------------------------------- the check


def check_text(
    path: str, new_text: str, touched: Set[int], old_text: Optional[str] = None
) -> List[Violation]:
    """The whole rule, over one file's before and after. No git, so it is testable.

    @param path      the file's repository-relative path, used in the message
    @param new_text  the file as it is now
    @param touched   line numbers the diff changed on the new side
    @param old_text  the file as it was, or None when it is new
    @flow  functions() -> touched? -> missing / param / unchanged
    주요 내부 변수: before(옛 명세 맵), violations(누적)
    """
    before = {func.name: normalise(func.spec) for func in functions(old_text or "")}
    violations: List[Violation] = []
    for func in functions(new_text):
        if not any(func.lineno <= line <= func.end_lineno for line in touched):
            continue  # 소급 금지: this slice did not touch it
        spec = func.spec.strip()
        if not spec:
            violations.append(
                Violation(
                    path,
                    func.lineno,
                    func.name,
                    KIND_MISSING,
                    "no spec comment - one line saying what it does, then @param per parameter",
                )
            )
            continue
        documented = set(SPEC_PARAM_RE.findall(spec))
        missing = [name for name in func.params if name not in documented]
        if missing:
            violations.append(
                Violation(
                    path,
                    func.lineno,
                    func.name,
                    KIND_PARAM,
                    "no @param line for: {0}".format(", ".join(missing)),
                )
            )
            continue
        old_spec = before.get(func.name)
        if old_spec is not None and old_spec == normalise(spec):
            violations.append(
                Violation(
                    path,
                    func.lineno,
                    func.name,
                    KIND_UNCHANGED,
                    "the body changed and the spec did not - update it, one line is enough",
                )
            )
    return violations


def check(repo: Path, base: str, head: str = "HEAD") -> List[Violation]:
    """Run the convention over everything this slice changed.

    @param repo  the repository or worktree the commits live in
    @param base  the commit the slice started from
    @param head  the commit to compare against
    @flow  changed_python_files -> per file: show old, read new -> check_text
    주요 내부 변수: violations(누적), touched(파일별 변경 줄)
    """
    violations: List[Violation] = []
    for path, touched in sorted(changed_python_files(repo, base, head).items()):
        new_text = workspace.show_file(repo, head, path)
        if new_text is None:
            try:
                new_text = (Path(repo) / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        old_text = workspace.show_file(repo, base, path)
        violations.extend(check_text(path, new_text, touched, old_text))
    return violations


# ------------------------------------------------------------------------ CLI
#
# So the implement stage can check itself before verify does, from inside the
# worktree, with no session cost:
#
#     python -m aidev.specs --base <the requirement commit>


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the violations of a commit range and exit non-zero if there are any.

    @param argv  command line arguments, defaulting to ``sys.argv[1:]``
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m aidev.specs",
        description="Check that every function this range changed carries a spec comment.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", default=None, help="the commit the work started from")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(list(argv) if argv is not None else None)

    base = args.base or os.environ.get("AIDEV_SPEC_BASE")
    if not base:
        print(
            "error: no base commit. Pass --base <rev>, or set AIDEV_SPEC_BASE.",
            file=sys.stderr,
        )
        return 2
    violations = check(args.repo, base, args.head)
    for violation in violations:
        print(str(violation))
    if not violations:
        print("spec check: clean ({0}..{1})".format(base[:12], args.head))
        return 0
    print("{0} spec violation(s)".format(len(violations)))
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
