"""The 작업 지시서 parser and the after-the-fact comparison, without a repository.

Everything in ``aidev.workorder`` is deterministic string work on purpose - no
model parses the table, and nothing here spawns git - so these tests need
nothing but text. The one exception, ``escapes_root``, gets a tmp_path.
"""

import os

import pytest

from aidev import workorder
from aidev.workorder import Item


HEADER = "| 동사 | 대상 경로 | symbol | 책임 |\n| --- | --- | --- | --- |"


def order_doc(*rows, title="## 작업 지시서", intro="# PLAN\n\n산문은 그대로 둔다.\n"):
    return "{0}\n{1}\n\n{2}\n{3}\n".format(intro, title, HEADER, "\n".join(rows))


def reasons_doc(*rows, title="## 범위 밖 수정 사유"):
    return "요약.\n\n{0}\n\n| 경로 | 사유 |\n| --- | --- |\n{1}\n".format(title, "\n".join(rows))


# ------------------------------------------------------------------- parsing


def test_a_plain_three_row_order_reads_back_exactly():
    order = workorder.parse_work_order(
        order_doc(
            "| CREATE | aidev/workorder.py |  | 지시서 파싱 |",
            "| MODIFY | aidev/pipeline.py | run_pipeline | 호출 지점 |",
            "| REFERENCE | aidev/verify.py |  | 결과 구조 |",
        )
    )

    assert order.errors == []
    assert order.present is True
    assert [(i.verb, i.path, i.symbol) for i in order.items] == [
        ("CREATE", "aidev/workorder.py", ""),
        ("MODIFY", "aidev/pipeline.py", "run_pipeline"),
        ("REFERENCE", "aidev/verify.py", ""),
    ]
    assert order.items[0].note == "지시서 파싱"
    # the row number points at the document line, so a refusal can name it
    assert order.items[0].row == 9
    assert order.items[-1].row == 11


def test_a_lowercase_verb_is_the_same_verb():
    order = workorder.parse_work_order(order_doc("| modify | a.py |  | 고친다 |"))
    assert order.errors == []
    assert order.items[0].verb == "MODIFY"


def test_a_verb_nobody_knows_is_refused():
    order = workorder.parse_work_order(order_doc("| DELETE | a.py |  | 지운다 |"))
    assert order.items == []
    assert any("동사는" in reason for reason in order.errors)


def test_no_section_at_all_is_its_own_refusal():
    order = workorder.parse_work_order("# PLAN\n\n산문만 있다.\n")
    assert order.present is False
    assert any("작업 지시서" in reason for reason in order.errors)


def test_two_recognisable_sections_are_refused():
    text = order_doc("| CREATE | a.py |  | x |") + order_doc("| CREATE | b.py |  | y |", intro="")
    order = workorder.parse_work_order(text)
    assert order.items == []
    assert any("절이 2개" in reason for reason in order.errors)


def test_a_heading_inside_a_fence_is_an_example_and_not_a_section():
    text = (
        "# PLAN\n\n"
        "형식은 이렇다:\n\n"
        "```markdown\n"
        "## 작업 지시서\n\n"
        "| 동사 | 대상 경로 | symbol | 책임 |\n"
        "| --- | --- | --- | --- |\n"
        "| CREATE | example.py |  | 예시 |\n"
        "```\n\n"
        "## 작업 지시서\n\n" + HEADER + "\n"
        "| CREATE | real.py |  | 진짜 |\n"
    )
    order = workorder.parse_work_order(text)

    assert order.errors == []
    assert [item.path for item in order.items] == ["real.py"]


def test_a_tilde_fence_hides_a_section_too():
    text = "~~~\n## 작업 지시서\n~~~\n\n" + order_doc("| CREATE | a.py |  | x |", intro="")
    order = workorder.parse_work_order(text)
    assert order.errors == []
    assert [item.path for item in order.items] == ["a.py"]


@pytest.mark.parametrize(
    "row",
    [
        "| CREATE | a.py | 세 칸뿐 |",
        "| CREATE | a.py |  | 다섯 | 칸 |",
    ],
)
def test_a_row_that_is_not_four_cells_is_refused(row):
    order = workorder.parse_work_order(order_doc(row))
    assert order.items == []
    assert any("정확히 4칸" in reason for reason in order.errors)


