#!/usr/bin/env python3
"""A mock OpenAI-compatible model server for testing the pipeline without any model.

It answers by role (found in the prompt) and by model id, which selects the quality of
a build: `good` by default, or a model id containing buggy, isdigit, syntax, importhang or loop.
Builds come from the reference solutions in tests/fixtures. Unknown model ids get a 404,
so a test fails if the proxy did not rewrite the name or the fake `lms` did not load it.

    python tests/mock_server.py --port 1234 --models a,b,c --state-dir /tmp/mock
"""
import argparse
import http.server
import json
import os
import re
import ssl
import time

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TASK_FOLDERS = {"bepay": "toy-bepay", "cronnext": "cron-next", "walkv": "wal-kv"}
JUDGE_MODEL = "mock-judge"
JUDGE_KEY = "judge-secret"

PLAN = """# Plan (mock)

## Overview
Implement what TASK.md describes, as one small package. This text comes from a mock server,
so it is only long enough to pass the harness's check for suspiciously short plans.

## Files
The files listed in TASK.md.

## Functions
As specified in TASK.md, with every edge case it names.

## Error handling
As specified in TASK.md.

## Test plan
One test per rule in TASK.md.

## Build order
1. Write the code. 2. Write the tests. 3. Commit.
"""

REVIEW = """# Review (mock)

## Summary
A canned review from the mock server.

## Issues
| # | Severity | Where | Problem | Suggested fix |
|---|---|---|---|---|

## Requirement checklist
Not assessed by the mock.

## Plan adherence
Not assessed by the mock.

## Verdict
fix first
"""


def build_files(task, variant):
    folder = os.path.join(FIXTURES, task)
    good = os.path.join(folder, "good")
    files = {}
    for root, _dirs, names in os.walk(good):
        for name in names:
            if name.endswith(".py"):
                full = os.path.join(root, name)
                with open(full, encoding="utf-8") as fh:
                    files[os.path.relpath(full, good).replace(os.sep, "/")] = fh.read()
    with open(os.path.join(folder, "variants.json"), encoding="utf-8") as fh:
        variants = json.load(fh)
    init = variants["package"] + "/__init__.py"
    if variant == "syntax":
        files[init] = "def (\n"
    elif variant == "importhang":
        files[init] = "while True:\n    pass\n"
    elif variant != "good":
        for edit in variants[variant]["edits"]:
            assert edit["find"] in files[edit["file"]], edit["find"]
            files[edit["file"]] = files[edit["file"]].replace(edit["find"], edit["replace"])
    return files


def variant_of(model):
    for name in ("buggy", "isdigit", "udigits", "lenient", "newline", "syntax", "importhang", "loop"):
        if name in model:
            return name
    return "good"


def judge_answer(packet):
    letters = re.findall(r"^# Build ([A-Z]+)$", packet, re.MULTILINE)
    results = re.findall(r"^Passed \*\*(\d+) of (\d+)", packet, re.MULTILINE)
    reviews = re.findall(r"^## Review (\S+)$", packet, re.MULTILINE)
    builds = {}
    best, best_score = None, -1.0
    for letter, (passed, total) in zip(letters, results):
        share = int(passed) / max(int(total), 1)
        score = int(round(1 + 4 * share))
        builds[letter] = {"correctness": score, "plan_adherence": 3, "code_quality": 3,
                          "edge_cases": score, "own_tests": 1}
        if share > best_score:
            best, best_score = letter, share
    data = {"builds": builds,
            "reviews": {r: {"real_issues": 0, "false_alarms": 0, "missed_important": 1,
                            "accuracy": 3, "usefulness": 2} for r in reviews},
            "preferred_build": best, "one_line_verdict": "Canned verdict from the mock server."}
    return "# Judge scores\n\n## Verdict\nMock.\n\n## Scores (JSON)\n\n```json\n%s\n```\n" % json.dumps(data, indent=2)


