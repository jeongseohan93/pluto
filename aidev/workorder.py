"""작업 지시서: the fixed-column table a plan declares its scope in.

Measured: the most expensive thing about an unattended implement stage is not
what it got wrong, it is what it was *allowed to interpret*. A plan written in
prose leaves room, and room is where a session invents a file nobody asked for.

So a plan carries one more section - a fixed-column markdown table naming every
file the work touches, with one of three verbs against it:

    ## 작업 지시서

    | 동사 | 대상 경로 | symbol | 책임 |
    | --- | --- | --- | --- |
    | CREATE | aidev/workorder.py |  | 지시서 파싱과 사후 대조 |
    | MODIFY | aidev/pipeline.py | run_pipeline | scope check 호출 지점 |
    | REFERENCE | aidev/verify.py |  | 검증 결과 구조 |

and the engine checks it twice: once before the plan may be committed, and again
against the real diff before every stage that changed a file may be committed.

Everything here is deterministic string work. **No model ever parses this** -
that is the whole point of a fixed-column table - and nothing in this module
reads state.json, spawns git, or imports ``pipeline``. The two functions that do
touch the filesystem (``escapes_root``) and the DB (nothing here) are kept apart
on purpose, so the parser can be unit-tested without a repository at all.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# The three verbs, and nothing else. CREATE is a file that does not exist yet,
# MODIFY one that does, REFERENCE one that is only read - REFERENCE is an input
# list for the context tray, never a write permission and never a read allowlist.
VERB_CREATE = "CREATE"
VERB_MODIFY = "MODIFY"
VERB_REFERENCE = "REFERENCE"
VERBS: Tuple[str, ...] = (VERB_CREATE, VERB_MODIFY, VERB_REFERENCE)

# What a heading has to say, once '#', spaces and a trailing ':' are stripped.
ORDER_HEADINGS: Tuple[str, ...] = ("작업 지시서", "work order")
REASON_HEADINGS: Tuple[str, ...] = ("범위 밖 수정 사유", "unplanned changes")

ORDER_COLUMNS: Tuple[str, ...] = ("동사", "대상 경로", "symbol", "책임")
REASON_COLUMNS: Tuple[str, ...] = ("경로", "사유")

# The one column whose header spelling is not case-sensitive: it is an English
# word inside a Korean header row, so 'Symbol' and 'symbol' are the same word.
_LOOSE_COLUMNS = frozenset({"symbol"})

# Windows refuses these names whatever the extension, so a path carrying one
# cannot be created on half the machines this tool runs on.
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM{0}".format(n) for n in range(1, 10)]
    + ["LPT{0}".format(n) for n in range(1, 10)]
)

_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*)$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


@dataclass
class Item:
    """One row of a work order: a verb, a path, an optional symbol, a duty."""

    verb: str
    path: str
    symbol: str = ""
    note: str = ""
    #: Which line of the document this came from, so a refusal can point at it.
    row: int = 0

    def to_dict(self) -> Dict[str, str]:
        """How state.json remembers one row - the line number is not part of it.

        The row number belongs to the document that was parsed, and state.json
        outlives that document: a plan edited at the gate moves every number.
        """
        return {"verb": self.verb, "path": self.path, "symbol": self.symbol, "note": self.note}

    @property
    def key(self) -> Tuple[str, str, str]:
        """What makes two rows the same row: the verb, the path and the symbol."""
        return (self.verb, self.path, self.symbol)


@dataclass
class Order:
    """A parsed work order, and every reason it cannot be accepted."""

    items: List[Item] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    #: Whether a section was recognised at all - absent and malformed differ.
    present: bool = False


@dataclass
class Reasons:
    """A parsed '범위 밖 수정 사유' table: ``(path, reason)`` rows, and its errors."""

    rows: List[Tuple[str, str]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    present: bool = False

    @property
    def paths(self) -> List[str]:
        """Just the paths, in the order they were declared - what the match is over."""
        return [path for path, _ in self.rows]


@dataclass
class ScopeVerdict:
    """What the final diff looks like once the work order has judged it."""

    ok: bool = True
    failures: List[str] = field(default_factory=list)
    #: Existing files changed without being declared - not a failure, a declaration debt.
    unplanned: List[str] = field(default_factory=list)
    #: New files nobody declared, and deletions nobody declared. Both are refusals.
    added: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    #: Paths the order did account for.
    declared: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- document


def mask_fenced(text: str) -> str:
    """Blank out every line inside a fenced code block, keeping the line count.

    A prompt or a plan quotes the table format inside a fence to explain it, and
    a heading inside a fence is an example rather than a section. Blanking
    instead of deleting is what keeps the reported line numbers honest.

    @param text  the whole document
    @flow  per line: inside a fence -> blank, closing? leave ; opening fence -> blank ; else keep
    주요 내부 변수: fence(열려 있는 펜스 문자열), out(마스킹된 줄들)
    """
    out: List[str] = []
    fence = ""
    for line in (text or "").splitlines():
        match = _FENCE_RE.match(line)
        if fence:
            out.append("")
            body = line.strip()
            if body and body[0] == fence[0] and body == body[0] * len(body) and len(body) >= len(fence):
                fence = ""
            continue
        if match is not None:
            fence = match.group(1)
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def heading_title(line: str) -> Optional[str]:
    """The text of an ATX heading, or ``None`` when this line is not one.

    ``## 작업 지시서 ##`` and ``## 작업 지시서:`` are the same heading as
    ``## 작업 지시서`` - the closing hashes and one trailing colon are noise.

    @param line  one line of the masked document
    """
    match = _HEADING_RE.match(line)
    if match is None:
        return None
    return match.group(2).strip().rstrip("#").strip().rstrip(":").strip()


def find_sections(lines: Sequence[str], titles: Sequence[str]) -> List[int]:
    """Line numbers of every heading whose text is exactly one of ``titles``.

    Every one of them, because two recognisable sections is a refusal and not a
    case to resolve: picking the first would let a second table sit under the
    first saying something else entirely.

    @param lines   the masked document, split into lines
    @param titles  the accepted heading texts, compared case-insensitively
    @flow  per line: an ATX heading -> its text in titles -> keep the index
    주요 내부 변수: wanted(소문자 제목 집합), found(찾은 줄 번호들)
    """
    wanted = {str(title).strip().lower() for title in titles}
    found: List[int] = []
    for index, line in enumerate(lines):
        title = heading_title(line)
        if title is not None and title.lower() in wanted:
            found.append(index)
    return found


def section_body(lines: Sequence[str], start: int) -> List[str]:
    """The lines under one heading, stopping at the next heading of any level.

    @param lines  the masked document, split into lines
    @param start  the heading's own line index
    """
    body: List[str] = []
    for line in lines[start + 1 :]:
        if heading_title(line) is not None:
            break
        body.append(line)
    return body


def split_row(line: str) -> Optional[List[str]]:
    """One markdown table row as its cells, or ``None`` when this is not a row.

    Written out by hand rather than split on ``|`` because ``\\|`` is a literal
    pipe inside a cell, and a duty column is exactly where somebody writes one.

    @param line  one line of the section
    @flow  not starting with '|' -> None ; walk the characters, '\\|' is literal
           -> drop the empty cell each outer pipe makes -> strip every cell
    주요 내부 변수: cells(모인 셀들), current(현재 셀의 문자들)
    """
    text = line.strip()
    if not text.startswith("|"):
        return None
    cells: List[str] = []
    current: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current))
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _header_matches(cells: Sequence[str], columns: Sequence[str]) -> bool:
    """Is this the fixed header, spelled exactly? Only 'symbol' forgives its case.

    @param cells    the header row's cells
    @param columns  the fixed column names
    @flow  per column: the loose ones compare lowered, the rest compare exactly
    """
    for cell, column in zip(cells, columns):
        if column in _LOOSE_COLUMNS:
            if cell.strip().lower() != column:
                return False
        elif cell.strip() != column:
            return False
    return True


def _is_separator(cells: Sequence[str]) -> bool:
    """Is this the ``| --- | --- |`` row markdown puts under a header?

    @param cells  the row's cells
    """
    return all(_SEPARATOR_CELL_RE.match(cell.replace(" ", "")) for cell in cells)


def expected_header(columns: Sequence[str]) -> str:
    """The header row a table of these columns must carry, spelled out for a refusal.

    @param columns  the fixed column names
    """
    return "| " + " | ".join(columns) + " |"


def parse_table(
    lines: Sequence[str], columns: Sequence[str], offset: int = 0
) -> Tuple[List[Tuple[int, List[str]]], List[str]]:
    """Read one fixed-column table out of a section. Anything else is an error.

    Deliberately unforgiving: a row with the wrong number of cells is a refusal
    rather than a row read as best it can be, because "as best it can be" is
    where a path silently becomes a duty.

    @param lines    the section's lines
    @param columns  the exact column names, in order
    @param offset   the document line number of ``lines[0]``, for the messages
    @flow  first '|' line is the header -> the next is the separator -> the rest
           are data ; wrong cell count, wrong header or a missing separator stops it
    주요 내부 변수: rows(데이터 행들), header_at(헤더 줄), separator(구분자를 봤는지)
    """
    rows: List[Tuple[int, List[str]]] = []
    errors: List[str] = []
    header_at: Optional[int] = None
    separator = False
    for index, raw in enumerate(lines):
        cells = split_row(raw)
        if cells is None:
            continue
        number = offset + index + 1
        if len(cells) != len(columns):
            errors.append(
                "{0}행: 표는 정확히 {1}칸이다 (읽은 칸 {2}개). 기대하는 헤더: {3}".format(
                    number, len(columns), len(cells), expected_header(columns)
                )
            )
            return [], errors
        if header_at is None:
            if not _header_matches(cells, columns):
                errors.append(
                    "{0}행: 표 헤더가 다르다. 기대하는 헤더: {1}".format(
                        number, expected_header(columns)
                    )
                )
                return [], errors
            header_at = index
            continue
        if not separator:
            if not _is_separator(cells):
                errors.append(
                    "{0}행: 헤더 다음 줄은 구분자 행이어야 한다: {1}".format(
                        number, "| " + " | ".join(["---"] * len(columns)) + " |"
                    )
                )
                return [], errors
            separator = True
            continue
        rows.append((number, cells))
    if header_at is None:
        errors.append("표가 없다. 기대하는 헤더: {0}".format(expected_header(columns)))
    elif not separator:
        errors.append(
            "구분자 행이 없다: {0}".format("| " + " | ".join(["---"] * len(columns)) + " |")
        )
    elif not rows:
        errors.append("표에 데이터 행이 하나도 없다")
    return rows, errors


# -------------------------------------------------------------------- paths


def normalize_path(raw: str) -> Tuple[str, str]:
    """A repo-relative path, or the one reason it is not one. Never both.

    Every rejection here is a shape a path check downstream would have to guess
    about: a drive letter, an NTFS alternate data stream, a Windows device name,
    a segment ending in a dot that the filesystem silently trims.

    @param raw  the cell as it was written
    @flow  empty -> backslash -> control -> colon -> absolute -> per segment:
           empty, '.'/'..', trailing dot or space, reserved device name
    주요 내부 변수: text(다듬은 원문), segments(경로 조각들)
    """
    text = (raw or "").strip()
    if not text:
        return "", "경로가 비어 있다"
    if "\\" in text:
        return "", "역슬래시는 쓰지 않는다 - repo-relative 경로는 '/'로 쓴다: {0}".format(text)
    for char in text:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return "", "제어문자가 들어 있다: {0!r}".format(text)
    if ":" in text:
        return "", "콜론은 쓸 수 없다 - 드라이브 문자와 ADS는 경로가 아니다: {0}".format(text)
    if text.startswith("/"):
        return "", "절대경로는 쓸 수 없다: {0}".format(text)
    segments = text.split("/")
    if segments[-1] == "":
        return "", "경로가 '/'로 끝난다 - 대상은 파일이다: {0}".format(text)
    for segment in segments:
        if segment == "":
            return "", "빈 세그먼트가 있다: {0}".format(text)
        if segment in (".", ".."):
            return "", "'.'와 '..'는 쓸 수 없다: {0}".format(text)
        if segment != segment.rstrip(". "):
            return "", "세그먼트가 점이나 공백으로 끝난다: {0}".format(text)
        if segment.split(".")[0].upper() in _RESERVED_NAMES:
            return "", "Windows 예약 장치명이다: {0}".format(text)
    return text, ""


def escapes_root(root: Path, rel: str) -> bool:
    """Does this repo-relative path leave the repository once the OS resolves it?

    ``realpath`` rather than string arithmetic, because the escape this is
    aimed at is a symlink or an NTFS junction partway down the path - which no
    amount of ``..`` checking can see. It answers for a path that does not
    exist yet too: the parent is resolved either way.

    @param root  the worktree the path is relative to
    @param rel   an already normalised repo-relative path
    @flow  realpath both -> compare with normcase -> inside means equal or prefixed
    주요 내부 변수: base(해석된 기준), target(해석된 대상)
    """
    try:
        base = os.path.realpath(str(root))
        target = os.path.realpath(os.path.join(base, str(rel).replace("/", os.sep)))
    except (OSError, ValueError):
        return True
    base_key = os.path.normcase(base).rstrip(os.sep)
    target_key = os.path.normcase(target)
    return not (target_key == base_key or target_key.startswith(base_key + os.sep))


# ------------------------------------------------------------------- rows


def check_duplicates(items: Sequence[Item]) -> List[str]:
    """Every way one path can be declared twice and mean two different things.

    Two rows for the same path are the normal shape of a function-level order -
    two symbols in one file - so the rule is not "one row per path". What is
    refused is a path that carries two verbs, the exact same ``(path, symbol)``
    twice, and a file-level row sitting beside a symbol row for the same file:
    each of those is a scope with two readings.

    @param items  the parsed rows, paths already normalised
    @flow  group by path -> more than one verb -> repeated (path, symbol)
           -> bare row mixed with symbol rows -> CREATE carrying a symbol
    주요 내부 변수: groups(경로별 행들), errors(사유 목록)
    """
    groups: Dict[str, List[Item]] = {}
    for item in items:
        if item.path:
            groups.setdefault(item.path, []).append(item)
    errors: List[str] = []
    for path, group in groups.items():
        verbs = sorted({item.verb for item in group})
        if len(verbs) > 1:
            errors.append(
                "같은 경로에 서로 다른 동사가 있다: {0} -> {1}".format(path, ", ".join(verbs))
            )
            continue
        seen: Set[str] = set()
        said: Set[str] = set()
        for item in group:
            if item.symbol in seen and item.symbol not in said:
                said.add(item.symbol)
                errors.append(
                    "같은 (경로, symbol) 조합이 두 번 있다: {0} | {1}".format(
                        path, item.symbol or "(symbol 없음)"
                    )
                )
            seen.add(item.symbol)
        if "" in seen and len(seen) > 1:
            errors.append(
                "같은 경로에 symbol 없는 파일 단위 행과 symbol 행이 함께 있다: {0}".format(path)
            )
    for item in items:
        if item.verb == VERB_CREATE and item.symbol:
            errors.append(
                "{0}행: CREATE는 아직 없는 파일이라 symbol을 해석할 수 없다 - "
                "symbol을 비워라: {1}".format(item.row, item.path)
            )
    return errors


def items_digest(items: Sequence[Item]) -> str:
    """The canonical digest of a scope: verb, path and symbol, order-independent.

    The duty column is deliberately left out. Rewording what a row is *for*
    changes the document a human reads and not the scope a machine compares, and
    a digest that moved on a reworded sentence would be a digest nobody trusts.

    @param items  the rows to digest
    """
    lines = sorted("{0}\t{1}\t{2}".format(item.verb, item.path, item.symbol) for item in items)
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def document_digest(data: Any) -> str:
    """The digest of a whole document, byte for byte - what re-approval is judged on.

    Deliberately not ``items_digest``: a human editing plan.md at the gate may
    well change only prose, and the approval they gave was for the document.

    @param data  the file's bytes, or its text
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(bytes(data)).hexdigest()


