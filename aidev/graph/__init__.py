"""Function graph: 사람에겐 시선(view), AI에겐 DB(query).

The engine's data layer. It parses code plus its 명세 주석 into a Function DB so
an agent stops *searching* for a function and simply *asks* for it by name:

    aidev graph build --repo .          # 전체 빌드
    aidev graph show run_pipeline       # 명세 + 시그니처 + 좌표 + 호출/피호출
    aidev graph callers merge_slice     # 부르는 곳들
    aidev graph calls commit_stage      # 부르는 것들
    aidev graph summaries --dir aidev   # 이름 + 기능 한 줄

The code is the truth and the DB is a derivative: ``.aidev/graph/graph.db`` may
be deleted at any moment and rebuilt from the files. Nothing in the pipeline
depends on it being there, which is why the stage hook only *marks* it stale and
the refresh happens when somebody actually asks a question.

This package imports ``specs``, ``workspace`` and ``storage`` - never
``pipeline``, which imports this one.
"""

from __future__ import annotations

from .build import (
    GRAPH_SCHEMA,
    UpdateReport,
    build_repo,
    db_path,
    discover,
    graph_dir,
    is_dirty,
    mark_dirty,
    refresh_if_dirty,
    update_repo,
)
from .commands import add_parser
from .db import GraphDB
from .model import CORE_TAGS, ParsedCall, ParsedFile, ParsedFunction, SpecTag, parse_spec
from .jsparse import blank_strings, parse_js
from .pyparse import parse_python

__all__ = [
    "CORE_TAGS",
    "GRAPH_SCHEMA",
    "GraphDB",
    "ParsedCall",
    "ParsedFile",
    "ParsedFunction",
    "SpecTag",
    "UpdateReport",
    "add_parser",
    "blank_strings",
    "build_repo",
    "db_path",
    "discover",
    "graph_dir",
    "is_dirty",
    "mark_dirty",
    "parse_js",
    "parse_python",
    "parse_spec",
    "refresh_if_dirty",
    "update_repo",
]
