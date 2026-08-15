from aidev import events as ev


def test_parse_line_ignores_noise():
    assert ev.parse_line("") is None
    assert ev.parse_line("not json") is None
    assert ev.parse_line("[1,2]") is None
    assert ev.parse_line('{"type": "system"}') == {"type": "system"}


def test_tool_target_and_category():
    assert ev.tool_target("Read", {"file_path": "/repo/src/gameSession.js"}) == "/repo/src/gameSession.js"
    assert ev.tool_target("Bash", {"command": "npm  test\n--silent"}) == "npm test --silent"
    assert ev.tool_target("Grep", {"pattern": "doctor"}) == "doctor"
    assert ev.tool_target("Read", {}) == ""

    assert ev.tool_category("Read") == "explore"
    assert ev.tool_category("Edit") == "edit"
    assert ev.tool_category("Bash", {"command": "ls -al"}) == "exec"
    assert ev.tool_category("Bash", {"command": "npm run test:unit"}) == "test"
    assert ev.tool_category("TodoWrite") == "other"


def test_looks_like_test():
    assert ev.looks_like_test("cd backend && pytest -x")
    assert ev.looks_like_test("npx vitest run")
    assert ev.looks_like_test("go test ./...")
    assert not ev.looks_like_test("git status")
    assert not ev.looks_like_test("cat testdata.json")


def test_file_operation():
    assert ev.file_operation("Read") == "read"
    assert ev.file_operation("Edit") == "edit"
    assert ev.file_operation("Write") == "write"
    assert ev.file_operation("Bash") is None


def test_iter_tool_uses_and_results():
    assistant = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.js"}},
            ]
        },
    }
    uses = ev.iter_tool_uses(assistant)
    assert len(uses) == 1
    assert uses[0].id == "t1"
    assert uses[0].name == "Read"
    assert uses[0].target == "a.js"
    assert uses[0].category == "explore"
    assert ev.assistant_text_bytes(assistant) == 5

    user = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "12345", "is_error": False}
            ]
        },
    }
    results = ev.iter_tool_results(user)
    assert len(results) == 1
    assert results[0].tool_use_id == "t1"
    assert results[0].result_bytes == 5
    assert results[0].is_error is False

    # a user message with no tool_result yields nothing
    assert ev.iter_tool_results({"type": "user", "message": {"content": "hi"}}) == []
    assert ev.iter_tool_uses(user) == []


def test_content_bytes_handles_shapes():
    assert ev.content_bytes(None) == 0
    assert ev.content_bytes("abc") == 3
    assert ev.content_bytes("한글") == 6
    assert ev.content_bytes([{"type": "text", "text": "abcd"}, {"type": "text", "text": "ef"}]) == 6
    assert ev.content_bytes({"source": {"data": "x" * 100}}) == 100
    assert ev.content_bytes({"file_path": "a.js"}) > 0


def test_extract_usage_from_both_places():
    assert ev.extract_usage({"usage": {"input_tokens": 10, "x": "no"}}) == {"input_tokens": 10}
    assert ev.extract_usage({"message": {"usage": {"output_tokens": 5}}}) == {"output_tokens": 5}
    assert ev.extract_usage({"type": "result"}) == {}


def test_estimate_tokens():
    assert ev.estimate_tokens(0) == 0
    assert ev.estimate_tokens(-5) == 0
    assert ev.estimate_tokens(4000) == 1000
