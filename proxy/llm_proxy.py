#!/usr/bin/env python3
"""Logging proxy between a coding agent and an OpenAI-compatible model server.

The agent talks to this proxy (http://127.0.0.1:1235/v1) instead of directly to
the model server. For each model call the proxy records:

  run id, stage, model, input tokens, output tokens,
  time to first token, generation speed (tokens/sec), total time,
  the sampling settings the agent sent, and integrity hits (see below).
  It also records the size of each request as the agent sent it (bytes and number of
  messages), and cached prompt tokens when the server reports them. Comparing the
  request size with the server's input tokens shows who shortened a long prompt.

Which run and stage a call belongs to is read from <logs_dir>/current-stage.json,
which `pipeline.py next` / `start` / `load` write. A request path that starts
with /run/<run-id>/<stage>/ overrides that (used by the smoke test).

Requests are forwarded unchanged, with four exceptions:

  * When the current stage names a `model_id` (servers without a load step), the
    request's `model` is rewritten to it, and GET /v1/models lists only the alias.
  * Streaming chat requests get `stream_options.include_usage`, so token counts
    come from the server instead of being estimated from chunks.
  * If backend.api_key_env is set, that key replaces the agent's Authorization.
  * Settings in the config's `request_overrides` replace what the agent sent.

The proxy also keeps <logs_dir>/proxy-activity.json current: a change counter and
the number of calls in flight. `pipeline.py run` uses it to tell a slow model from
a stuck agent, because a call is only logged once it has ended.

Integrity hits: the proxy searches each request body for the stage's `sentinels`
(for example `hidden_tests`). Agents resend what they have read, so a hit means
the model probably saw something it should not have. It is a best-effort flag.

Start it with:   python proxy/llm_proxy.py
Stop it with:    Ctrl+C

Standard library only. Works with Python 3.9+.
"""
import hashlib
import http.client
import http.server
import json
import os
import re
import sys
import threading
import time
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.dirname(HERE)
# Which code this process is running, so `check` and `run` can spot a proxy from before an update.
with open(os.path.abspath(__file__), "rb") as _source:
    CODE_HASH = hashlib.sha256(_source.read()).hexdigest()[:16]
DEFAULT_UPSTREAM = "http://127.0.0.1:1234"
DEFAULT_PORT = 1235


def normalise_upstream(url):
    """Backend root without a trailing slash or /v1; requests already carry /v1."""
    url = url.strip().rstrip("/")
    return url[:-3].rstrip("/") if url.endswith("/v1") else url


def _load_settings():
    upstream, port, alias, logs_dir, key_env = DEFAULT_UPSTREAM, DEFAULT_PORT, "local-model", "logs", ""
    cfg_path = os.environ.get("RELAYBENCH_CONFIG") or os.path.join(HARNESS, "pipeline.json")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        backend = cfg.get("backend") or {}
        upstream = backend.get("url", upstream)
        key_env = backend.get("api_key_env") or ""
        port = int(cfg.get("proxy_port", port))
        alias = cfg.get("served_model_name", alias)
        logs_dir = cfg.get("logs_dir", logs_dir)
    except (OSError, ValueError):
        pass
    upstream = os.environ.get("UPSTREAM_URL", upstream)
    port = int(os.environ.get("PROXY_PORT", port))
    logs_dir = os.path.normpath(os.path.join(HARNESS, os.path.expanduser(logs_dir)))
    return normalise_upstream(upstream), port, alias, logs_dir, key_env


UPSTREAM, PORT, ALIAS, LOG_DIR, API_KEY_ENV = _load_settings()
LOG_FILE = os.path.join(LOG_DIR, "proxy-log.jsonl")
STAGE_FILE = os.path.join(LOG_DIR, "current-stage.json")
ACTIVITY_FILE = os.path.join(LOG_DIR, "proxy-activity.json")
PREFIX_RE = re.compile(r"^/run/([^/]+)/([^/]+)(/.*)?$")
MODEL_CALL_RE = re.compile(r"/(v1/messages|v1/chat/completions|v1/completions|api/v\d+/chat)(\?.*)?$")
MODELS_LIST_RE = re.compile(r"^/v1/models/?(\?.*)?$")
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
    "accept-encoding",
}
_log_lock = threading.Lock()
_activity_lock = threading.Lock()
_activity = {"seq": 0, "in_flight": 0}


def current_stage():
    """The stage written by pipeline.py (or an empty one if there is none)."""
    try:
        with open(STAGE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def note_activity(change=0):
    with _activity_lock:
        _activity["seq"] += 1
        _activity["in_flight"] += change
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            tmp = ACTIVITY_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_activity, fh)
            os.replace(tmp, ACTIVITY_FILE)
        except OSError:  # it only informs `pipeline.py run`, so it must never break a model call
            pass