def answer_for(messages, model):
    text = "\n".join(m.get("content") or "" for m in messages if m.get("role") != "assistant")
    if any(m.get("role") == "assistant" for m in messages):
        return "DONE"
    if "# Your role: impartial judge" in text:
        return judge_answer(text)
    if "Reply with exactly these two words" in text:
        return "pipeline OK"
    if "# Your role: planner" in text:
        return '<file path="plan.md">\n%s</file>\nPLAN DONE' % PLAN
    if "# Your role: code reviewer" in text:
        return '<file path="REVIEW.md">\n%s</file>\nREVIEW DONE' % REVIEW
    if "# Your role: developer" in text:
        match = re.search(r"^# Task: `(\w+)`", text, re.MULTILINE)
        files = build_files(TASK_FOLDERS[match.group(1)], variant_of(model))
        blocks = ['<file path="%s">\n%s</file>' % item for item in sorted(files.items())]
        return "\n".join(blocks) + "\nBUILD DONE"
    return "OK"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    options = None

    def log_message(self, fmt, *args):
        pass

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self):
        path = self.path.split("?", 1)[0]
        prefix = self.options.prefix
        if prefix:
            if not path.startswith(prefix + "/"):
                return None
            path = path[len(prefix):]
        return path

    def _loaded(self):
        try:
            with open(os.path.join(self.options.state_dir, "loaded.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def do_GET(self):
        path = self._route()
        models = self.options.models.split(",")
        if self.options.lmstudio and path == "/api/v1/models":
            rows = [dict({"key": m, "type": "llm"}, **({"format": "mlx", "quantization": {"name": "4bit"}} if "good" in m else {}))
                    for m in models] + [{"key": "text-embedding-x", "type": "embedding"}]
            self._json(200, {"models": rows})
        elif path == "/v1/models":
            # LM Studio with Just-in-Time loading off lists only what is loaded.
            ids = list(self._loaded()) if self.options.lmstudio else models + ["text-embedding-x"]
            self._json(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})
        else:
            self._json(404, {"error": {"message": "not found: %s" % self.path}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length) or b"{}")
        path = self._route()
        auth = self.headers.get("Authorization")
        model = req.get("model") or ""
        with open(os.path.join(self.options.state_dir, "requests.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"path": self.path, "model": model, "auth": auth,
                                 "stream_options": req.get("stream_options"), "temperature": req.get("temperature"),
                                 "max_tokens": req.get("max_tokens")}) + "\n")
        if path != "/v1/chat/completions":
            return self._json(404, {"error": {"message": "not found: %s" % self.path}})
        if model == JUDGE_MODEL:
            if auth != "Bearer " + JUDGE_KEY:
                return self._json(401, {"error": {"message": "bad judge key"}})
        else:
            if self.options.require_key and auth != "Bearer " + self.options.require_key:
                return self._json(401, {"error": {"message": "bad backend key"}})
            model = self._loaded().get(model, model)
            if model not in self.options.models.split(","):
                return self._json(404, {"error": {"message": "unknown model: %s" % model}})
        content = answer_for(req.get("messages") or [], model)
        usage = {"prompt_tokens": max(length // 4, 1), "completion_tokens": max(len(content) // 4, 1)}
        if "cached" in model:
            usage["prompt_tokens_details"] = {"cached_tokens": max(length // 8, 1)}
        if not req.get("stream"):
            return self._json(200, {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": usage})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(event):
            line = b"data: " + (json.dumps(event).encode() if isinstance(event, dict) else event) + b"\n\n"
            self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
            self.wfile.flush()

        time.sleep(0.01)
        if "think" in model:
            send({"choices": [{"delta": {"reasoning": "thinking"}}]})
        pause = 0.05 if "slow" in model else 0.001
        pieces = [content[i:i + 400] for i in range(0, len(content), 400)] or [""]
        if "slow" in model:
            pieces = list(content) * 200
        for piece in pieces:
            send({"choices": [{"delta": {"content": piece}}]})
            time.sleep(pause)
        if (req.get("stream_options") or {}).get("include_usage") and "nousage" not in model:
            send({"choices": [], "usage": usage})
        send(b"[DONE]")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--lmstudio", action="store_true", help="also behave like LM Studio's native API")
    parser.add_argument("--prefix", default="", help="serve under this path, e.g. /custom")
    parser.add_argument("--require-key", default="", help="require this bearer key for model calls")
    parser.add_argument("--tls-cert", default="")
    parser.add_argument("--tls-key", default="")
    options = parser.parse_args()
    Handler.options = options
    server = Server(("127.0.0.1", options.port), Handler)
    if options.tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(options.tls_cert, options.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print("mock server on port %d" % options.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
