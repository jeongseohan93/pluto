"""JS/TS/JSX/TSX side of the parser: a character scanner, then brace matching.

Not tree-sitter and not bare regex. First every string, template, comment and
regex literal is blanked out - the characters become spaces, the newlines stay -
and *then* declarations are matched and bodies are found by counting braces on
that cleaned copy. JSX needs nothing special: ``{...}`` expression containers are
balanced, so the same counting works inside markup.

What it finds: named declarations. ``function f``, ``const f = () =>``,
``const f = function``, and the members of a ``class`` body. What it does not
find, on purpose: object-literal methods and anonymous callbacks - neither has a
name to ask for by name, and their calls are attributed to the function they sit
in.

When the scan cannot be trusted (unbalanced braces, a file too big to be source
code) the whole *file* is reported as failed and the build carries on. One file
must never end a build.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from pathlib import PurePosixPath
from typing import List, Optional, Tuple

from .model import LANG_JS, SUFFIX_LANG, ParsedCall, ParsedFile, ParsedFunction, spec_from_block

# Past this a file is generated, minified or vendored - not something a human
# asks the graph about, and the scanner is linear but not free.
MAX_SOURCE_BYTES = 1_500_000

_WORD = re.compile(r"[A-Za-z_$]")

# A '/' right after one of these opens a regex literal; after a value it is a
# division. The distinction is a heuristic and it is allowed to be wrong: a wrong
# guess unbalances the braces, and that fails this one file, loudly.
#
# '<' is deliberately not here and '/>' is excluded below, because in JSX both
# spellings are tags - ``</p>`` and ``<Badge />`` - many times for every regex a
# whole repository contains. Reading ``<Row a={b} />} />`` as a regex swallows a
# closing brace and costs the file. ``a < /x/`` would have to be nonsense anyway.
_REGEX_PRECEDERS = "(,=:[!&|?{};+-*%~^>"
_REGEX_KEYWORDS = frozenset(
    "return typeof case in of delete void instanceof new do else yield await".split()
)

_MODIFIERS = r"(?:(?:export|default|declare|public|private|protected|static|readonly|abstract|override|async|get|set)\s+)*"

_FUNCTION_RE = re.compile(
    r"^[ \t]*" + _MODIFIERS + r"function\s*\*?\s*([A-Za-z_$][\w$]*)\s*(?:<[^<>{};]*>)?\s*\(",
    re.MULTILINE,
)
_ASSIGN_RE = re.compile(
    r"^[ \t]*" + _MODIFIERS + r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]*)?=\s*",
    re.MULTILINE,
)
_CLASS_RE = re.compile(r"^[ \t]*" + _MODIFIERS + r"class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
# ``[ \t]`` and not ``\s`` before the name: ``\s`` crosses newlines, and a blank
# line above a JSDoc block would then start the match - putting the declaration's
# line number three lines too early and losing the comment that sits between.
_METHOD_RE = re.compile(
    r"^[ \t]*" + _MODIFIERS + r"\*?[ \t]*([A-Za-z_$][\w$]*)\s*(?:<[^<>{};]*>)?\s*\(", re.MULTILINE
)
_FIELD_RE = re.compile(
    r"^[ \t]*" + _MODIFIERS + r"([A-Za-z_$][\w$]*)\s*(?::[^=\n]*)?=\s*", re.MULTILINE
)
_CALL_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(")

# Words that are followed by '(' without being a call, and names nobody wants
# back from `graph callers`.
_NOT_CALLS = frozenset(
    """if for while switch catch return typeof void delete instanceof in of new function class
    await yield else do try finally throw super import require export const let var case with""".split()
)


class ScanError(RuntimeError):
    """The cleaned copy could not be trusted, so this file is skipped."""


def blank_strings(text: str) -> str:
    """A copy with every literal and comment blanked to spaces, newlines kept.

    Line numbers survive (only non-newline characters are replaced), so an index
    into the copy is an index into the source. Everything downstream - brace
    matching, declaration matching, call finding - runs on this and therefore
    can never be fooled by a brace inside a string or a JSDoc block.

    @param text  the file's source
    @flow  per character: // -> /* */ -> quote -> template -> regex? -> plain code
    주요 내부 변수: out(사본), prev(직전 유의미 문자), word(직전 식별자)
    """
    source = text or ""
    out = list(source)
    total = len(source)
    index = 0
    prev = ""
    word = ""
    in_word = False
    # The loop always advances, but a scanner that silently stops is a hang in an
    # unattended pipeline, so it is bounded and says so instead.
    steps = 0
    limit = 4 * total + 16
    while index < total:
        steps += 1
        if steps > limit:
            raise ScanError("scanner made no progress")
        char = source[index]
        if char == "/" and index + 1 < total and source[index + 1] == "/":
            end = source.find("\n", index)
            end = total if end < 0 else end
            _blank(out, index, end)
            index, prev, word, in_word = end, prev, "", False
            continue
        if char == "/" and index + 1 < total and source[index + 1] == "*":
            end = source.find("*/", index + 2)
            end = total if end < 0 else end + 2
            _blank(out, index, end)
            index, prev, word, in_word = end, prev, "", False
            continue
        if char in ("'", '"'):
            end = _skip_quoted(source, index)
            _blank(out, index, end)
            index, prev, word, in_word = end, char, "", False
            continue
        if char == "`":
            end = _skip_template(source, index)
            _blank(out, index, end)
            index, prev, word, in_word = end, "`", "", False
            continue
        if (
            char == "/"
            and not (index + 1 < total and source[index + 1] == ">")  # JSX self-close
            and _starts_regex(prev, word, in_word)
        ):
            end = _skip_regex(source, index)
            if end > 0:
                _blank(out, index, end)
                index, prev, word, in_word = end, "/", "", False
                continue
        if char.isspace():
            in_word = False
        elif _WORD.match(char) or char.isdigit():
            word = word + char if in_word else char
            in_word = True
            prev = char
        else:
            word, in_word, prev = "", False, char
        index += 1
    return "".join(out)


def _blank(out: List[str], start: int, end: int) -> None:
    """Replace a span with spaces, leaving its newlines where they are.

    @param out    the copy being built, one character per list entry
    @param start  first offset to blank
    @param end    one past the last
    @flow  every character of the span except a newline becomes a space
    """
    for index in range(start, min(end, len(out))):
        if out[index] != "\n":
            out[index] = " "


def _starts_regex(prev: str, word: str, in_word: bool) -> bool:
    """Would a ``/`` here open a regex literal, or divide?

    @param prev     the last significant character
    @param word     the identifier that just ended, if any
    @param in_word  is that identifier still being read (``a/b``)?
    @flow  nothing before -> regex ; operator -> regex ; keyword -> regex ; else division
    """
    if not prev:
        return True
    if prev in _REGEX_PRECEDERS:
        return True
    if in_word or (word and _WORD.match(prev or "")):
        return word in _REGEX_KEYWORDS
    return False


def _skip_quoted(source: str, index: int) -> int:
    """Where a ``'``/``"`` literal ends, or the line end when it never closes.

    @param source  the raw text
    @param index   the opening quote
    @flow  escape -> skip two ; the same quote -> done ; newline -> unterminated
    """
    quote = source[index]
    total = len(source)
    cursor = index + 1
    while cursor < total:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == quote:
            return cursor + 1
        if char == "\n":
            return cursor
        cursor += 1
    return total


def _skip_template(source: str, index: int, depth: int = 0) -> int:
    """Where a template literal ends, ``${...}`` and nested templates included.

    The whole literal is blanked, interpolations and all: a call written inside
    ``${...}`` is lost, which is the price of never miscounting a brace.

    @param source  the raw text
    @param index   the opening backtick
    @param depth   recursion depth, so a pathological file cannot blow the stack
    @flow  outside ${}: backtick ends it ; inside: braces nest, strings recurse
    주요 내부 변수: inner(${} 안의 중괄호 깊이)
    """
    total = len(source)
    cursor = index + 1
    inner = 0
    while cursor < total:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if inner == 0:
            if char == "`":
                return cursor + 1
            if char == "$" and cursor + 1 < total and source[cursor + 1] == "{":
                inner = 1
                cursor += 2
                continue
            cursor += 1
            continue
        if char == "{":
            inner += 1
        elif char == "}":
            inner -= 1
        elif char == "`" and depth < 20:
            cursor = _skip_template(source, cursor, depth + 1)
            continue
        elif char in ("'", '"'):
            cursor = _skip_quoted(source, cursor)
            continue
        cursor += 1
    return total


def _skip_regex(source: str, index: int) -> int:
    """Where a regex literal ends, or -1 when it does not end on this line.

    @param source  the raw text
    @param index   the opening slash
    @flow  escape -> skip two ; [..] class ; closing / -> done ; newline -> not a regex
    주요 내부 변수: in_class(문자 클래스 안인지)
    """
    total = len(source)
    cursor = index + 1
    in_class = False
    while cursor < total:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "\n":
            return -1
        if in_class:
            if char == "]":
                in_class = False
        elif char == "[":
            in_class = True
        elif char == "/":
            return cursor + 1
        cursor += 1
    return -1


# ------------------------------------------------------------------ the parse


def parse_js(text: str, path: str) -> ParsedFile:
    """Every named function of one JS/TS file, with spec, signature and calls.

    @param text  the file's source
    @param path  the repository-relative path, which also names the language
    @flow  size guard -> blank literals -> brace balance -> functions, assignments, classes
    주요 내부 변수: cleaned(사본), found(수집된 함수들)
    """
    source = text or ""
    lang = SUFFIX_LANG.get(PurePosixPath(path).suffix.lower(), LANG_JS)
    if len(source) > MAX_SOURCE_BYTES:
        return ParsedFile(
            path=path,
            lang=lang,
            error="skipped: {0} bytes is past the {1} byte limit".format(
                len(source), MAX_SOURCE_BYTES
            ),
        )
    try:
        cleaned = blank_strings(source)
    except ScanError as exc:
        return ParsedFile(path=path, lang=lang, error="skipped: {0}".format(exc))
    if cleaned.count("{") != cleaned.count("}"):
        return ParsedFile(
            path=path, lang=lang, error="unbalanced braces - the scanner could not read this file"
        )
    view = _View(source, cleaned)
    found: List[ParsedFunction] = []
    for match in _FUNCTION_RE.finditer(cleaned):
        if view.depth(match.start()) != 0:
            continue
        function = _declared_function(view, match, name=match.group(1), prefix="")
        if function is not None:
            found.append(function)
    for match in _ASSIGN_RE.finditer(cleaned):
        if view.depth(match.start()) != 0:
            continue
        function = _assigned_function(view, match, name=match.group(1), prefix="")
        if function is not None:
            found.append(function)
    for match in _CLASS_RE.finditer(cleaned):
        if view.depth(match.start()) != 0:
            continue
        found.extend(_class_members(view, match))
    found.sort(key=lambda function: (function.lineno, function.qualname))
    return ParsedFile(path=path, lang=lang, functions=found)


class _View:
    """The source and its blanked copy together, with the index maths on top."""

    def __init__(self, source: str, cleaned: str) -> None:
        self.source = source
        self.cleaned = cleaned
        self.lines = source.splitlines()
        self._starts: List[int] = [0]
        for index, char in enumerate(cleaned):
            if char == "\n":
                self._starts.append(index + 1)
        self._depths: List[int] = []
        depth = 0
        for char in cleaned:
            self._depths.append(depth)
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        self._depths.append(depth)

    def depth(self, index: int) -> int:
        """Brace depth just before ``index``.

        @param index  an offset into the cleaned copy
        """
        if index < 0:
            return 0
        return self._depths[min(index, len(self._depths) - 1)]

    def line_of(self, index: int) -> int:
        """The 1-based line an offset falls on.

        @param index  an offset into the cleaned copy
        """
        return bisect_right(self._starts, max(0, index))


def _skip_space(cleaned: str, index: int) -> int:
    """The next non-whitespace offset at or after ``index``.

    @param cleaned  the blanked copy
    @param index    where to start
    """
    total = len(cleaned)
    while index < total and cleaned[index].isspace():
        index += 1
    return index


_PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">"}


def _match_bracket(cleaned: str, index: int) -> int:
    """The offset just past the bracket that closes the one at ``index``, or -1.

    @param cleaned  the blanked copy, where brackets in literals are already gone
    @param index    the opening bracket
    @flow  count the same pair up and down until it returns to zero
    주요 내부 변수: level(현재 깊이)
    """
    opener = cleaned[index] if index < len(cleaned) else ""
    closer = _PAIRS.get(opener)
    if closer is None:
        return -1
    level = 0
    for cursor in range(index, len(cleaned)):
        char = cleaned[cursor]
        if char == opener:
            level += 1
        elif char == closer:
            level -= 1
            if level == 0:
                return cursor + 1
    return -1


def _body_brace(cleaned: str, index: int) -> int:
    """The offset of the ``{`` that opens a body, past any return type, or -1.

    ``function f(): {a: number} {`` has two braces after the parameters and only
    the second one is the body. A brace that directly follows ``:``/``|``/``&``
    is a type, so it is skipped whole.

    @param cleaned  the blanked copy
    @param index    just past the parameter list
    @flow  skip space -> type brace? skip it -> body brace ; anything else -> no body
    주요 내부 변수: prev(직전 유의미 문자)
    """
    cursor = _skip_space(cleaned, index)
    prev = ""
    while cursor < len(cleaned):
        char = cleaned[cursor]
        if char.isspace():
            cursor += 1
            continue
        if char == "{":
            if prev in (":", "|", "&"):
                end = _match_bracket(cleaned, cursor)
                if end < 0:
                    return -1
                cursor, prev = end, "}"
                continue
            return cursor
        if char in (";", "=", ")"):
            return -1
        prev = char
        cursor += 1
    return -1


def _param_text(view: _View, open_index: int) -> Tuple[str, int]:
    """The parameter list as written, and the offset just past its ``)``.

    @param view        the source and its copy
    @param open_index  the ``(``
    """
    end = _match_bracket(view.cleaned, open_index)
    if end < 0:
        return "", -1
    inner = view.source[open_index + 1 : end - 1]
    return " ".join(inner.split()), end


def _split_params(text: str) -> List[str]:
    """Parameter names, destructuring patterns kept whole, types and defaults dropped.

    @param text  the text between the parentheses
    @flow  split on top-level commas -> cut at ':' or '=' -> keep what names it
    주요 내부 변수: level(괄호 깊이), pieces(조각들)
    """
    pieces: List[str] = []
    current = ""
    level = 0
    for char in text:
        if char in "([{<":
            level += 1
        elif char in ")]}>":
            level -= 1
        if char == "," and level == 0:
            pieces.append(current)
            current = ""
            continue
        current += char
    pieces.append(current)
    names: List[str] = []
    for piece in pieces:
        name = _param_name(piece)
        if name:
            names.append(name)
    return names


def _param_name(piece: str) -> str:
    """One parameter's name: an identifier, or the destructuring pattern itself.

    @param piece  one comma-separated parameter
    @flow  strip modifiers -> pattern? keep it whole -> else cut at ':' or '='
    """
    text = piece.strip()
    for modifier in ("public ", "private ", "protected ", "readonly "):
        if text.startswith(modifier):
            text = text[len(modifier) :].strip()
    if not text:
        return ""
    if text[0] in "{[":
        end = _match_bracket(text, 0)
        return text[:end].strip() if end > 0 else text
    match = re.match(r"\.{3}\s*([A-Za-z_$][\w$]*)|([A-Za-z_$][\w$]*)", text)
    if match is None:
        return ""
    return ("..." + match.group(1)) if match.group(1) else match.group(2)


def _spec_above(lines: List[str], lineno: int) -> str:
    """The JSDoc block or the ``//`` run directly above a declaration.

    ``/** ... */`` first, because that is where a JS ``@param`` lives; a run of
    ``//`` lines is read the same way ``specs.comment_block_above`` reads ``#``.

    @param lines   the file's lines, 0-based
    @param lineno  the 1-based line the declaration starts on
    @flow  '*/' above -> walk up to '/*' and strip the markers ; '//' -> collect upwards
    주요 내부 변수: collected(모은 줄들), index(위로 올라가는 커서)
    """
    index = lineno - 2
    if index < 0 or not lines[index].strip():
        return ""
    stripped = lines[index].strip()
    if stripped.endswith("*/"):
        collected: List[str] = []
        while index >= 0:
            line = lines[index].strip()
            collected.append(line)
            if "/*" in line:
                break
            index -= 1
        body = "\n".join(reversed(collected))
        body = re.sub(r"\*/\s*$", "", body)
        body = re.sub(r"^\s*/\*+", "", body)
        return "\n".join(re.sub(r"^\s*\*+ ?", "", line) for line in body.splitlines()).strip()
    if stripped.startswith("//"):
        collected = []
        while index >= 0:
            line = lines[index].strip()
            if not line.startswith("//"):
                break
            collected.append(line.lstrip("/").strip())
            index -= 1
        return "\n".join(reversed(collected)).strip()
    return ""


def _calls_in(view: _View, start: int, end: int) -> List[ParsedCall]:
    """Every call written between two offsets, keywords excluded.

    Callbacks count: an anonymous arrow has no record of its own, so what it
    calls belongs to the function it was written in.

    @param view   the source and its copy
    @param start  first offset of the body
    @param end    one past the last
    @flow  match name( -> drop keywords -> dotted name keeps its last segment
    주요 내부 변수: found(호출들)
    """
    found: List[ParsedCall] = []
    for match in _CALL_RE.finditer(view.cleaned, max(0, start), max(0, end)):
        raw = match.group(1)
        name = raw.rsplit(".", 1)[-1]
        if not name or name in _NOT_CALLS or raw.split(".", 1)[0] in _NOT_CALLS:
            continue
        if _word_before(view.cleaned, match.start(1)) in ("function", "class"):
            continue  # a nested declaration, not a call of it
        found.append(ParsedCall(name=name, raw=raw, lineno=view.line_of(match.start(1))))
    return found


def _word_before(cleaned: str, index: int) -> str:
    """The identifier immediately before an offset, whitespace skipped.

    @param cleaned  the blanked copy
    @param index    the offset to look back from
    @flow  skip spaces backwards -> read the word backwards -> return it forwards
    주요 내부 변수: cursor(뒤로 가는 커서)
    """
    cursor = index - 1
    while cursor >= 0 and cleaned[cursor].isspace():
        cursor -= 1
    end = cursor + 1
    while cursor >= 0 and (cleaned[cursor].isalnum() or cleaned[cursor] in "_$"):
        cursor -= 1
    return cleaned[cursor + 1 : end]


def _make(
    view: _View,
    name: str,
    prefix: str,
    kind: str,
    start: int,
    params_text: str,
    body_start: int,
    body_end: int,
) -> ParsedFunction:
    """Assemble one record once the scanner has found all four coordinates.

    @param view         the source and its copy
    @param name         the declared name
    @param prefix       the qualname prefix, ``Class.`` or empty
    @param kind         function / arrow / method
    @param start        offset the declaration begins at
    @param params_text  the parameter list as written
    @param body_start   offset the body begins at
    @param body_end     one past the body's end
    """
    lineno = view.line_of(start)
    return ParsedFunction(
        qualname=prefix + name,
        name=name,
        lineno=lineno,
        end_lineno=view.line_of(max(body_end - 1, body_start)),
        params=_split_params(params_text),
        signature="{0}({1})".format(name, params_text),
        kind=kind,
        calls=_calls_in(view, body_start, body_end),
        **_spec_fields(view.lines, lineno)
    )


def _spec_fields(lines: List[str], lineno: int) -> dict:
    """The three spec fields of a record, from the comment above the declaration.

    @param lines   the file's lines
    @param lineno  the 1-based declaration line
    """
    spec, summary, tags = spec_from_block(_spec_above(lines, lineno))
    return {"spec": spec, "summary": summary, "tags": tags}


def _declared_function(
    view: _View, match: "re.Match[str]", name: str, prefix: str
) -> Optional[ParsedFunction]:
    """A ``function f(...) {...}`` declaration, or None when it has no body.

    @param view    the source and its copy
    @param match   the header match, ending just past ``(``
    @param name    the declared name
    @param prefix  the qualname prefix
    """
    params_open = match.end() - 1
    params_text, after = _param_text(view, params_open)
    if after < 0:
        return None
    body = _body_brace(view.cleaned, after)
    if body < 0:
        return None  # an overload signature or a .d.ts declaration: no body to index
    body_end = _match_bracket(view.cleaned, body)
    if body_end < 0:
        return None
    return _make(view, name, prefix, "function", match.start(), params_text, body, body_end)


def _assigned_function(
    view: _View, match: "re.Match[str]", name: str, prefix: str
) -> Optional[ParsedFunction]:
    """``const f = () => ...`` or ``const f = function ...``, or None when it is neither.

    @param view    the source and its copy
    @param match   the assignment match, ending just past ``=``
    @param name    the declared name
    @param prefix  the qualname prefix
    @flow  'function' -> a function expression ; '=>' -> an arrow ; else not a function
    주요 내부 변수: cursor(``=`` 뒤 첫 유의미 위치)
    """
    cleaned = view.cleaned
    cursor = _skip_space(cleaned, match.end())
    if re.match(r"async\s", cleaned[cursor : cursor + 6]):
        cursor = _skip_space(cleaned, cursor + 5)
    if cleaned.startswith("function", cursor):
        params_open = cleaned.find("(", cursor)
        if params_open < 0:
            return None
        params_text, after = _param_text(view, params_open)
        body = _body_brace(cleaned, after) if after > 0 else -1
        body_end = _match_bracket(cleaned, body) if body >= 0 else -1
        if body_end < 0:
            return None
        return _make(view, name, prefix, "function", match.start(), params_text, body, body_end)
    arrow = _arrow_after(view, cursor)
    if arrow is None:
        return None
    params_text, arrow_end = arrow
    body_start, body_end = _arrow_body(cleaned, arrow_end)
    if body_end < 0:
        return None
    return _make(view, name, prefix, "arrow", match.start(), params_text, body_start, body_end)


def _arrow_after(view: _View, cursor: int) -> Optional[Tuple[str, int]]:
    """Is an arrow function written here? Its parameters, and the offset past ``=>``.

    @param view    the source and its copy
    @param cursor  the first significant offset after ``=``
    @flow  generics -> parameters in parens or one bare name -> return type -> '=>'
    주요 내부 변수: params_text(파라미터 원문), between(파라미터와 화살표 사이)
    """
    cleaned = view.cleaned
    if cursor >= len(cleaned):
        return None
    if cleaned[cursor] == "<":  # a generic arrow: <T,>(x) => ...
        end = _match_bracket(cleaned, cursor)
        if end < 0:
            return None
        cursor = _skip_space(cleaned, end)
    if cleaned[cursor] == "(":
        params_text, cursor = _param_text(view, cursor)
        if cursor < 0:
            return None
    else:
        match = re.compile(r"[A-Za-z_$][\w$]*").match(cleaned, cursor)
        if match is None:
            return None
        params_text = match.group(0)
        cursor = match.end()
    tail = cleaned[cursor : cursor + 200]
    arrow = tail.find("=>")
    if arrow < 0:
        return None
    between = tail[:arrow].strip()
    if between and not between.startswith(":"):
        return None  # something else follows the parameters: not an arrow function
    return params_text, cursor + arrow + 2


def _arrow_body(cleaned: str, arrow_end: int) -> Tuple[int, int]:
    """Where an arrow's body starts and ends: a block, a bracketed expression, or a line.

    @param cleaned    the blanked copy
    @param arrow_end  the offset just past ``=>``
    @flow  '{' or '(' or '[' -> match it ; otherwise to the ';' or the line end
    주요 내부 변수: start(본문 시작)
    """
    start = _skip_space(cleaned, arrow_end)
    if start >= len(cleaned):
        return arrow_end, -1
    if cleaned[start] in "{([":
        return start, _match_bracket(cleaned, start)
    end = start
    while end < len(cleaned) and cleaned[end] not in ";\n":
        end += 1
    return start, end


def _class_members(view: _View, match: "re.Match[str]") -> List[ParsedFunction]:
    """The methods and arrow properties of one ``class`` body.

    @param view   the source and its copy
    @param match  the class header match
    @flow  find the body -> members at depth 1 -> method with a body, or an arrow property
    주요 내부 변수: prefix(``Class.``), found(수집)
    """
    cleaned = view.cleaned
    body = cleaned.find("{", match.end())
    if body < 0:
        return []
    body_end = _match_bracket(cleaned, body)
    if body_end < 0:
        return []
    prefix = match.group(1) + "."
    inside = view.depth(body) + 1
    found: List[ParsedFunction] = []
    seen = set()
    for member in _METHOD_RE.finditer(cleaned, body + 1, body_end):
        if view.depth(member.start()) != inside or member.group(1) in _NOT_CALLS:
            continue
        function = _declared_function(view, member, name=member.group(1), prefix=prefix)
        if function is not None:
            function.kind = "method"
            found.append(function)
            seen.add(function.lineno)
    for member in _FIELD_RE.finditer(cleaned, body + 1, body_end):
        if view.depth(member.start()) != inside:
            continue
        function = _assigned_function(view, member, name=member.group(1), prefix=prefix)
        if function is not None and function.lineno not in seen:
            function.kind = "method"
            found.append(function)
    return found