def _fmt(value, digits=1):
    return "-" if value is None else ("%." + str(digits) + "f") % value


def write_log(record):
    os.makedirs(LOG_DIR, exist_ok=True)
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    print(
        "[%s] %-28s %-22s in=%-7s out=%-6s ttft=%ss gen=%s tok/s total=%ss%s%s"
        % (
            time.strftime("%H:%M:%S"),
            (record["stage"] or "-")[:28],
            (record.get("model") or "-")[:22],
            record.get("input_tokens") if record.get("input_tokens") is not None else "-",
            record.get("output_tokens") if record.get("output_tokens") is not None else "-",
            _fmt(record.get("ttft_s")),
            _fmt(record.get("gen_tps")),
            _fmt(record.get("total_s")),
            "  (HTTP %s)" % record["status"] if (record.get("status") or 200) >= 400 else "",
            "  INTEGRITY: %s" % ", ".join(record["integrity_hits"]) if record.get("integrity_hits") else "",
        ),
        flush=True,
    )


class StreamStats:
    """Reads Anthropic- or OpenAI-style server-sent events and collects stats."""

    def __init__(self):
        self.first_token_at = None
        self.input_tokens = None
        self.output_tokens = None
        self.cached_tokens = None
        self.delta_count = 0

    def feed_line(self, line, now):
        if not line.startswith(b"data:"):
            return
        raw = line[5:].strip()
        if not raw or raw == b"[DONE]":
            return
        try:
            event = json.loads(raw)
        except ValueError:
            return
        etype = event.get("type")
        if etype == "message_start":
            usage = (event.get("message") or {}).get("usage") or {}
            self._take_usage(usage)
        elif etype == "content_block_delta":
            self.delta_count += 1
            if self.first_token_at is None:
                self.first_token_at = now
        elif etype == "message_delta":
            self._take_usage(event.get("usage") or {})
        elif "choices" in event:  # OpenAI-style chunk
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                if (delta.get("content") or delta.get("reasoning_content")
                        or delta.get("reasoning") or delta.get("tool_calls")):
                    self.delta_count += 1
                    if self.first_token_at is None:
                        self.first_token_at = now
            if event.get("usage"):
                self._take_usage(event["usage"])

    def _take_usage(self, usage):
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and details.get("cached_tokens") is not None:
            self.cached_tokens = details["cached_tokens"]
        elif usage.get("cache_read_input_tokens") is not None:
            self.cached_tokens = usage["cache_read_input_tokens"]
        inp = usage.get("input_tokens", usage.get("prompt_tokens"))
        if inp:
            cached = (usage.get("cache_read_input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
            self.input_tokens = inp + cached
        out = usage.get("output_tokens", usage.get("completion_tokens"))
        if out:
            self.output_tokens = out


def apply_stream_stats(record, stats, started, ended):
    """Also runs for aborted streams: a cancelled, looping model is a finding too."""
    record["input_tokens"] = stats.input_tokens
    record["cached_tokens"] = stats.cached_tokens
    if stats.output_tokens:
        record["output_tokens"] = stats.output_tokens
        record["usage_source"] = "server"
    elif stats.delta_count:
        record["output_tokens"] = stats.delta_count
        record["usage_source"] = "chunks"
    if stats.first_token_at is not None:
        record["ttft_s"] = stats.first_token_at - started
        gen_time = ended - stats.first_token_at
        if record["output_tokens"] and gen_time > 0:
            record["gen_tps"] = record["output_tokens"] / gen_time
        if stats.input_tokens and record["ttft_s"] > 0:
            record["prefill_tps"] = stats.input_tokens / record["ttft_s"]


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console readable
        pass

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def do_OPTIONS(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def _send_simple(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_chunk(self, data):
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def _handle(self):
        self._counted = False
        try:
            self._forward()
        finally:
            if self._counted:
                note_activity(-1)

    def _forward(self):
        started = time.time()
        if self.path == "/__health":
            self._send_simple(200, {"ok": True, "upstream": UPSTREAM, "pid": os.getpid(), "code": CODE_HASH})
            return

        stage_info = current_stage()
        match = PREFIX_RE.match(self.path)
        if match:
            run_id, stage, rest = match.group(1), match.group(2), match.group(3) or "/"
            stage_model, model_id, sentinels, overrides = None, None, [], {}
        else:
            run_id, stage, rest = stage_info.get("run_id", "-"), stage_info.get("stage", "-"), self.path
            stage_model = stage_info.get("model")
            model_id = stage_info.get("model_id")
            sentinels = stage_info.get("sentinels") or []
            overrides = stage_info.get("overrides") or {}

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        request_bytes, messages = len(body), None

        if model_id and self.command == "GET" and MODELS_LIST_RE.match(rest):
            self._send_simple(200, {"object": "list", "data": [
                {"id": ALIAS, "object": "model", "owned_by": "relaybench"}]})
            return

        is_model_call = bool(MODEL_CALL_RE.search(rest)) and self.command == "POST"
        if is_model_call:
            self._counted = True
            note_activity(1)
        hits = []
        if is_model_call and sentinels:
            text = body.decode("utf-8", "replace")
            hits = [s for s in sentinels if s in text]

        model, wants_stream, temperature, max_tokens, pinned = None, False, None, None, []
        if body:
            try:
                req = json.loads(body)
                listed = req.get("messages", req.get("input"))
                messages = len(listed) if isinstance(listed, list) else None
                model = req.get("model")
                wants_stream = bool(req.get("stream"))
                temperature = req.get("temperature")
                max_tokens = req.get("max_tokens", req.get("max_completion_tokens"))
                changed = False
                if is_model_call and overrides:
                    req.update(overrides)
                    if "max_tokens" in overrides:
                        req.pop("max_completion_tokens", None)
                    temperature, max_tokens = req.get("temperature"), req.get("max_tokens", max_tokens)
                    pinned = sorted(overrides)
                    changed = True
                if is_model_call and model_id and "model" in req:
                    req["model"] = model_id
                    changed = True
                if is_model_call and wants_stream and "chat/completions" in rest:
                    options = req.get("stream_options") or {}
                    if not options.get("include_usage"):
                        req["stream_options"] = dict(options, include_usage=True)
                        changed = True
                if changed:
                    body = json.dumps(req).encode()
            except (ValueError, AttributeError):
                pass
        model = stage_model or model

        up = urlsplit(UPSTREAM)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Host"] = up.netloc
        headers["Accept-Encoding"] = "identity"
        if body:
            headers["Content-Length"] = str(len(body))
        api_key = os.environ.get(API_KEY_ENV) if API_KEY_ENV else None
        if api_key:
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
            headers["Authorization"] = "Bearer " + api_key

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": started,
            "run_id": run_id,
            "stage": stage,
            "path": rest,
            "model": model,
            "stream": wants_stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "pinned": pinned,
            "status": None,
            "request_bytes": request_bytes,
            "messages": messages,
            "input_tokens": None,
            "cached_tokens": None,
            "output_tokens": None,
            "usage_source": None,
            "ttft_s": None,
            "gen_tps": None,
            "prefill_tps": None,
            "total_s": None,
            "aborted": False,
            "integrity_hits": hits,
        }

        if up.scheme == "https":
            conn = http.client.HTTPSConnection(up.hostname, up.port or 443, timeout=3600)
        else:
            conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=3600)
        try:
            conn.request(self.command, up.path + rest, body=body or None, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            conn.close()
            record["status"] = 502
            record["total_s"] = time.time() - started
            if is_model_call:
                write_log(record)
            self._send_simple(502, {
                "type": "error",
                "error": {"type": "proxy_error", "message": "Model server not reachable at %s: %s" % (UPSTREAM, exc)},
            })
            return

        record["status"] = resp.status
        content_type = resp.getheader("Content-Type", "")
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)

        stats = None
        try:
            if "text/event-stream" in content_type:
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                stats = StreamStats()
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    stats.feed_line(line, time.time())
                    self._write_chunk(line)
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            else:
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                self.wfile.flush()
                if is_model_call:
                    try:
                        usage = json.loads(data).get("usage") or {}
                        usage_stats = StreamStats()
                        usage_stats._take_usage(usage)
                        record["input_tokens"] = usage_stats.input_tokens
                        record["cached_tokens"] = usage_stats.cached_tokens
                        record["output_tokens"] = usage_stats.output_tokens
                        if usage_stats.output_tokens:
                            record["usage_source"] = "server"
                    except (ValueError, AttributeError):
                        pass
        except (OSError, http.client.HTTPException):
            record["aborted"] = True
            self.close_connection = True
        finally:
            conn.close()

        if stats is not None:
            apply_stream_stats(record, stats, started, time.time())
        record["total_s"] = time.time() - started
        if is_model_call:
            write_log(record)


class ThreadingServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadingServer(("127.0.0.1", PORT), ProxyHandler)
    note_activity()  # clears what a proxy that crashed mid-call left behind
    print("relaybench logging proxy listening on http://127.0.0.1:%d" % PORT)
    print("Forwarding to the model server at %s" % UPSTREAM)
    print("Logging model calls to %s" % LOG_FILE)
    print("Leave this window open. Press Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