def test_a_header_that_does_not_match_is_refused():
    text = (
        "# PLAN\n\n## 작업 지시서\n\n"
        "| verb | path | symbol | duty |\n| --- | --- | --- | --- |\n"
        "| CREATE | a.py |  | x |\n"
    )
    order = workorder.parse_work_order(text)
    assert order.items == []
    assert any("표 헤더가 다르다" in reason for reason in order.errors)


def test_the_symbol_column_alone_is_case_insensitive():
    text = (
        "# PLAN\n\n## 작업 지시서\n\n"
        "| 동사 | 대상 경로 | Symbol | 책임 |\n| --- | --- | --- | --- |\n"
        "| CREATE | a.py |  | x |\n"
    )
    assert workorder.parse_work_order(text).errors == []


def test_a_missing_separator_row_is_refused():
    text = (
        "# PLAN\n\n## 작업 지시서\n\n"
        "| 동사 | 대상 경로 | symbol | 책임 |\n"
        "| CREATE | a.py |  | x |\n"
    )
    order = workorder.parse_work_order(text)
    assert order.items == []
    assert any("구분자" in reason for reason in order.errors)


def test_a_section_with_no_table_and_a_table_with_no_rows_both_refuse():
    empty = workorder.parse_work_order("# PLAN\n\n## 작업 지시서\n\n표가 없다.\n")
    assert any("표가 없다" in reason for reason in empty.errors)

    headers_only = workorder.parse_work_order("# PLAN\n\n## 작업 지시서\n\n" + HEADER + "\n")
    assert any("데이터 행이 하나도 없다" in reason for reason in headers_only.errors)


def test_an_escaped_pipe_stays_inside_the_cell():
    order = workorder.parse_work_order(
        order_doc("| MODIFY | a.py |  | a \\| b 를 고친다 |")
    )
    assert order.errors == []
    assert order.items[0].note == "a | b 를 고친다"


def test_a_korean_path_with_spaces_survives():
    order = workorder.parse_work_order(order_doc("| MODIFY | 문서/설계 노트.md |  | 갱신 |"))
    assert order.errors == []
    assert order.items[0].path == "문서/설계 노트.md"


def test_the_english_heading_is_the_same_heading():
    order = workorder.parse_work_order(
        order_doc("| CREATE | a.py |  | x |", title="### WORK ORDER:")
    )
    assert order.errors == []
    assert [item.path for item in order.items] == ["a.py"]


# --------------------------------------------------------------------- paths


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/etc/passwd",
        "C:/Windows/system32",
        "a\\b.py",
        "a//b.py",
        "a/",
        "a.py:stream",
        "CON.py",
        "aux/x.py",
        "a./x.py",
        "a /x.py",
        "a/../b.py",
        "",
    ],
)
def test_a_path_that_is_not_a_repo_relative_path_is_refused(path):
    _, reason = workorder.normalize_path(path)
    assert reason


def test_a_control_character_is_refused():
    _, reason = workorder.normalize_path("a\x07b.py")
    assert "제어문자" in reason


@pytest.mark.parametrize("path", ["a.py", "aidev/graph/db.py", "tests/test_x.py", "console.py"])
def test_an_ordinary_path_is_accepted(path):
    value, reason = workorder.normalize_path(path)
    assert (value, reason) == (path, "")


def test_a_bad_path_refuses_the_row_rather_than_the_document():
    order = workorder.parse_work_order(
        order_doc("| MODIFY | ../outside.py |  | 나간다 |", "| MODIFY | a.py |  | 남는다 |")
    )
    assert [item.path for item in order.items] == ["a.py"]
    assert any("'.'와 '..'" in reason for reason in order.errors)