def items_from_dicts(data: Any) -> List[Item]:
    """Rows read back out of state.json, skipping anything that is not one.

    @param data  whatever ``state["work_order"]["items"]`` holds
    """
    items: List[Item] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        verb = str(entry.get("verb") or "").strip().upper()
        path = str(entry.get("path") or "").strip()
        if verb not in VERBS or not path:
            continue
        items.append(
            Item(
                verb=verb,
                path=path,
                symbol=str(entry.get("symbol") or "").strip(),
                note=str(entry.get("note") or "").strip(),
            )
        )
    return items


def _escape_cell(text: str) -> str:
    """One cell's text, made safe to sit inside a row: pipes escaped, newlines flattened.

    @param text  whatever the cell holds
    """
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def render_table(items: Sequence[Item]) -> str:
    """The rows as the table again, for a prompt or an approval screen.

    Rendered rather than quoted from the document, so what a session is shown is
    what the engine actually holds - a table and a paragraph about it cannot
    drift apart if there is only one of them.

    @param items  the rows to render
    """
    lines = [expected_header(ORDER_COLUMNS), "| " + " | ".join(["---"] * len(ORDER_COLUMNS)) + " |"]
    for item in items:
        lines.append(
            "| {0} | {1} | {2} | {3} |".format(
                item.verb, _escape_cell(item.path), _escape_cell(item.symbol), _escape_cell(item.note)
            )
        )
    return "\n".join(lines)


