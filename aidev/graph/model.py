"""What a parser produces, and the 명세 주석 태그 규약: 닫힌 코어, 열린 주변.

Four tags are *core*: the one-line summary, ``@param``, ``@flow`` and ``@why`` -
the same shape ``aidev.specs`` already checks. A tool may read those and mean
something by them.

Every other ``@tag`` is kept exactly as written and given no meaning at all. It
is stored, counted and printed back unchanged, which is what
``pipeline.unknown_front_matter_keys`` does for front matter keys: an unknown
key is data, not an error. Attaching a *rule* to a custom tag is a future
engine block's job - here a tag is only ever data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

# The closed core. Everything else is collected and left alone.
CORE_TAGS: Tuple[str, ...] = ("param", "flow", "why")

# ``@param x  the seat`` / ``@custom 도구가 해석하지 않는다``
_TAG_RE = re.compile(r"^\s*@([A-Za-z_][\w-]*)\s*(.*)$")

LANG_PY = "py"
LANG_JS = "js"
LANG_TS = "ts"

# The languages the graph reads: this repository (Python) and its first two
# customers' front ends (JS/TS, jsx/tsx included).
SUFFIX_LANG = {
    ".py": LANG_PY,
    ".js": LANG_JS,
    ".jsx": LANG_JS,
    ".mjs": LANG_JS,
    ".cjs": LANG_JS,
    ".ts": LANG_TS,
    ".tsx": LANG_TS,
}


@dataclass
class SpecTag:
    """One ``@tag`` of a spec comment, core or not, as written."""

    tag: str
    value: str
    core: bool
    # 0-based offset inside the spec block, so ``show`` can print source order.
    line: int = 0

    def rendered(self) -> str:
        """The tag the way it appeared in the source, for an unaltered echo."""
        return ("@{0} {1}".format(self.tag, self.value)).rstrip()


@dataclass
class ParsedCall:
    """One call site inside a function body, resolved later or not at all."""

    name: str
    raw: str
    lineno: int


@dataclass
class ParsedFunction:
    """One function or method: where it is, what it takes, what it says, what it calls."""

    qualname: str
    name: str
    lineno: int
    end_lineno: int
    params: List[str] = field(default_factory=list)
    signature: str = ""
    kind: str = "function"
    spec: str = ""
    summary: str = ""
    tags: List[SpecTag] = field(default_factory=list)
    calls: List[ParsedCall] = field(default_factory=list)


@dataclass
class ParsedFile:
    """One file's functions, or the reason it could not be read."""

    path: str
    lang: str
    functions: List[ParsedFunction] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        """Was this file read at all? An error means no functions, not zero of them. Takes no arguments."""
        return not self.error


def parse_spec(text: str) -> Tuple[str, List[SpecTag]]:
    """Split one spec comment into its summary line and its tags, known or not.

    The summary is the first non-empty line that is not a tag. A tag's value
    continues onto following lines until the next tag or a blank line, so a
    wrapped ``@why`` stays one tag.

    @param text  a docstring or comment block, already stripped of its markers
    @flow  line by line: tag -> new tag / blank -> close it / text -> summary or continuation
    주요 내부 변수: current(이어붙이는 중인 태그), summary(첫 비태그 줄)
    """
    summary = ""
    tags: List[SpecTag] = []
    current = None
    for index, raw in enumerate((text or "").splitlines()):
        match = _TAG_RE.match(raw)
        if match is not None:
            name = match.group(1).lower()
            current = SpecTag(
                tag=name, value=match.group(2).strip(), core=name in CORE_TAGS, line=index
            )
            tags.append(current)
            continue
        stripped = raw.strip()
        if not stripped:
            current = None
            continue
        if current is not None:
            current.value = (current.value + " " + stripped).strip()
            continue
        if not summary:
            summary = stripped
    return summary, tags


def spec_from_block(text: str) -> Tuple[str, str, List[SpecTag]]:
    """A spec comment as the DB stores it: the whole text, its summary, its tags.

    @param text  a docstring or comment block
    """
    spec = (text or "").strip()
    summary, tags = parse_spec(spec)
    return spec, summary, tags