def test_escapes_root_sees_through_a_symlink(tmp_path):
    root = tmp_path / "repo"
    (root / "inside").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    assert workorder.escapes_root(root, "inside/a.py") is False
    assert workorder.escapes_root(root, "does/not/exist/yet.py") is False

    try:
        os.symlink(str(tmp_path / "elsewhere"), str(root / "away"), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("this machine will not make a symlink")
    assert workorder.escapes_root(root, "away/a.py") is True


# --------------------------------------------------------- duplicates (§8)


def test_the_same_path_under_two_verbs_is_refused():
    order = workorder.parse_work_order(
        order_doc("| CREATE | a.py |  | 만든다 |", "| MODIFY | a.py |  | 고친다 |")
    )
    assert any("서로 다른 동사" in reason for reason in order.errors)


def test_the_same_path_and_symbol_twice_is_refused():
    order = workorder.parse_work_order(
        order_doc("| MODIFY | a.py | run |  하나 |", "| MODIFY | a.py | run | 둘 |")
    )
    assert any("두 번 있다" in reason for reason in order.errors)


def test_two_bare_rows_for_one_path_is_the_same_duplicate():
    order = workorder.parse_work_order(
        order_doc("| MODIFY | a.py |  | 하나 |", "| MODIFY | a.py |  | 둘 |")
    )
    assert any("두 번 있다" in reason for reason in order.errors)


def test_two_different_symbols_in_one_file_are_the_normal_shape():
    order = workorder.parse_work_order(
        order_doc("| MODIFY | a.py | run | 하나 |", "| MODIFY | a.py | stop | 둘 |")
    )
    assert order.errors == []
    assert [item.symbol for item in order.items] == ["run", "stop"]


def test_a_bare_row_beside_a_symbol_row_is_refused():
    order = workorder.parse_work_order(
        order_doc("| MODIFY | a.py |  | 파일 단위 |", "| MODIFY | a.py | run | 함수 단위 |")
    )
    assert any("함께 있다" in reason for reason in order.errors)


def test_a_create_row_may_not_carry_a_symbol():
    order = workorder.parse_work_order(order_doc("| CREATE | a.py | run | 만든다 |"))
    assert any("CREATE는 아직 없는 파일" in reason for reason in order.errors)


# ------------------------------------------------------------------ digests


def rows(*triples):
    return [Item(verb=v, path=p, symbol=s) for v, p, s in triples]


def test_the_scope_digest_ignores_row_order_and_the_duty_column():
    one = rows(("MODIFY", "a.py", ""), ("CREATE", "b.py", ""))
    other = [Item(verb="CREATE", path="b.py", note="완전히 다른 문장")] + [
        Item(verb="MODIFY", path="a.py", note="이것도")
    ]
    assert workorder.items_digest(one) == workorder.items_digest(other)


def test_the_scope_digest_moves_when_a_symbol_does():
    assert workorder.items_digest(rows(("MODIFY", "a.py", "run"))) != workorder.items_digest(
        rows(("MODIFY", "a.py", "stop"))
    )


def test_the_document_digest_answers_to_one_byte():
    assert workorder.document_digest("plan") != workorder.document_digest("plam")
    assert workorder.document_digest("plan") == workorder.document_digest(b"plan")


def test_items_survive_a_round_trip_through_state():
    items = rows(("MODIFY", "a.py", "run"), ("CREATE", "b.py", ""))
    back = workorder.items_from_dicts([item.to_dict() for item in items])
    assert [item.key for item in back] == [item.key for item in items]
    # junk in the list is dropped rather than raised over
    assert workorder.items_from_dicts([{"verb": "NOPE", "path": "a"}, 3, None]) == []


def test_a_rendered_table_parses_back_to_the_same_rows():
    items = workorder.parse_work_order(
        order_doc("| MODIFY | a.py | run | a \\| b |", "| CREATE | b.py |  | 만든다 |")
    ).items
    again = workorder.parse_work_order("## 작업 지시서\n\n" + workorder.render_table(items))
    assert again.errors == []
    assert [item.key for item in again.items] == [item.key for item in items]


# ----------------------------------------------------------------- classify


ORDER = rows(("CREATE", "new.py", ""), ("MODIFY", "old.py", ""), ("REFERENCE", "read.py", ""))


def test_a_declared_creation_and_a_declared_edit_pass():
    verdict = workorder.classify({"new.py": "A", "old.py": "M"}, ORDER)
    assert verdict.ok is True
    assert verdict.unplanned == []
    assert sorted(verdict.declared) == ["new.py", "old.py"]


def test_a_new_file_nobody_declared_fails():
    verdict = workorder.classify({"evil.py": "A"}, ORDER)
    assert verdict.ok is False
    assert verdict.added == ["evil.py"]
    assert "지시서의 CREATE에 없는 새 파일" in verdict.failures[0]


def test_a_deletion_nobody_declared_fails():
    verdict = workorder.classify({"read.py": "D"}, ORDER)
    assert verdict.ok is False
    assert verdict.deleted == ["read.py"]
    assert "MODIFY로 선언한다" in verdict.failures[0]


def test_a_declared_modify_may_be_deleted():
    verdict = workorder.classify({"old.py": "D"}, ORDER)
    assert verdict.ok is True
    assert verdict.deleted == []


def test_an_undeclared_edit_is_a_debt_and_not_a_refusal():
    verdict = workorder.classify({"README.md": "M", "read.py": "T"}, ORDER)
    assert verdict.ok is True
    assert verdict.unplanned == ["README.md", "read.py"]


def test_an_excluded_path_is_not_judged_at_all():
    verdict = workorder.classify(
        {".aidev/history/s/slice.json": "M", "evil.py": "A"},
        ORDER,
        excluded={".aidev/history/s/slice.json", "evil.py"},
    )
    assert verdict.ok is True
    assert (verdict.unplanned, verdict.added) == ([], [])


# ------------------------------------------------------------- reason table


def test_a_reason_table_reads_back_as_pairs():
    reasons = workorder.parse_reasons(reasons_doc("| README.md | 문서가 사실과 달라졌다 |"))
    assert reasons.errors == []
    assert reasons.rows == [("README.md", "문서가 사실과 달라졌다")]
    assert reasons.paths == ["README.md"]


def test_no_reason_section_is_legal_and_says_so():
    reasons = workorder.parse_reasons("끝났다.")
    assert (reasons.present, reasons.rows, reasons.errors) == (False, [], [])


def test_two_reason_sections_are_refused():
    text = reasons_doc("| a.py | 하나 |") + reasons_doc("| b.py | 둘 |")
    reasons = workorder.parse_reasons(text)
    assert reasons.rows == []
    assert any("절이 2개" in reason for reason in reasons.errors)


def test_a_reason_row_with_no_reason_is_refused():
    reasons = workorder.parse_reasons(reasons_doc("| a.py |  |"))
    assert any("사유가 비어 있다" in reason for reason in reasons.errors)


def test_a_reason_row_with_a_path_that_is_not_one_is_refused():
    reasons = workorder.parse_reasons(reasons_doc("| ../a.py | 밖이다 |"))
    assert any("'.'와 '..'" in reason for reason in reasons.errors)


# ------------------------------------------------------------ match_reasons


def test_an_exact_match_passes():
    assert workorder.match_reasons(["a.py", "b.py"], ["b.py", "a.py"]) == []


def test_a_missing_reason_fails():
    failures = workorder.match_reasons(["a.py", "b.py"], ["a.py"])
    assert failures and "사유가 없다" in failures[0]
    assert "b.py" in failures[0]


def test_an_extra_reason_fails():
    failures = workorder.match_reasons(["a.py"], ["a.py", "b.py"])
    assert failures and "범위 밖 수정이 아닌 경로" in failures[0]


def test_a_repeated_reason_fails():
    failures = workorder.match_reasons(["a.py"], ["a.py", "a.py"])
    assert any("같은 경로가 두 번" in failure for failure in failures)


def test_a_reason_declared_in_an_earlier_check_is_not_asked_for_again():
    assert workorder.match_reasons(["a.py"], [], carried={"a.py"}) == []
    # but a new one still is
    failures = workorder.match_reasons(["a.py", "b.py"], [], carried={"a.py"})
    assert failures and "b.py" in failures[0] and "a.py" not in failures[0]