# ------------------------------------------------------------------ parsing


def _one_section(text: str, headings: Sequence[str], what: str) -> Tuple[List[str], int, List[str]]:
    """The one recognised section's lines, where it starts, and why there is not one.

    @param text      the whole document
    @param headings  the accepted heading texts
    @param what      what to call the section in a refusal
    @flow  mask fences -> find headings -> none -> absent ; two or more -> refuse
    주요 내부 변수: lines(마스킹된 줄들), starts(인식된 제목 줄들)
    """
    lines = mask_fenced(text).splitlines()
    starts = find_sections(lines, headings)
    if not starts:
        return [], -1, []
    if len(starts) > 1:
        return (
            [],
            starts[0],
            [
                "'{0}' 절이 {1}개다 ({2}행) - 인식 가능한 절은 하나여야 한다".format(
                    what, len(starts), ", ".join(str(n + 1) for n in starts)
                )
            ],
        )
    return section_body(lines, starts[0]), starts[0], []


def parse_work_order(text: str) -> Order:
    """Read the '작업 지시서' section out of a document. Table work only.

    Nothing here touches the filesystem or the Function DB, so the parser is
    unit-testable without a repository - existence and symbol resolution are the
    caller's job, in ``pipeline.validate_work_order``.

    @param text  the whole document, usually plan.md
    @flow  one section -> the table -> per row: verb, path, symbol
           -> duplicates and conflicts
    주요 내부 변수: body(절 본문), rows(표 데이터 행), items(읽어낸 지시 행들)
    """
    body, start, errors = _one_section(text, ORDER_HEADINGS, "작업 지시서")
    if errors:
        return Order(items=[], errors=errors, present=True)
    if start < 0:
        return Order(
            items=[],
            errors=[
                "'## 작업 지시서' 절이 없다 - 계획은 파일 단위 지시서를 반드시 포함한다"
            ],
            present=False,
        )
    rows, errors = parse_table(body, ORDER_COLUMNS, offset=start + 1)
    items: List[Item] = []
    for number, cells in rows:
        verb = cells[0].strip().upper()
        if verb not in VERBS:
            errors.append(
                "{0}행: 동사는 {1} 중 하나다 (읽은 값 {2!r})".format(
                    number, " / ".join(VERBS), cells[0].strip()
                )
            )
            continue
        path, reason = normalize_path(cells[1])
        if reason:
            errors.append("{0}행: {1}".format(number, reason))
            continue
        items.append(
            Item(verb=verb, path=path, symbol=cells[2].strip(), note=cells[3].strip(), row=number)
        )
    errors += check_duplicates(items)
    return Order(items=items, errors=errors, present=True)


