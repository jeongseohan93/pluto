"""Stand-in for the ``claude`` CLI used by the runner tests.

Reads the prompt from stdin (like ``claude -p`` does), then emits a small
stream-json session on stdout, one JSON object per line, plus one deliberately
malformed line and some stderr noise.
"""

import json
import sys


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    prompt = sys.stdin.read()
    sys.stderr.write("fake-claude: got {0} prompt chars\n".format(len(prompt)))
    sys.stderr.write("fake-claude: args {0}\n".format(" ".join(sys.argv[1:])))
    sys.stderr.flush()

    emit({"type": "system", "subtype": "init", "session_id": "fake-session", "model": "fake-model"})
    first = {
        "type": "assistant",
        "session_id": "fake-session",
        "request_id": "req_fake_1",
        "message": {
            "id": "msg_1",
            "usage": {
                "input_tokens": 40,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 1000,
                "output_tokens": 30,
            },
            "content": [
                {"type": "text", "text": "reading"},
                {"type": "tool_use", "id": "u1", "name": "Read", "input": {"file_path": "a.js"}},
            ],
        },
    }
    emit(first)
    emit(first)  # same request_id twice: the runner must count it once
    emit(
        {
            "type": "user",
            "session_id": "fake-session",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "y" * 512}]
            },
        }
    )
    sys.stdout.write("this line is not json\n")  # parser must survive this
    sys.stdout.flush()
    emit(
        {
            "type": "assistant",
            "session_id": "fake-session",
            "request_id": "req_fake_2",
            "message": {
                "id": "msg_2",
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 1400,
                    "output_tokens": 20,
                },
                "content": [
                    {"type": "tool_use", "id": "u2", "name": "Bash", "input": {"command": "pytest -q"}}
                ],
            },
        }
    )
    emit(
        {
            "type": "user",
            "session_id": "fake-session",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "u2", "content": "z" * 256}]
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "session_id": "fake-session",
            "is_error": False,
            "num_turns": 2,
            "duration_ms": 4321,
            "duration_api_ms": 3210,
            "total_cost_usd": 0.0421,
            "usage": {
                "input_tokens": 50,
                "cache_creation_input_tokens": 300,
                "cache_read_input_tokens": 2400,
                "output_tokens": 50,
            },
            "result": "ok",
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
