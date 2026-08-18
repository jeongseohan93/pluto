"""Python side of the parser: ``ast``, so the coordinates are exact.

No dependency and no guessing - line ranges, qualnames and call nodes all come
straight out of the tree. Two differences from ``aidev.specs``, which walks the
same tree for a different purpose: dunders and nested functions are *kept* (a
graph is an index, not a checker, and ``__init__``'s coordinates are a fair
question), and every call site is collected.

A file that does not parse comes back as a ``ParsedFile`` carrying the reason.
The exception never leaves this module: one broken file must not end a build.
"""

from __future__ import annotations

import ast
from typing import List, Optional, Tuple

from .. import specs
from .model import LANG_PY, ParsedCall, ParsedFile, ParsedFunction, spec_from_block


def parse_python(text: str, path: str) -> ParsedFile:
    """Every function and method of one Python file, with spec, signature and calls.

    @param text  the file's source
    @param path  the repository-relative path, for the record
    @flow  ast.parse (fails -> ParsedFile with the reason) -> walk -> one ParsedFunction each
    주요 내부 변수: found(수집된 함수들), lines(주석 블록을 찾기 위한 원본 줄)
    """
    source = text or ""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return ParsedFile(path=path, lang=LANG_PY, functions=[], error=_syntax_reason(exc))
    lines = source.splitlines()
    found: List[ParsedFunction] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + child.name
                found.append(_function(child, qualname, lines, in_class))
                visit(child, qualname + ".", False)
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".", True)
            else:
                visit(child, prefix, in_class)

    visit(tree, "", False)
    found.sort(key=lambda func: (func.lineno, func.qualname))
    return ParsedFile(path=path, lang=LANG_PY, functions=found)


def _syntax_reason(exc: Exception) -> str:
    """The one-line 'why this file was skipped' a status report can print.

    @param exc  whatever ``ast.parse`` raised
    """
    lineno = getattr(exc, "lineno", None)
    detail = str(getattr(exc, "msg", None) or exc).strip() or exc.__class__.__name__
    if lineno:
        return "{0}: {1} (line {2})".format(exc.__class__.__name__, detail, lineno)
    return "{0}: {1}".format(exc.__class__.__name__, detail)


def _function(node: ast.AST, qualname: str, lines: List[str], in_class: bool) -> ParsedFunction:
    """Turn one ``def`` node into the record the DB stores.

    @param node      the FunctionDef / AsyncFunctionDef
    @param qualname  the dotted name, classes and enclosing functions included
    @param lines     the file's lines, for the comment block fallback
    @param in_class  is this a method? only the kind label depends on it
    """
    name = getattr(node, "name", "")
    lineno = int(getattr(node, "lineno", 1) or 1)
    end_lineno = int(getattr(node, "end_lineno", lineno) or lineno)
    block = ast.get_docstring(node) or specs.comment_block_above(lines, lineno)
    spec, summary, tags = spec_from_block(block or "")
    return ParsedFunction(
        qualname=qualname,
        name=name,
        lineno=lineno,
        end_lineno=end_lineno,
        params=_param_names(node),
        signature="{0}({1})".format(name, _render_params(node)),
        kind="method" if in_class else "function",
        spec=spec,
        summary=summary,
        tags=tags,
        calls=_direct_calls(node),
    )


def _param_names(node: ast.AST) -> List[str]:
    """The parameter names ``@param`` is expected to document, ``self``/``cls`` aside.

    The same rule ``specs._params`` applies, deliberately: a reader comparing a
    spec against this list must see the checker's answer, not a second opinion.

    @param node  the function node
    @flow  posonly + args -> vararg -> kwonly -> kwarg
    """
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


def _render_params(node: ast.AST) -> str:
    """The signature as written: annotations, defaults, ``/`` and ``*`` markers included.

    @param node  the function node
    @flow  positional (defaults right-aligned) -> * -> keyword-only -> **
    주요 내부 변수: parts(렌더된 조각들), defaults(뒤에서부터 붙는 기본값)
    """
    args = getattr(node, "args", None)
    if args is None:
        return ""
    parts: List[str] = []
    posonly = list(getattr(args, "posonlyargs", []) or [])
    positional = posonly + list(args.args)
    defaults = list(args.defaults)
    offset = len(positional) - len(defaults)
    for index, arg in enumerate(positional):
        default = defaults[index - offset] if index >= offset else None
        parts.append(_render_arg(arg, default))
        if posonly and index == len(posonly) - 1:
            parts.append("/")
    if args.vararg is not None:
        parts.append("*" + _render_arg(args.vararg, None))
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, list(args.kw_defaults)):
        parts.append(_render_arg(arg, default))
    if args.kwarg is not None:
        parts.append("**" + _render_arg(args.kwarg, None))
    return ", ".join(parts)


def _render_arg(arg: ast.AST, default: Optional[ast.AST]) -> str:
    """One parameter: name, annotation, default - whatever of those exists.

    @param arg      the ``ast.arg`` node
    @param default  its default expression, or None
    @flow  name -> ': annotation' -> '=default' (' = ' when annotated, as PEP 8 asks)
    """
    rendered = getattr(arg, "arg", "")
    annotation = _unparse(getattr(arg, "annotation", None))
    if annotation:
        rendered += ": " + annotation
    if default is not None:
        value = _unparse(default) or "..."
        rendered += " = " + value if annotation else "=" + value
    return rendered


def _unparse(node: Optional[ast.AST]) -> str:
    """``ast.unparse`` without the failure modes - an unreadable annotation is not fatal.

    @param node  any expression node, or None
    """
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on a parsed tree
        return ""


# Nodes whose bodies belong to *them*, not to the function they sit in. A lambda
# is not among them: nothing indexes a lambda, so its calls would be lost.
_OWN_SCOPE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _direct_calls(node: ast.AST) -> List[ParsedCall]:
    """Call sites this function makes itself, nested definitions excluded.

    @param node  the function node
    @flow  walk children -> skip anything with its own scope -> record ast.Call
    주요 내부 변수: found(이 함수가 직접 부르는 것들)
    """
    found: List[ParsedCall] = []

    def walk(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, _OWN_SCOPE):
                continue
            if isinstance(child, ast.Call):
                name, raw = _call_name(child.func)
                if name:
                    found.append(
                        ParsedCall(name=name, raw=raw, lineno=int(getattr(child, "lineno", 0) or 0))
                    )
            walk(child)

    walk(node)
    return found


def _call_name(func: ast.AST) -> Tuple[str, str]:
    """What is being called: the bare name, and the dotted text it was written as.

    ``json.dumps`` answers ``("dumps", "json.dumps")``. Anything computed - a
    call's result, a subscript - answers nothing rather than a guess.

    @param func  the ``func`` side of an ``ast.Call``
    @flow  Name -> itself ; Attribute -> attr + dotted chain ; else -> nothing
    """
    if isinstance(func, ast.Name):
        return func.id, func.id
    if isinstance(func, ast.Attribute):
        return func.attr, _dotted(func)
    return "", ""


def _dotted(node: ast.AST) -> str:
    """The written form of an attribute chain, ``a.b.c``, best effort.

    @param node  an Attribute node
    @flow  Name -> id ; Attribute -> recurse ; anything else -> '?'
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return "{0}.{1}".format(prefix, node.attr) if prefix else node.attr
    return ""