def parse_reasons(text: str) -> Reasons:
    """Read the '범위 밖 수정 사유' section out of a stage's final message.

    Absent is legal and means "there were none": the engine compares the table
    with what actually happened, so an omitted section that should have been
    there is caught by the comparison rather than by this parser.

    @param text  the stage's final message
    @flow  one section -> the two-column table -> per row: path, reason
    주요 내부 변수: body(절 본문), rows(표 데이터 행)
    """
    body, start, errors = _one_section(text, REASON_HEADINGS, "범위 밖 수정 사유")
    if errors:
        return Reasons(rows=[], errors=errors, present=True)
    if start < 0:
        return Reasons(rows=[], errors=[], present=False)
    rows, errors = parse_table(body, REASON_COLUMNS, offset=start + 1)
    declared: List[Tuple[str, str]] = []
    for number, cells in rows:
        path, reason = normalize_path(cells[0])
        if reason:
            errors.append("{0}행: {1}".format(number, reason))
            continue
        note = cells[1].strip()
        if not note:
            errors.append("{0}행: 사유가 비어 있다: {1}".format(number, path))
            continue
        declared.append((path, note))
    return Reasons(rows=declared, errors=errors, present=True)


# ------------------------------------------------------------- the comparison


def _listing(paths: Sequence[str], limit: int = 20) -> str:
    """Paths as an indented list a human reads, capped so a refusal stays readable.

    The cap is announced rather than silent: a message that quietly stopped at
    twenty would read as "twenty" when it meant "two hundred".

    @param paths  the paths to show
    @param limit  how many to print before saying how many are left
    """
    shown = ["    {0}".format(path) for path in paths[:limit]]
    if len(paths) > limit:
        shown.append("    ... {0} more".format(len(paths) - limit))
    return "\n".join(shown)


def classify(
    status: Dict[str, str], items: Sequence[Item], excluded: Iterable[str] = ()
) -> ScopeVerdict:
    """Judge the final diff against the work order, one path at a time.

    The asymmetry is the design. A new file nobody declared and a deletion
    nobody declared are refusals, because both are the session deciding what the
    scope is. An *edit* to an existing file is not: it is a debt, recorded as
    ``unplanned`` and settled by the reason table, because refusing it outright
    would fail a slice for a one-line fix it genuinely had to make.

    REFERENCE grants nothing: a path that is only referenced can still be
    modified as unplanned, but creating or deleting it fails.

    @param status    the final diff, path -> one of A / M / D / T
    @param items     the effective work order
    @param excluded  paths the engine wrote itself and must not judge
    @flow  per path in order: A not writable -> added ; D not modifiable
           -> deleted ; M/T not writable -> unplanned ; else declared
    주요 내부 변수: writable(CREATE∪MODIFY), modifiable(MODIFY), verdict(누적 판정)
    """
    skip = set(excluded)
    writable = {item.path for item in items if item.verb in (VERB_CREATE, VERB_MODIFY)}
    modifiable = {item.path for item in items if item.verb == VERB_MODIFY}
    verdict = ScopeVerdict()
    for path in sorted(status):
        if path in skip:
            continue
        letter = status[path]
        if letter == "A":
            (verdict.declared if path in writable else verdict.added).append(path)
        elif letter == "D":
            (verdict.declared if path in modifiable else verdict.deleted).append(path)
        else:
            (verdict.declared if path in writable else verdict.unplanned).append(path)
    if verdict.added:
        verdict.failures.append(
            "지시서의 CREATE에 없는 새 파일 {0}개:\n{1}".format(
                len(verdict.added), _listing(verdict.added)
            )
        )
    if verdict.deleted:
        verdict.failures.append(
            "지시서의 MODIFY에 없는 삭제 {0}개 - 지우려면 그 경로를 미리 MODIFY로 "
            "선언한다:\n{1}".format(len(verdict.deleted), _listing(verdict.deleted))
        )
    verdict.ok = not verdict.failures
    return verdict


def match_reasons(
    unplanned: Sequence[str], declared: Sequence[str], carried: Iterable[str] = ()
) -> List[str]:
    """Compare the reason table with what really happened. Empty means they agree.

    Exact agreement in both directions: a missing row is an undeclared change, an
    extra row is a claim about a file that was not touched, and a repeated row is
    two answers to one question. ``carried`` is what keeps an amend finishable -
    the final diff still carries an earlier cycle's unplanned edits, and asking
    for those to be re-declared on every run would make the last cycle
    impossible to pass.

    @param unplanned  paths this check found changed without being declared
    @param declared   paths the reason table names, in the order it names them
    @param carried    paths a reason was already given for in an earlier check
    @flow  duplicates -> extras (not unplanned at all) -> missing (unplanned, not carried, not declared)
    주요 내부 변수: seen(선언된 경로 집합), required(이번에 반드시 선언해야 하는 것들)
    """
    failures: List[str] = []
    seen: Set[str] = set()
    repeated: List[str] = []
    for path in declared:
        if path in seen and path not in repeated:
            repeated.append(path)
        seen.add(path)
    if repeated:
        failures.append(
            "사유 표에 같은 경로가 두 번 있다:\n{0}".format(_listing(sorted(repeated)))
        )
    current = set(unplanned)
    extra = sorted(path for path in seen if path not in current)
    if extra:
        failures.append(
            "사유 표가 범위 밖 수정이 아닌 경로를 선언했다 - 바뀌지 않았거나 "
            "이미 지시서에 있다:\n{0}".format(_listing(extra))
        )
    already = set(carried)
    missing = sorted(path for path in current if path not in seen and path not in already)
    if missing:
        failures.append(
            "지시서에 없는 기존 파일을 고쳤는데 사유가 없다 - 최종 응답에 "
            "'## 범위 밖 수정 사유' 절을 두고 '| 경로 | 사유 |' 표로 선언한다:\n{0}".format(
                _listing(missing)
            )
        )
    return failures
