"""End-to-end tests: the real CLI and the real proxy process against a mock model server.

No model and no real agent are involved: tests/scripted_agent.py plays the agent's part.
Set RELAYBENCH_WRITE_EXAMPLE=<file> to save the toy run's report as a labelled example.
"""
import importlib.util
import json
import http.server
import os
import re
import shlex
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from scripted_agent import run_agent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE = os.path.join(ROOT, "pipeline.py")
PROXY = os.path.join(ROOT, "proxy", "llm_proxy.py")
MOCK = os.path.join(ROOT, "tests", "mock_server.py")
FAKE_LMS = os.path.join(ROOT, "tests", "fake_lms.py")
AGENT = os.path.join(ROOT, "tests", "scripted_agent.py")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
HIDDEN_COUNTS = {"toy-bepay": 43, "cron-next": 100, "wal-kv": 57}

EXAMPLE_BANNER = """<!--
  SYNTHETIC EXAMPLE. Every model here is a mock, and every number is meaningless.
  This shows the format of a report only. It says nothing about any real model.
  Regenerate: RELAYBENCH_WRITE_EXAMPLE=examples/mock-run-report.md python -m pytest tests -k full_run
-->

"""


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(url, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except urllib.error.HTTPError:
            return
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError("nothing answered at %s" % url)


class Stack:
    """A mock server, a config and a proxy in a temp folder. Nothing touches the repo's folders."""

    def __init__(self, tmp, models, mock_args=(), env=None):
        self.tmp = str(tmp)
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.mock_port = free_port()
        self.proxy_port = free_port()
        self.config_path = os.path.join(self.tmp, "pipeline.json")
        self.runs = os.path.join(self.tmp, "runs")
        self.logs = os.path.join(self.tmp, "logs")
        self.env = dict(os.environ, RELAYBENCH_CONFIG=self.config_path, FAKE_LMS_STATE=self.state,
                        PYTHONIOENCODING="utf-8")
        self.env.update(env or {})
        self.procs = []
        self.proxy = None
        output = open(os.path.join(self.state, "mock.log"), "w")
        self.procs.append(subprocess.Popen(
            [sys.executable, MOCK, "--port", str(self.mock_port), "--models", ",".join(models),
             "--state-dir", self.state] + list(mock_args), stdout=output, stderr=subprocess.STDOUT, env=self.env))
        self.mock_output = output

    @property
    def mock_url(self):
        return "http://127.0.0.1:%d" % self.mock_port

    @property
    def agent_url(self):
        return "http://127.0.0.1:%d/v1" % self.proxy_port

    def write_config(self, **overrides):
        cfg = {
            "task": "toy-bepay", "runs_dir": self.runs, "logs_dir": self.logs,
            "results_dir": os.path.join(self.tmp, "results"),
            "backend": {"type": "openai-compatible", "url": self.mock_url},
            "proxy_port": self.proxy_port, "served_model_name": "local-model", "test_timeout_s": 120,
            "planner": {"label": "mock-planner", "model": "mock-planner"},
            "builders": [{"label": "mock-good", "model": "mock-good"}, {"label": "mock-buggy", "model": "mock-buggy"}],
            "reviewers": [{"label": "mock-reviewer", "model": "mock-reviewer"}],
            "judges": [{"label": "judge-human", "type": "human"}],
        }
        cfg.update(overrides)
        self.save_config(cfg)
        return cfg

    def save_config(self, cfg):
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)

    def load_config(self):
        with open(self.config_path, encoding="utf-8") as fh:
            return json.load(fh)

    def start_proxy(self):
        if "--tls-cert" not in self.procs[0].args:
            wait_for_http(self.mock_url + "/v1/models")
        output = open(os.path.join(self.state, "proxy.log"), "w")
        self.proxy = subprocess.Popen([sys.executable, PROXY], stdout=output, stderr=subprocess.STDOUT, env=self.env)
        self.procs.append(self.proxy)
        wait_for_http("http://127.0.0.1:%d/__health" % self.proxy_port)

    def stop(self):
        for proc in self.procs:
            proc.kill()
            proc.wait()

    def cli(self, *args, stdin=None, expect=0, timeout=300):
        result = subprocess.run([sys.executable, PIPELINE] + list(args), cwd=ROOT, env=self.env, input=stdin,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
        output = result.stdout + result.stderr
        if expect is not None:
            assert result.returncode == expect, "exit %s for %s:\n%s" % (result.returncode, args, output)
        return output

    def run_dir(self):
        with open(os.path.join(self.runs, "CURRENT"), encoding="utf-8") as fh:
            return os.path.join(self.runs, fh.read().strip())

    def requests(self):
        path = os.path.join(self.state, "requests.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def proxy_log(self):
        path = os.path.join(self.logs, "proxy-log.jsonl")
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def json_in_run(self, *parts):
        with open(os.path.join(self.run_dir(), *parts), encoding="utf-8") as fh:
            return json.load(fh)

    def text_in_run(self, *parts):
        with open(os.path.join(self.run_dir(), *parts), encoding="utf-8") as fh:
            return fh.read()

    # ---- driving a run the way a person would

    def next_stage(self, starts):
        out = self.cli("next")
        assert "STAGE: " + starts in out, out
        return re.search(r"^Folder: (.+)$", out, re.MULTILINE).group(1)

    def plan(self, task=None):
        self.cli("new-run", *(["--task", task] if task else []))
        run_agent(self.next_stage("plan"), self.agent_url)
        assert "freeze" in self.cli("next")
        self.cli("freeze")

    def build_all(self, extra_text=""):
        for builder in self.load_config()["builders"]:
            run_agent(self.next_stage("build-" + builder["label"]), self.agent_url, extra_text=extra_text)
        assert " test" in self.cli("next")

    def review_all(self):
        cfg = self.load_config()
        key = self.json_in_run("blind-key.json")
        count = len(key["builds"]) * len(cfg["reviewers"])
        for _ in range(count):
            run_agent(self.next_stage("review-"), self.agent_url)
        assert " packet" in self.cli("next")

    def through_tests(self, task=None):
        self.plan(task)
        self.build_all()
        self.cli("test")

    def summary(self):
        return self.json_in_run("tests", "summary.json")

    def load_stage(self):
        with open(os.path.join(self.logs, "current-stage.json"), encoding="utf-8") as fh:
            return json.load(fh).get("stage", "")

    def replace_run_agent(self, command):
        """The agent is part of a run's snapshot, so a test that stops misbehaving has to change it there."""
        run = self.json_in_run("run.json")
        run["agent"]["command"] = command
        with open(os.path.join(self.run_dir(), "run.json"), "w", encoding="utf-8") as fh:
            json.dump(run, fh)

    def leftover_proxies(self):
        """A proxy that a killed `run` could not stop."""
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/__health" % self.proxy_port, timeout=2) as resp:
                return [json.load(resp)["pid"]]
        except OSError:
            return []


@pytest.fixture
def make_stack(tmp_path):
    stacks = []

    def build(models, sub="a", mock_args=(), env=None):
        folder = tmp_path / sub
        folder.mkdir()
        stack = Stack(folder, models, mock_args, env)
        stacks.append(stack)
        return stack

    yield build
    for stack in stacks:
        stack.stop()


def fill_human_scores(path, skip_build=None):
    """Fill in the null template by hand, like a person would."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = json.loads(re.search(r"```json\n(.*?)\n```", text, re.DOTALL).group(1))
    for letter, scores in data["builds"].items():
        if letter != skip_build:
            for criterion in scores:
                scores[criterion] = 4
    for review in data["reviews"].values():
        for field in review:
            review[field] = 2
    data["preferred_build"] = "A"
    data["one_line_verdict"] = "Filled in by a test."
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text[:text.index("```json")] + "```json\n" + json.dumps(data, indent=2) + "\n```\n")


def walk_text(folder):
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            yield name, open(os.path.join(root, name), encoding="utf-8", errors="replace").read()


INIT_ANSWERS = """2
{url}
MOCK_BACKEND_KEY
{proxy}
mock-planner
mock-good,mock-buggy,mock-syntax
mock-rev-a,mock-rev-b
api
judge-api
{judge_url}
mock-judge
MOCK_JUDGE_KEY
paste
judge-paste
human
judge-human

1
{runs}
{task}
"""


@pytest.mark.parametrize("task", ["toy-bepay", "cron-next", "wal-kv"])
def test_full_run(make_stack, task):
    """The whole pipeline, openai-compatible backend behind a path and a key, config written by init."""
    labels = ["mock-planner", "mock-good", "mock-buggy", "mock-syntax", "mock-rev-a", "mock-rev-b"]
    stack = make_stack(labels, mock_args=["--prefix", "/custom", "--require-key", "backend-secret"],
                       env={"MOCK_BACKEND_KEY": "backend-secret", "MOCK_JUDGE_KEY": "judge-secret"})
    time.sleep(0.3)
    base = stack.mock_url + "/custom/v1"
    out = stack.cli("init", stdin=INIT_ANSWERS.format(url=base, proxy=stack.proxy_port, judge_url=base,
                                                      runs=stack.runs, task=task))
    assert "Wrote" in out
    assert "mock-good" in out and "text-embedding-x" not in out

    cfg = stack.load_config()
    assert [b["label"] for b in cfg["builders"]] == ["mock-good", "mock-buggy", "mock-syntax"]
    assert "backend-secret" not in json.dumps(cfg) and "judge-secret" not in json.dumps(cfg)
    cfg["logs_dir"], cfg["results_dir"] = stack.logs, os.path.join(stack.tmp, "results")
    stack.save_config(cfg)
    stack.start_proxy()
    assert "Everything looks good" in stack.cli("check")

    stack.plan()
    stack.build_all()

    # Stage repos must not reach a repo further up, wherever the runs folder is.
    plan_dir = os.path.join(stack.run_dir(), "1-plan")
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=plan_dir, capture_output=True,
                         encoding="utf-8", check=True).stdout.strip()
    assert os.path.realpath(top) == os.path.realpath(plan_dir)

    # Scores stay hidden unless asked for: no model label next to a number.
    out = stack.cli("test")
    assert "Scores stay hidden" in out
    assert not re.search(r"mock-(good|buggy|syntax)\s+\d+ of", out)

    summary = stack.summary()
    expected = HIDDEN_COUNTS[task]
    assert summary["mock-good"]["passed"] == expected and summary["mock-good"]["expected"] == expected
    assert summary["mock-good"]["failing"] == []
    with open(os.path.join(FIXTURES, task, "variants.json"), encoding="utf-8") as fh:
        must_fail = json.load(fh)["buggy"]["must_fail"]
    for name in must_fail:
        assert name in summary["mock-buggy"]["failing"], summary["mock-buggy"]["failing"]
    assert 0 < summary["mock-buggy"]["passed"] < expected
    assert summary["mock-syntax"]["passed"] == 0 and summary["mock-syntax"]["expected"] == expected
    assert not summary["mock-syntax"]["collection_error"]

    stack.cli("blind")
    key = stack.json_in_run("blind-key.json")
    assert sorted(key["builds"]) == ["A", "B", "C"] and key["warnings"] == []
    stack.review_all()
    stack.cli("packet")

    # Nothing a reviewer or judge reads may name a model.
    for name in os.listdir(stack.run_dir()):
        if name.startswith("3-review-"):
            assert not re.search(r"2-build-\D", name)
            for _file, text in walk_text(os.path.join(stack.run_dir(), name)):
                for label in labels:
                    assert label not in text
    packet = stack.text_in_run("4-judging", "judge-packet.md")
    for label in labels:
        assert label not in packet
    assert "# Build A" in packet and "## Review A/reviewer-2" in packet

    # api judge: called directly with its own key, never through the proxy.
    proxy_calls_before = len(stack.proxy_log())
    assert "scores saved" in stack.cli("judge")
    assert len(stack.proxy_log()) == proxy_calls_before
    assert "already scored" in stack.cli("judge")
    assert any(r["model"] == "mock-judge" and r["auth"] == "Bearer judge-secret" for r in stack.requests())

    # human judge: an untouched template doesn't count.
    assert "Have judge-human fill in" in stack.cli("next")
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md"))
    # paste judge: the answer pasted by hand, with a typographic quote mangling the first attempt.
    api_answer = stack.text_in_run("4-judging", "scores-judge-api.md")
    with open(os.path.join(stack.run_dir(), "4-judging", "scores-judge-paste.md"), "a", encoding="utf-8") as fh:
        fh.write(api_answer.replace('"builds"', "\u201cbuilds\u201d"))
    assert "report" in stack.cli("next")
    stack.cli("report")

    # The proxy reached the backend with the real model id, its path and its key.
    model_calls = [r for r in stack.requests() if r["model"] != "mock-judge" and "chat" in r["path"]]
    assert {r["model"] for r in model_calls} == set(labels)
    assert all(r["path"].startswith("/custom/v1/") for r in model_calls)
    assert all(r["auth"] == "Bearer backend-secret" for r in model_calls)
    assert all(r["stream_options"]["include_usage"] for r in model_calls)

    stages = {r["stage"] for r in stack.proxy_log()}
    assert stages == {"plan", "build-mock-good", "build-mock-buggy", "build-mock-syntax",
                      "review-A-by-reviewer-1", "review-B-by-reviewer-1", "review-C-by-reviewer-1",
                      "review-A-by-reviewer-2", "review-B-by-reviewer-2", "review-C-by-reviewer-2"}
    for rec in stack.proxy_log():
        assert rec["input_tokens"] and rec["output_tokens"] and rec["usage_source"] == "server"
        assert rec["temperature"] == 0.2 and rec["max_tokens"] == 16384
        assert rec["integrity_hits"] == []

    report = stack.text_in_run("REPORT.md")
    assert "not controlled by the harness" in report
    assert "`~`" not in report
    assert "%d of %d" % (expected, expected) in report
    assert "0 of %d" % expected in report
    for judge in ("judge-api", "judge-paste", "judge-human"):
        assert "| %s |" % judge in report
    assert "no usable scores" not in report
    assert "None detected" in report
    good_letter = {v: k for k, v in key["builds"].items()}["mock-good"]
    assert "Build %s = mock-good" % good_letter in report
    assert "**judge-api** prefers Build %s (mock-good)" % good_letter in report
    assert stack.tmp not in report and os.path.expanduser("~") not in report
    assert os.path.exists(os.path.join(stack.tmp, "results", os.path.basename(stack.run_dir()) + ".md"))

    # Notes survive a second report.
    edited = report.replace("- What surprised me:", "- What surprised me: nothing")
    with open(os.path.join(stack.run_dir(), "REPORT.md"), "w", encoding="utf-8") as fh:
        fh.write(edited)
    stack.cli("report")
    assert "- What surprised me: nothing" in stack.text_in_run("REPORT.md")

    # Nothing was written into the task folders.
    assert not [n for n in os.listdir(os.path.join(ROOT, "tasks", task, "hidden_tests")) if n.endswith(".wal")]

    example = os.environ.get("RELAYBENCH_WRITE_EXAMPLE")
    if example and task == "toy-bepay":
        with open(os.path.join(ROOT, example), "w", encoding="utf-8") as fh:
            # Whichever machine regenerates this, its hardware has no place in a public example.
            report = re.sub(r"(?m)^- Hardware: .*$", "- Hardware: (left out of this example)",
                            stack.text_in_run("REPORT.md"))
            # It's regenerated before the commit that includes it, and that says nothing about the example.
            fh.write(EXAMPLE_BANNER + report.replace(" (with uncommitted changes)", ""))


@pytest.mark.skipif(os.name == "nt", reason="the fake lms is a shell script")
def test_lmstudio_mode_loads_under_alias(make_stack, tmp_path):
    models = ["pub/mock-planner", "pub/mock-good", "pub/mock-buggy", "pub/mock-reviewer"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lms = bin_dir / "lms"
    lms.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE_LMS))
    lms.chmod(lms.stat().st_mode | stat.S_IEXEC)
    stack = make_stack(models, mock_args=["--lmstudio"],
                       env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "FAKE_LMS_MODELS": ",".join(models)})
    time.sleep(0.3)

    # /v1/models is empty here (Just-in-Time off, nothing loaded), so init must use the native API.
    answers = "1\n%s\n\n65536\nmax\n%d\n1\n2,3\n4\nhuman\n\n\n1\n%s\ntoy-bepay\n" % (stack.mock_url, stack.proxy_port, stack.runs)
    out = stack.cli("init", stdin=answers)
    assert "pub/mock-good" in out and "text-embedding-x" not in out
    cfg = stack.load_config()
    assert cfg["planner"] == {"label": "mock-buggy", "model": "pub/mock-buggy"}  # number 1, sorted
    cfg["planner"] = {"label": "mock-planner", "model": "pub/mock-planner"}
    cfg["builders"] = [{"label": "mock-good", "model": "pub/mock-good"}, {"label": "mock-buggy", "model": "pub/mock-buggy"}]
    cfg["reviewers"] = [{"label": "mock-reviewer", "model": "pub/mock-reviewer"}]
    cfg["logs_dir"], cfg["results_dir"] = stack.logs, os.path.join(stack.tmp, "results")
    stack.save_config(cfg)
    stack.start_proxy()
    out = stack.cli("check")
    assert "LM Studio CLI" in out and "Everything looks good" in out

    stack.cli("load", "mock-good")
    assert "Smoke test passed" in stack.cli("smoke")

    stack.through_tests()
    assert stack.json_in_run("run.json")["model_details"] == {"mock-good": "mlx 4bit"}
    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-human-1.md"))
    stack.cli("report")

    with open(os.path.join(stack.state, "lms-calls.jsonl"), encoding="utf-8") as fh:
        calls = [json.loads(line) for line in fh]
    loads = [c for c in calls if c[0] == "load"]
    assert ["load", "pub/mock-planner", "--identifier", "local-model", "--context-length", "65536", "--gpu", "max"] in loads
    assert {c[1] for c in loads} == set(models)
    assert ["unload", "--all"] in calls
    # The agent's name reaches the server unchanged, and the mock resolves it through the fake load.
    assert {r["model"] for r in stack.requests() if "smoke" not in r["path"]} == {"local-model"}
    report = stack.text_in_run("REPORT.md")
    assert "Context length: 65536 tokens" in report
    assert "4 |" in report and "43 of 43" in report

    # A second stage with the same model doesn't reload it.
    reviewer_loads = [c for c in loads if c[1] == "pub/mock-reviewer"]
    assert len(reviewer_loads) == 1


def test_init_numbers_retry_and_key_protection(make_stack):
    stack = make_stack(["org/model-a", "org/model:b", "other/model-a"])
    time.sleep(0.3)
    answers = "\n".join([
        "2", stack.mock_url + "/v1", "",
        "\u00b2", str(stack.proxy_port),  # isdigit() says yes to this, and int() raises
        "9", "1",          # a number that doesn't exist, then the first model
        "1,2,3",           # builders by number, including a clash on the label
        "",                # no reviewers
        "nonsense",        # a bad judge type
        "api", "j", stack.mock_url + "/v1", "m",
        "sk-this-is-a-real-looking-key!", "JUDGE_KEY",  # a pasted key is refused
        "",
        "1",               # no agent
        stack.runs, "cron-next", ""])
    out = stack.cli("init", stdin=answers)
    assert "There is no model number 9" in out
    assert "not the key itself" in out
    cfg = stack.load_config()
    assert "sk-this" not in json.dumps(cfg)
    assert cfg["reviewers"] == []
    assert cfg["task"] == "cron-next"
    labels = [b["label"] for b in cfg["builders"]]
    assert len(set(labels)) == 3 and all(re.match(r"^[a-z0-9][a-z0-9._-]*$", label) for label in labels)
    # Existing config: answering no changes nothing.
    before = open(stack.config_path).read()
    assert "Nothing changed" in stack.cli("init", stdin="n\n")
    assert open(stack.config_path).read() == before


def test_init_with_server_down_accepts_typed_ids(make_stack):
    stack = make_stack(["x"])
    stack.procs[0].kill()
    stack.procs[0].wait()
    answers = "\n".join(["2", stack.mock_url + "/v1", "", str(stack.proxy_port), "n", "p/planner", "p/builder", "", "human", "", "", "1", stack.runs, "", ""])
    out = stack.cli("init", stdin=answers)
    assert "No models found" in out
    assert stack.load_config()["planner"]["model"] == "p/planner"


def init_answers(stack, *agent_answers):
    return "\n".join(["2", stack.mock_url + "/v1", "", str(stack.proxy_port), "mock-planner", "mock-good,mock-buggy",
                      "", "human", "", ""] + list(agent_answers) + [stack.runs, "", ""]) + "\n"


AGENT_MESSAGE = "Follow the instructions in PROMPT.md"


def test_init_sets_up_cline_without_editing_the_config(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy"])
    wait_for_http(stack.mock_url + "/v1/models")
    out = stack.cli("init", stdin=init_answers(stack, "2"))
    assert stack.load_config()["agent"] == {"command": ["cline", "-y", AGENT_MESSAGE],
                                            "stage_timeout_s": 3600, "idle_timeout_s": 600}
    assert ("cline auth --provider openai --apikey dummy --baseurl http://127.0.0.1:%d/v1 --modelid local-model"
            % stack.proxy_port) in out
    assert "pipeline.py run" in out


def test_init_again_keeps_the_agent_unless_told_otherwise(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy"])
    wait_for_http(stack.mock_url + "/v1/models")
    stack.cli("init", stdin=init_answers(stack, "2"))
    cfg = stack.load_config()
    cfg["agent"]["stage_timeout_s"] = 7200  # a setting the person changed by hand must survive
    stack.save_config(cfg)

    out = stack.cli("init", "--force", stdin=init_answers(stack, ""))
    assert "Keep the current one: cline -y 'Follow the instructions in PROMPT.md'" in out
    assert stack.load_config()["agent"] == cfg["agent"]

    stack.cli("init", "--force", stdin=init_answers(stack, "1"))
    assert "agent" not in stack.load_config()
    assert "Keep the current one" not in stack.cli("init", "--force", stdin=init_answers(stack, "1"))


def test_init_another_agent_and_bad_answers(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy"])
    wait_for_http(stack.mock_url + "/v1/models")
    with open(stack.config_path, "w", encoding="utf-8") as fh:
        fh.write("{ this isn't JSON")  # a broken config must not stop you from writing a new one
    out = stack.cli("init", "--force", stdin=init_answers(
        stack, "9", "4", "3", "", "3", "bad 'quote", "3", 'no-such-agent-here --flag "a b"'))
    assert out.count("Please type a number from the list") == 2, "4 only exists when there is an agent to keep"
    assert "Please type a command" in out and "can't be read" in out
    assert "'no-such-agent-here' was not found" in out
    assert "Point your agent at the proxy" in out
    assert stack.load_config()["agent"]["command"] == ["no-such-agent-here", "--flag", "a b", AGENT_MESSAGE]


def test_init_then_run_needs_no_editing(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-judge"])
    wait_for_http(stack.mock_url + "/v1/models")
    stack.cli("init", stdin=init_answers(stack, "3", shlex.join([sys.executable, AGENT])))
    cfg = stack.load_config()
    cfg["logs_dir"], cfg["results_dir"] = stack.logs, os.path.join(stack.tmp, "results")  # keeps the repo clean
    stack.save_config(cfg)
    stack.cli("run", "--allow-sleep")
    assert stage_result(stack, "build-mock-good")["status"] == "done"
    assert "'%s'`" % AGENT_MESSAGE in stack.text_in_run("REPORT.md")


def test_init_input_ends_cleanly(make_stack):
    stack = make_stack(["x"])
    assert "input ended" in stack.cli("init", stdin="2\n", expect=1)
    assert not os.path.exists(stack.config_path)


def test_missing_config_and_bad_config(make_stack):
    stack = make_stack(["x"])
    assert "init" in stack.cli("status", expect=1)
    stack.write_config(builders=[{"label": "Has Space", "model": "x"}])
    assert "not valid" in stack.cli("status", expect=1)
    stack.write_config(builders=[{"label": "same", "model": "x"}, {"label": "same", "model": "x"}])
    assert "only once" in stack.cli("status", expect=1)
    stack.write_config(judges=[{"label": "j", "type": "api"}])
    assert "base_url" in stack.cli("status", expect=1)
    with open(stack.config_path, "w") as fh:
        fh.write("{ not json")
    assert "not valid JSON" in stack.cli("status", expect=1)


def test_check_fails_when_things_are_down(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    out = stack.cli("check", expect=1)
    assert "Logging proxy is not running" in out
    stack.write_config(planner={"label": "planner", "model": "PASTE-MODEL-KEY-HERE"})
    assert "still missing" in stack.cli("check", expect=1)
    assert "Fill in the model keys" in stack.cli("new-run", expect=1)


def test_many_builders_no_reviewers(make_stack):
    """Past the old limit of 10 builders, and past Z. Zero reviewers is a valid setup."""
    names = ["mock-b%02d" % i for i in range(27)]
    stack = make_stack(["mock-planner"] + names)
    stack.write_config(builders=[{"label": n, "model": n} for n in names], reviewers=[])
    stack.start_proxy()
    stack.through_tests()
    out = stack.cli("blind")
    assert "Build Z" in out and "Build AA" in out
    assert " packet" in stack.cli("next")
    stack.cli("packet")
    packet = stack.text_in_run("4-judging", "judge-packet.md")
    assert packet.index("# Build Z") < packet.index("# Build AA")
    score = os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md")
    fill_human_scores(score)
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    assert "- Build AA = " in report and "| AA | " in report


def test_build_looping_in_one_call_fails_one_test(make_stack):
    stack = make_stack(["mock-planner", "mock-loop", "mock-reviewer"])
    stack.write_config(task="cron-next", builders=[{"label": "mock-loop", "model": "mock-loop"}])
    stack.start_proxy()
    out = stack.cli("new-run")
    assert "nothing to compare" in out
    run_agent(stack.next_stage("plan"), stack.agent_url)
    stack.cli("freeze")
    run_agent(stack.next_stage("build-mock-loop"), stack.agent_url)
    stack.cli("test")
    res = stack.summary()["mock-loop"]
    assert not res["timeout"]
    assert res["ran"] == HIDDEN_COUNTS["cron-next"]
    assert res["failing"] == ["test_hidden.test_impossible_date_returns_none[0 0 31 2 *]",
                              "test_hidden.test_impossible_date_returns_none[0 0 30 2 *]"]


@pytest.mark.parametrize("task", ["toy-bepay", "cron-next"])
def test_isdigit_bug_is_caught(make_stack, task):
    """str.isdigit() accepts characters int() can't read. A real model's build shipped this bug,
    and passed every hidden test until these were added. Only judges reading the code caught it."""
    stack = make_stack(["mock-planner", "mock-good", "mock-isdigit", "mock-reviewer"])
    stack.write_config(task=task, builders=[{"label": "mock-good", "model": "mock-good"},
                                            {"label": "mock-isdigit", "model": "mock-isdigit"}])
    stack.start_proxy()
    stack.through_tests()
    summary = stack.summary()
    assert summary["mock-good"]["passed"] == HIDDEN_COUNTS[task]
    with open(os.path.join(FIXTURES, task, "variants.json"), encoding="utf-8") as fh:
        must_fail = json.load(fh)["isdigit"]["must_fail"]
    assert sorted(summary["mock-isdigit"]["failing"]) == sorted(must_fail)


def test_non_ascii_digits_in_the_delimited_format_are_caught(make_stack):
    """int() reads these digits, so a `\\d` regex accepts them. Plain-format tests alone can't see that."""
    stack = make_stack(["mock-planner", "mock-good", "mock-udigits", "mock-reviewer"])
    stack.write_config(builders=[{"label": "mock-good", "model": "mock-good"},
                                 {"label": "mock-udigits", "model": "mock-udigits"}])
    stack.start_proxy()
    stack.through_tests()
    summary = stack.summary()
    assert summary["mock-good"]["passed"] == HIDDEN_COUNTS["toy-bepay"]
    with open(os.path.join(FIXTURES, "toy-bepay", "variants.json"), encoding="utf-8") as fh:
        must_fail = json.load(fh)["udigits"]["must_fail"]
    assert sorted(summary["mock-udigits"]["failing"]) == sorted(must_fail)


def test_packet_numbers_lines_and_keeps_reviews_after_the_builds(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    stack.plan()
    stack.build_all()
    with open(os.path.join(stack.run_dir(), "2-build-1", "leading_blank.py"), "w", encoding="utf-8") as fh:
        fh.write("\n\nlate_line = 1\n")
    stack.cli("test")
    stack.cli("blind")
    stack.review_all()
    # Moving reviews must not stop the label search from covering them, or from naming the right build.
    with open(os.path.join(stack.run_dir(), "3-review-B-by-reviewer-1", "REVIEW.md"), "a", encoding="utf-8") as fh:
        fh.write("\nThis looks like the work of mock-good.\n")
    out = stack.cli("packet")
    assert "sections of Build B mention a model label" in out
    packet = stack.text_in_run("4-judging", "judge-packet.md")
    assert re.search(r"^ *1 \| import re$", packet, re.MULTILINE)
    assert re.search(r"^3 \| late_line = 1$", packet, re.MULTILINE), "numbers must match the file, blank lines included"
    last_build = max(m.start() for m in re.finditer(r"^# Build ", packet, re.MULTILINE))
    reviews = packet.index("\n# Reviews\n")
    assert last_build < reviews < packet.index("## Review A/reviewer-1")
    assert packet.count("## Review ") == 2


def test_own_tests_are_run_and_shown(make_stack):
    labels = ["mock-good-mixed", "mock-good-none", "mock-good-hang", "mock-good-broken"]
    stack = make_stack(["mock-planner", "mock-reviewer"] + labels)
    stack.write_config(builders=[{"label": l, "model": l} for l in labels], test_timeout_s=30)
    stack.start_proxy()
    stack.plan()
    stack.build_all()
    tests = {"2-build-1": "def test_ok():\n    open('left_behind.txt', 'w').close()\n\ndef test_bad():\n    assert False\n",
             "2-build-3": "import time\n\ndef test_hangs():\n    time.sleep(600)\n",
             "2-build-4": "import no_such_module_here\n\ndef test_x():\n    pass\n"}
    for folder, source in tests.items():
        with open(os.path.join(stack.run_dir(), folder, "test_own.py"), "w", encoding="utf-8") as fh:
            fh.write(source)
    # The layout real builds use: a tests folder that imports the package from the project's root.
    os.makedirs(os.path.join(stack.run_dir(), "2-build-1", "tests"))
    with open(os.path.join(stack.run_dir(), "2-build-1", "tests", "test_package.py"), "w", encoding="utf-8") as fh:
        fh.write("import unittest\nimport bepay\n\n\nclass Own(unittest.TestCase):\n    def test_it(self):\n"
                 "        self.assertTrue(bepay.is_valid_ogm('+++123/4567/89002+++'))\n")
    stack.cli("test")
    summary = stack.summary()
    for label in labels:
        assert summary[label]["passed"] == HIDDEN_COUNTS["toy-bepay"], "own tests must not affect hidden results"
    assert summary["mock-good-mixed"]["own_tests"]["passed"] == 2 and summary["mock-good-mixed"]["own_tests"]["ran"] == 3
    assert summary["mock-good-none"]["own_tests"]["ran"] == 0
    assert summary["mock-good-hang"]["own_tests"]["timeout"] is True
    assert summary["mock-good-broken"]["own_tests"]["collection_error"] is True
    leftovers = os.listdir(os.path.join(stack.run_dir(), "2-build-1"))
    assert "left_behind.txt" not in leftovers, "they must run on a copy"
    assert ".pytest_cache" not in leftovers and "__pycache__" not in leftovers

    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    packet = stack.text_in_run("4-judging", "judge-packet.md")
    for text in ("2 of 3 passed", "none found", "timed out", "could not be collected"):
        assert "own tests: **%s**." % text in packet, text
    assert "`test_own.test_bad`" in packet
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md"))
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    assert "| Own tests |" in report and re.search(r"\| mock-good-mixed \| 43 of 43 \| 2 of 3 passed \|", report)


def test_request_overrides_pin_settings_for_every_model_call(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config(request_overrides={"max_tokens": 1234, "temperature": 0.5})
    stack.start_proxy()
    stack.plan()
    # The run's snapshot decides, as with every other setting.
    cfg = stack.load_config()
    del cfg["request_overrides"]
    stack.save_config(cfg)
    stack.build_all()
    calls = [r for r in stack.requests() if "chat" in r["path"]]
    assert calls and all(r["max_tokens"] == 1234 and r["temperature"] == 0.5 for r in calls)
    stack.cli("test")
    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md"))
    stack.cli("report")
    assert "| 0.5 (pinned) | 1234 (pinned) |" in stack.text_in_run("REPORT.md")


def test_without_overrides_requests_are_unchanged_and_bad_ones_are_refused(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    stack.plan()
    calls = [r for r in stack.requests() if "chat" in r["path"]]
    assert calls and all(r["max_tokens"] == 16384 and r["temperature"] == 0.2 for r in calls)  # what the agent sent
    for bad, word in (({"seed": 1}, "only hold"), ({"max_tokens": 0}, "max_tokens"), ({"max_tokens": 1.5}, "max_tokens"),
                      ({"temperature": "hot"}, "temperature"), ({"temperature": -1}, "temperature"), ("x", "only hold")):
        stack.write_config(request_overrides=bad)
        assert word in stack.cli("status", expect=1), bad


def test_build_hanging_on_import_times_out_without_stalling(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-importhang", "mock-reviewer"])
    stack.write_config(test_timeout_s=6, builders=[{"label": "mock-good", "model": "mock-good"},
                                                   {"label": "mock-importhang", "model": "mock-importhang"}])
    stack.start_proxy()
    stack.plan()
    stack.build_all()
    started = time.time()
    out = stack.cli("test")
    assert time.time() - started < 40
    assert "timed out or lost tests" in out
    res = stack.summary()["mock-importhang"]
    assert res["timeout"] and res["passed"] == 0 and res["expected"] == 43
    assert stack.summary()["mock-good"]["passed"] == 43
    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    assert "0 of 43 (timeout" in stack.text_in_run("4-judging", "judge-packet.md")


def test_collection_error_is_reported_not_counted(make_stack, tmp_path):
    tasks = tmp_path / "my-tasks"
    shutil.copytree(os.path.join(ROOT, "tasks", "toy-bepay"), str(tasks / "unguarded"))
    with open(str(tasks / "unguarded" / "hidden_tests" / "test_hidden.py"), "w") as fh:
        fh.write("import bepay_is_not_there\n\ndef test_x():\n    pass\n")
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config(task="unguarded", tasks_dir=str(tasks))
    stack.start_proxy()
    stack.plan()
    stack.build_all()
    out = stack.cli("test", "--show")
    assert "collection error" in out
    res = stack.summary()["mock-good"]
    assert res["collection_error"] and res["ran"] == 0 and res["passed"] == 0


def test_tests_run_on_copies_and_leave_no_files(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config(task="wal-kv")
    stack.start_proxy()
    hidden = os.path.join(ROOT, "tasks", "wal-kv", "hidden_tests")
    before = sorted(os.listdir(hidden))
    stack.through_tests()
    assert sorted(os.listdir(hidden)) == before
    build = os.path.join(stack.run_dir(), "2-build-1")
    assert subprocess.run(["git", "status", "--porcelain"], cwd=build, capture_output=True,
                          encoding="utf-8", check=True).stdout.strip() == ""


def test_planner_sees_starter_and_judges_see_it_once(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config(task="wal-kv")
    stack.start_proxy()
    stack.through_tests()
    assert os.path.exists(os.path.join(stack.run_dir(), "1-plan", "starter", "walkv", "store.py"))
    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    packet = stack.text_in_run("4-judging", "judge-packet.md")
    assert "# Starter code" in packet
    assert "return self._data.get(key) or default" in packet
    assert "Unchanged from the starter code: `tests/__init__.py`, `tests/test_store.py`" in packet


def test_force_sets_old_outputs_aside(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    stack.through_tests()
    stack.cli("blind")
    assert "--force" in stack.cli("blind", expect=1)
    stack.review_all()
    stack.cli("packet")
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md"))
    stack.cli("report")

    # New letters: old reviews and scores must not carry over onto them.
    stack.cli("blind", "--force")
    names = os.listdir(stack.run_dir())
    assert not os.path.exists(os.path.join(stack.run_dir(), "4-judging"))
    assert not os.path.exists(os.path.join(stack.run_dir(), "REPORT.md"))
    stale = [n for n in names if n.startswith("_stale-")]
    assert len(stale) == 1
    assert os.path.exists(os.path.join(stack.run_dir(), stale[0], "4-judging", "scores-judge-human.md"))
    assert "STAGE: review-A" in stack.cli("next")
    stack.cli("status")

    # A new freeze invalidates everything after the plan, including the read-only Git objects' folders.
    assert "--force" in stack.cli("freeze", expect=1)
    stack.cli("freeze", "--force")
    assert "STAGE: build-mock-good" in stack.cli("next")
    assert not os.path.exists(os.path.join(stack.run_dir(), "tests"))
    assert not os.path.exists(os.path.join(stack.run_dir(), "blind-key.json"))


def test_run_keeps_using_its_snapshot(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer", "mock-other"])
    cfg = stack.write_config()
    stack.start_proxy()
    stack.plan()
    cfg["builders"][0]["model"] = "mock-other"
    stack.save_config(cfg)
    folder = stack.next_stage("build-mock-good")
    run_agent(folder, stack.agent_url)
    assert "mock-other" not in {r["model"] for r in stack.requests()}


def test_scores_validation(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config(judges=[{"label": "judge-a", "type": "human"}, {"label": "judge-b", "type": "human"},
                               {"label": "judge-c", "type": "paste"}])
    stack.start_proxy()
    stack.through_tests()
    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    judging = os.path.join(stack.run_dir(), "4-judging")

    # Untouched template: not scored.
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    assert "`judge-a`: no usable scores yet" in report
    assert "Judge scores complete (0 of 3)" in stack.cli("status")

    # judge-a skips Build B, judge-b is complete with out-of-range values for nothing, judge-c gives junk.
    fill_human_scores(os.path.join(judging, "scores-judge-a.md"), skip_build="B")
    fill_human_scores(os.path.join(judging, "scores-judge-b.md"))
    with open(os.path.join(judging, "scores-judge-c.md"), "a", encoding="utf-8") as fh:
        fh.write('{"builds": {"A": {"correctness": 99, "plan_adherence": 3, "code_quality": 3, "edge_cases": 3, "own_tests": 3}}}\n')
    with open(os.path.join(judging, "scores-stray.md"), "w", encoding="utf-8") as fh:
        fh.write('{"builds": {"A": {"correctness": 5, "plan_adherence": 5, "code_quality": 5, "edge_cases": 5, "own_tests": 5}}}\n')
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    assert "`judge-a`: Build B incomplete" in report
    assert "`judge-c`: no usable scores yet" in report
    assert "stray" not in report
    # All criteria 4 is 20. An average of 20 for B proves judge-a's gap wasn't counted as 0.
    averages = re.findall(r"\*\*average\*\* \| \| \| \| \| \| \*\*([\d.]+)\*\*", report)
    assert averages == ["20.0", "20.0"]
    assert "Judge scores complete (1 of 3)" in stack.cli("status")


def test_integrity_flags(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    stack.plan()
    # The second builder's transcript contains a hidden test path, and it edits the frozen plan.
    run_agent(stack.next_stage("build-mock-good"), stack.agent_url)
    folder = stack.next_stage("build-mock-buggy")
    run_agent(folder, stack.agent_url, extra_text="cat ../../tasks/toy-bepay/hidden_tests/test_hidden.py")
    with open(os.path.join(folder, "plan.md"), "a", encoding="utf-8") as fh:
        fh.write("\nThe plan, adjusted.\n")
    with open(os.path.join(folder, "bepay", "extra.py"), "w", encoding="utf-8") as fh:
        fh.write("# Written by mock-buggy\n")
    os.makedirs(os.path.join(folder, ".aider.tags.cache.v3"))
    with open(os.path.join(folder, ".aider.chat.history.md"), "w", encoding="utf-8") as fh:
        fh.write("model: mock-buggy\n")
    subprocess.run(["git", "add", "-A"], cwd=folder, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "more"], cwd=folder, check=True)
    stack.cli("test")
    out = stack.cli("blind")
    key = stack.json_in_run("blind-key.json")
    letter = {v: k for k, v in key["builds"].items()}["mock-buggy"]
    assert "Build %s changed plan.md" % letter in out
    assert "Build %s: `bepay/extra.py` mentions a model label" % letter in out
    assert "mock-buggy" not in out.replace("Scores", "")

    # Reviewers get the frozen plan, and no agent droppings.
    review = os.path.join(stack.run_dir(), "3-review-%s-by-reviewer-1" % letter)
    assert "adjusted" not in open(os.path.join(review, "plan.md"), encoding="utf-8").read()
    assert not os.path.exists(os.path.join(review, ".aider.chat.history.md"))

    stack.review_all()
    out = stack.cli("packet")
    assert "mention a model label" in out
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md"))
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    flags = report.split("## Integrity flags")[1].split("## Blind key")[0]
    assert "`hidden_tests`" in flags
    assert "changed plan.md" in flags
    assert "mentions a model label" in flags


def test_usage_estimates_and_reasoning_are_marked(make_stack):
    stack = make_stack(["mock-planner", "mock-think-nousage", "mock-buggy", "mock-reviewer"])
    stack.write_config(builders=[{"label": "mock-x", "model": "mock-think-nousage"},
                                 {"label": "mock-buggy", "model": "mock-buggy"}])
    stack.start_proxy()
    stack.through_tests()
    records = [r for r in stack.proxy_log() if r["stage"] == "build-mock-x"]
    assert records and all(r["usage_source"] == "chunks" for r in records)
    assert records[0]["ttft_s"] is not None
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    assert "`~` = the server sent no token counts" in report


def test_aborted_stream_still_logs_stats(make_stack):
    stack = make_stack(["mock-planner", "mock-slow", "mock-good", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    body = json.dumps({"model": "mock-slow", "stream": True, "messages": [
        {"role": "user", "content": "Reply with exactly these two words: pipeline OK"}]}).encode()
    sock = socket.create_connection(("127.0.0.1", stack.proxy_port))
    sock.sendall(b"POST /run/abort/test/v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                 b"Content-Length: %d\r\n\r\n" % len(body) + body)
    received = b""
    while received.count(b"data:") < 5:
        received += sock.recv(4096)
    sock.close()
    deadline = time.time() + 15
    while time.time() < deadline:
        path = os.path.join(stack.logs, "proxy-log.jsonl")
        if os.path.exists(path):
            break
        time.sleep(0.1)
    record = stack.proxy_log()[0]
    assert record["aborted"] is True
    assert record["output_tokens"] and record["ttft_s"] is not None


def test_smoke_rewrite_and_synthetic_model_list(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    stack.cli("load", "mock-buggy")
    assert "Smoke test passed" in stack.cli("smoke")
    assert "Model replied: pipeline OK" in stack.cli("smoke", "mock-good")
    listed = json.loads(urllib.request.urlopen(stack.agent_url + "/models").read())
    assert [m["id"] for m in listed["data"]] == ["local-model"]
    run_agent(stack.tmp, stack.agent_url)  # the alias reaches the mock as the real id
    assert stack.requests()[-1]["model"] == "mock-buggy"


def make_certificate(tmp_path):
    cert, key = str(tmp_path / "cert.pem"), str(tmp_path / "key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                    "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    return cert, key


def wait_for_port(port, seconds=15):
    def is_open():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            return False
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_open():
            return
        time.sleep(0.05)
    raise AssertionError("nothing listening on port %d" % port)


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl to make a test certificate")
def test_untrusted_certificate_is_not_called_unreachable(make_stack, tmp_path):
    cert, key = make_certificate(tmp_path)
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"],
                       mock_args=["--tls-cert", cert, "--tls-key", key])
    stack.env.pop("SSL_CERT_FILE", None)
    wait_for_port(stack.mock_port)
    stack.write_config(backend={"type": "openai-compatible", "url": "https://127.0.0.1:%d/v1" % stack.mock_port})
    out = stack.cli("load", "mock-good", expect=1)
    assert "certificate" in out and "not reachable" not in out


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl to make a test certificate")
@pytest.mark.skipif(ssl.OPENSSL_VERSION.startswith("LibreSSL"),
                    reason="LibreSSL ignores SSL_CERT_FILE, which this test needs to trust its certificate")
def test_https_backend(make_stack, tmp_path):
    cert, key = make_certificate(tmp_path)
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"],
                       mock_args=["--tls-cert", cert, "--tls-key", key], env={"SSL_CERT_FILE": cert})
    wait_for_port(stack.mock_port)
    stack.write_config(backend={"type": "openai-compatible", "url": "https://127.0.0.1:%d/v1" % stack.mock_port})
    stack.start_proxy()
    stack.cli("load", "mock-good")
    assert "Model replied: pipeline OK" in stack.cli("smoke")


def test_missing_pytest_is_a_clear_error(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    stack.write_config()
    stack.start_proxy()
    stack.plan()
    stack.build_all()
    script = ("import importlib.util as u, runpy, sys\n"
              "u.find_spec = lambda name: None\n"
              "sys.argv = ['pipeline.py', 'test']\n"
              "runpy.run_path(%r, run_name='__main__')\n" % PIPELINE)
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=stack.env, capture_output=True, encoding="utf-8")
    assert result.returncode == 1
    assert "pytest is not installed" in result.stdout
    assert not os.path.exists(os.path.join(stack.run_dir(), "tests", "summary.json"))


# ----------------------------------------------------------------- unattended runs

API_JUDGE = {"label": "judge-api", "type": "api", "model": "mock-judge", "api_key_env": "MOCK_JUDGE_KEY"}
posix_only = pytest.mark.skipif(os.name == "nt", reason="uses POSIX signals and process groups")


def unattended(make_stack, *misbehave, models=("mock-planner", "mock-good", "mock-buggy", "mock-reviewer"),
               agent_args=(), sub="a", **overrides):
    """A config for `run`, with no proxy started: `run` has to do that itself."""
    limits = {k: overrides.pop(k) for k in ("stage_timeout_s", "idle_timeout_s") if k in overrides}
    stack = make_stack(list(models) + ["mock-judge"], sub=sub, env={"MOCK_JUDGE_KEY": "judge-secret"})
    wait_for_http(stack.mock_url + "/v1/models")
    command = [sys.executable, AGENT] + list(agent_args)
    for item in misbehave:
        command += ["--misbehave", item]
    settings = {"judges": [dict(API_JUDGE, base_url=stack.mock_url + "/v1")],
                "agent": dict({"command": command}, **limits)}
    settings.update(overrides)
    stack.write_config(**settings)
    return stack


def stage_result(stack, name):
    return stack.json_in_run("stage-results.json")[name]


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A killed child stays listed as a zombie until it is reaped.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, encoding="utf-8").stdout
    return bool(state.strip()) and not state.strip().startswith("Z")


def wait_until(check, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def start_run_in_background(stack, *args):
    output = open(os.path.join(stack.state, "run-output.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, PIPELINE, "run"] + list(args), cwd=ROOT, env=stack.env,
                            stdout=output, stderr=subprocess.STDOUT)
    stack.procs.append(proc)
    assert wait_until(lambda: os.path.exists(os.path.join(stack.runs, "agent.pid")))
    return proc


def proxy_is_up(stack):
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/__health" % stack.proxy_port, timeout=1).read()
        return True
    except OSError:
        return False


def test_run_goes_from_nothing_to_a_report(make_stack):
    # No --base-url: the agent finds the proxy through the environment variables `run` sets.
    stack = unattended(make_stack)
    stack.env["OPENAI_API_KEY"] = "a-real-key-from-the-environment"
    out = stack.cli("run", "--allow-sleep")
    assert not proxy_is_up(stack), "the proxy this run started must stop with it"
    assert stack.requests() and not any("a-real-key" in (r.get("auth") or "") for r in stack.requests())
    assert not os.path.exists(os.path.join(stack.runs, "run.pid"))

    report = stack.text_in_run("REPORT.md")
    assert "Mode: unattended, with `%s`" % shlex.join([sys.executable, AGENT]) in report
    assert "- Hardware: " in report and "- Quantisation: not reported" in report
    assert "- Agent version: Python " in report  # this agent's command starts with the Python it runs in
    assert "- Limits: stage 3600 s, idle 600 s, hidden tests 120 s" in report
    assert "## Unattended stages" in report and "Canned verdict" in report
    summary = stack.summary()
    assert summary["mock-good"]["passed"] == HIDDEN_COUNTS["toy-bepay"]
    assert 0 < summary["mock-buggy"]["passed"] < HIDDEN_COUNTS["toy-bepay"]
    assert "Scores stay hidden" in out and not re.search(r"mock-(good|buggy)\s+\d+ of", out)
    for stage in ("plan", "build-mock-good", "build-mock-buggy"):
        assert stage_result(stack, stage)["status"] == "done"
        assert stage_result(stack, stage)["model_calls"] == 2
    assert os.path.exists(os.path.join(stack.run_dir(), "agent-logs", "plan.log"))
    assert stack.json_in_run("run.json")["agent"]["command"][1] == AGENT
    # Each model answered its own stage.
    assert {"mock-planner", "mock-good", "mock-buggy", "mock-reviewer"} <= {r["model"] for r in stack.requests()}
    # A finished run isn't resumed: the next `run` is a new one.
    first = stack.run_dir()
    stack.cli("run", "--allow-sleep")
    assert stack.run_dir() != first


def headings(report):
    return re.findall(r"^#+ .*$", report, re.MULTILINE)


def test_run_report_has_the_manual_format_plus_its_own_section(make_stack):
    auto = unattended(make_stack, sub="auto")
    auto.cli("run", "--allow-sleep")
    manual = unattended(make_stack, sub="manual")
    manual.start_proxy()
    manual.plan()
    manual.build_all()
    manual.cli("test")
    manual.cli("blind")
    manual.review_all()
    manual.cli("packet")
    manual.cli("judge")
    manual.cli("report")
    manual_report, auto_report = manual.text_in_run("REPORT.md"), auto.text_in_run("REPORT.md")
    assert "- Mode: manual" in manual_report and "## Unattended stages" not in manual_report
    assert "your approvals" in manual_report and "your approvals" not in auto_report
    assert [h for h in headings(auto_report) if h != "## Unattended stages"][1:] == headings(manual_report)[1:]
    table_headers = re.compile(r"^(\|.*\|)\n\|---", re.MULTILINE)
    added = "| Stage | Outcome | Model calls | Time | Notes |"
    assert [h for h in table_headers.findall(auto_report) if h != added] == table_headers.findall(manual_report)
    assert added in table_headers.findall(auto_report)
    for label in ("mock-good", "mock-buggy"):
        assert auto.summary()[label]["passed"] == manual.summary()[label]["passed"]
        assert auto.summary()[label]["failing"] == manual.summary()[label]["failing"]


def test_run_placeholders_and_existing_proxy(make_stack):
    stack = unattended(make_stack, "no such folder:hang", agent_args=["--base-url", "{base_url}", "--model", "{model}"],
                       reviewers=[], judges=[])
    stack.start_proxy()
    stack.cli("run", "--allow-sleep")
    assert stack.proxy.poll() is None and proxy_is_up(stack), "a proxy you started is yours to stop"
    assert stack.summary()["mock-good"]["passed"] == HIDDEN_COUNTS["toy-bepay"]
    report = stack.text_in_run("REPORT.md")
    assert "No judges configured" in report
    assert "--misbehave 'no such folder:hang'`" in report, "the report's command must keep its quoting"


def test_run_stops_at_judges_that_need_a_person(make_stack):
    stack = unattended(make_stack, judges=[{"label": "judge-human", "type": "human"}])
    out = stack.cli("run", "--allow-sleep")
    assert "Still open" in out and "judge-human" in out
    assert "no usable scores yet" in stack.text_in_run("REPORT.md")
    fill_human_scores(os.path.join(stack.run_dir(), "4-judging", "scores-judge-human.md"))
    stack.cli("report")
    assert "Filled in by a test." in stack.text_in_run("REPORT.md")


def test_run_api_judge_failure_keeps_the_work(make_stack):
    stack = unattended(make_stack)
    cfg = stack.load_config()
    cfg["judges"][0]["base_url"] = "http://127.0.0.1:%d/v1" % free_port()
    stack.save_config(cfg)
    out = stack.cli("run", "--allow-sleep", expect=1)
    assert "no usable answer" in out and "Still open" in out
    assert os.path.exists(os.path.join(stack.run_dir(), "REPORT.md"))


@posix_only
def test_run_records_each_kind_of_stage_failure(make_stack):
    """Bad stages are recorded, and the rest of the run carries on."""
    labels = ["mock-good", "mock-no-commit", "mock-exit", "mock-nothing", "mock-other-model", "mock-leaves-child"]
    stack = unattended(make_stack, "2-build-2:no-commit", "2-build-3:exit-1", "2-build-4:nothing",
                       "2-build-5:no-calls", "2-build-6:leave-child", "3-review-:no-calls",
                       models=["mock-planner", "mock-reviewer"] + labels,
                       builders=[{"label": l, "model": l} for l in labels])
    child_file = os.path.join(stack.state, "child.pid")
    stack.env["SCRIPTED_AGENT_CHILD"] = child_file
    out = stack.cli("run", "--allow-sleep")
    full = HIDDEN_COUNTS["toy-bepay"]

    result = stage_result(stack, "build-mock-no-commit")
    assert result["status"] == "done" and any("didn't commit" in f for f in result["flags"])
    assert stack.summary()["mock-no-commit"]["passed"] == full

    result = stage_result(stack, "build-mock-exit")
    assert result["status"] == "done" and "agent exited with code 1" in result["flags"]

    result = stage_result(stack, "build-mock-nothing")
    assert result["status"] == "failed" and "changed nothing" in result["reason"]
    assert stack.summary()["mock-nothing"]["no_build"] and stack.summary()["mock-nothing"]["passed"] == 0

    # Work that didn't come through the proxy must never count as the model's.
    result = stage_result(stack, "build-mock-other-model")
    assert result["status"] == "failed" and "no model calls" in result["reason"]
    assert stack.summary()["mock-other-model"]["no_build"]

    assert not alive(child_pid(child_file)), "a process the build left behind would slow the next model"
    assert stage_result(stack, "build-mock-leaves-child")["status"] == "done"

    # Failed builds aren't anonymised, reviewed or judged, and their reviews weren't accepted either.
    key = stack.json_in_run("blind-key.json")
    assert sorted(key["builds"].values()) == ["mock-exit", "mock-good", "mock-leaves-child", "mock-no-commit"]
    reviews = [n for n in os.listdir(stack.run_dir()) if n.startswith("3-review-")]
    assert len(reviews) == 4
    for name in reviews:
        assert "no model calls" in stage_result(stack, name[2:])["reason"]
        assert os.path.exists(os.path.join(stack.run_dir(), name, "REVIEW.md.rejected"))
    packet = stack.text_in_run("4-judging", "judge-packet.md")
    assert packet.count("(No review was written.)") == 4 and "from_another_model" not in packet

    report = stack.text_in_run("REPORT.md")
    assert "| – | mock-nothing | 0 of %d (no build: the agent failed) | – | – |" % full in report
    assert "Build – (mock-nothing)" in report
    assert "6 stage(s) failed" in out
    assert "(FAILED)" in stack.cli("status")


def stack_agent_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except OSError:
        return False


def child_pid(child_file):
    with open(child_file, encoding="utf-8") as fh:
        return int(fh.read())


def test_run_all_builds_failing_still_reports(make_stack):
    stack = unattended(make_stack, "2-build-:nothing")
    out = stack.cli("run", "--allow-sleep", expect=1)
    assert "No build succeeded" in out and not os.path.exists(os.path.join(stack.run_dir(), "blind-key.json"))
    assert "no build: the agent failed" in stack.text_in_run("REPORT.md")


@pytest.mark.parametrize("mode", ["nothing", "no-calls"])
def test_run_stops_when_the_plan_fails_and_can_retry(make_stack, mode):
    stack = unattended(make_stack, "1-plan:" + mode)
    assert "plan stage failed" in stack.cli("run", "--allow-sleep", expect=1)
    assert not os.path.exists(os.path.join(stack.run_dir(), "plan.md"))
    assert not os.path.exists(os.path.join(stack.run_dir(), "1-plan", "plan.md"))
    assert not os.path.exists(os.path.join(stack.run_dir(), "2-build-1"))
    if mode == "no-calls":
        assert os.path.exists(os.path.join(stack.run_dir(), "1-plan", "plan.md.rejected"))
    first = stack.run_dir()
    stack.replace_run_agent([sys.executable, AGENT])
    stack.cli("run", "--allow-sleep")
    assert stack.run_dir() == first and os.path.exists(os.path.join(first, "REPORT.md"))


@posix_only
def test_run_kills_a_stuck_agent_and_what_it_started(make_stack):
    stack = unattended(make_stack, "2-build-1:hang", idle_timeout_s=2)
    stack.env["SCRIPTED_AGENT_CHILD"] = os.path.join(stack.state, "child.pid")
    stack.cli("run", "--allow-sleep")
    result = stage_result(stack, "build-mock-good")
    assert result["status"] == "failed"
    assert "stopped: no model call for 2 s" in result["flags"]
    assert not alive(child_pid(stack.env["SCRIPTED_AGENT_CHILD"]))
    assert stack.summary()["mock-buggy"]["passed"] > 0, "the next build still ran"


def test_slow_call_is_not_stuck_but_the_stage_limit_still_holds(make_stack):
    """The call stays in flight far longer than the idle limit, so only the stage limit may end it."""
    stack = unattended(make_stack, models=("mock-planner", "mock-slow", "mock-good", "mock-reviewer"),
                       builders=[{"label": "mock-slow", "model": "mock-slow"}, {"label": "mock-good", "model": "mock-good"}],
                       idle_timeout_s=2, stage_timeout_s=6, reviewers=[], judges=[])
    stack.cli("run", "--allow-sleep")
    result = stage_result(stack, "build-mock-slow")
    assert "stopped: reached the stage limit of 6 s" in result["flags"]
    assert result["model_calls"] == 1 and result["status"] == "failed"
    assert any(r.get("aborted") for r in stack.proxy_log() if r["stage"] == "build-mock-slow")


@posix_only
def test_run_interrupted_cleans_up_and_resumes(make_stack):
    stack = unattended(make_stack, "2-build-1:hang", idle_timeout_s=120)
    stack.env["SCRIPTED_AGENT_CHILD"] = os.path.join(stack.state, "child.pid")
    proc = start_run_in_background(stack, "--allow-sleep")
    assert wait_until(lambda: os.path.exists(stack.env["SCRIPTED_AGENT_CHILD"]) and
                      os.path.getsize(stack.env["SCRIPTED_AGENT_CHILD"]))
    assert stack.load_stage() == "build-mock-good"

    # A second run at the same time would load models under the first one.
    assert "already working" in stack.cli("run", "--allow-sleep", expect=1)

    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) == 130
    assert not alive(child_pid(stack.env["SCRIPTED_AGENT_CHILD"]))
    assert not proxy_is_up(stack)
    assert not os.path.exists(os.path.join(stack.runs, "agent.pid"))

    stack.replace_run_agent([sys.executable, AGENT])
    first = stack.run_dir()
    stack.cli("run", "--allow-sleep")
    assert stack.run_dir() == first and os.path.exists(os.path.join(first, "REPORT.md"))
    assert stack.summary()["mock-good"]["passed"] == HIDDEN_COUNTS["toy-bepay"]
    with open(os.path.join(first, "stage-history.jsonl"), encoding="utf-8") as fh:
        starts = [json.loads(line)["stage"] for line in fh]
    assert starts.count("build-mock-good") == 2, "the report's Attempts column must show the restart"


@posix_only
def test_run_survives_a_hangup_under_nohup_but_not_otherwise(make_stack):
    stack = unattended(make_stack, "1-plan:hang", idle_timeout_s=120)
    stack.env["SCRIPTED_AGENT_CHILD"] = os.path.join(stack.state, "child.pid")
    output = open(os.path.join(stack.state, "run-output.log"), "w", encoding="utf-8")
    ignoring = subprocess.Popen([sys.executable, PIPELINE, "run", "--allow-sleep"], cwd=ROOT, env=stack.env,
                                stdout=output, stderr=subprocess.STDOUT,
                                preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    stack.procs.append(ignoring)
    assert wait_until(lambda: os.path.exists(os.path.join(stack.runs, "agent.pid")))
    ignoring.send_signal(signal.SIGHUP)
    time.sleep(1.5)
    assert ignoring.poll() is None, "a run under nohup must survive a dropped session"
    ignoring.terminate()
    assert ignoring.wait(timeout=20) == 130

    plain = start_run_in_background(stack, "--allow-sleep")
    plain.send_signal(signal.SIGHUP)
    assert plain.wait(timeout=20) == 130, "without nohup it stops cleanly, so it can resume"
    assert not proxy_is_up(stack)


@posix_only
def test_run_refuses_while_an_orphaned_agent_is_working(make_stack):
    stack = unattended(make_stack, "1-plan:hang", idle_timeout_s=120)
    stack.env["SCRIPTED_AGENT_CHILD"] = os.path.join(stack.state, "child.pid")
    proc = start_run_in_background(stack, "--allow-sleep")
    with open(os.path.join(stack.runs, "agent.pid"), encoding="utf-8") as fh:
        agent_pid = int(fh.read())
    proc.kill()  # nothing can clean up after this
    proc.wait()
    try:
        assert "may still be working" in stack.cli("run", "--allow-sleep", expect=1)
    finally:
        os.killpg(agent_pid, signal.SIGKILL)
    stack.replace_run_agent([sys.executable, AGENT])
    assert wait_until(lambda: not stack_agent_group_alive(agent_pid))
    stack.cli("run", "--allow-sleep")
    assert os.path.exists(os.path.join(stack.run_dir(), "REPORT.md"))
    for proxy_pid in stack.leftover_proxies():
        os.kill(proxy_pid, signal.SIGKILL)


def test_repeat_then_compare(make_stack):
    stack = unattended(make_stack)
    out = stack.cli("run", "--allow-sleep", "--repeat", "2")
    assert "=== Run 2 of 2 ===" in out and "# Comparing 2 finished run(s)" in out
    folders = [n for n in os.listdir(stack.runs) if n.endswith("toy-bepay") or "toy-bepay-" in n]
    assert len(folders) == 2 and all(os.path.exists(os.path.join(stack.runs, n, "REPORT.md")) for n in folders)
    assert not proxy_is_up(stack) and not os.path.exists(os.path.join(stack.runs, "run.pid"))
    assert re.search(r"\| mock-good \| 2 \| 0 \| 43\.0 \(43\.0–43\.0\) of 43 \| 2 \| \d+\.\d \(", out)
    assert "fewer than 5 runs" in out
    with open(os.path.join(stack.tmp, "results", "compare-toy-bepay.md"), encoding="utf-8") as fh:
        assert "| mock-buggy | 2 |" in fh.read()

    # Different tests, prompts, scoring or agent mean different results: never in one average.
    first = os.path.join(stack.runs, sorted(folders)[0], "run.json")
    with open(first, encoding="utf-8") as fh:
        original = fh.read()

    def changes(change):
        run = json.loads(original)
        change(run)
        with open(first, "w", encoding="utf-8") as fh:
            json.dump(run, fh)
        out = stack.cli("compare")
        assert "not averaged together" in out and out.count("## `toy-bepay`: 1 run(s)") == 2
        assert "| mock-good | 2 |" not in out

    for field in ("task", "prompts", "scoring"):
        changes(lambda run: run["hashes"].update({field: "something-else"}))
    changes(lambda run: run["agent"].update({"command": ["another-agent"]}))
    run = json.loads(original)

    # A run from an older version, without what newer runs record, still loads.
    for name in ("machine", "hashes", "agent", "model_details", "test_timeout_s", "request_overrides", "agent_version"):
        run.pop(name, None)
    with open(first, "w", encoding="utf-8") as fh:
        json.dump(run, fh)
    assert "| mock-good | 1 |" in stack.cli("compare")
    assert "No finished runs to compare for task 'cron-next'" in stack.cli("compare", "--task", "cron-next")


def test_old_run_folder_still_reports(make_stack):
    stack = unattended(make_stack, reviewers=[], judges=[])
    stack.cli("run", "--allow-sleep")
    path = os.path.join(stack.run_dir(), "run.json")
    with open(path, encoding="utf-8") as fh:
        run = json.load(fh)
    for name in ("machine", "model_details", "test_timeout_s", "request_overrides", "agent_version"):
        run.pop(name, None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run, fh)
    summary = stack.summary()
    for res in summary.values():
        res.pop("own_tests", None)
    with open(os.path.join(stack.run_dir(), "tests", "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh)
    stack.cli("report")
    report = stack.text_in_run("REPORT.md")
    assert "- Hardware: not reported" in report and "- Agent version: not reported" in report
    assert "hidden tests ? s" in report


def test_compare_counts_failed_builds_and_skips_unfinished_runs(make_stack):
    stack = unattended(make_stack, "2-build-1:nothing", reviewers=[], judges=[])
    stack.cli("run", "--allow-sleep")
    assert "| mock-good | 1 | 1 | 0.0 of 43 | 0 | – |" in stack.cli("compare")
    stack.cli("new-run")  # no report yet, so it may still be blind
    assert "# Comparing 1 finished run(s)" in stack.cli("compare")


def test_repeat_carries_on_after_a_failed_plan(make_stack):
    stack = unattended(make_stack, "1-plan:nothing", reviewers=[], judges=[])
    out = stack.cli("run", "--allow-sleep", "--repeat", "2", expect=1)
    assert "=== Run 2 of 2 ===" in out and "Carrying on with the next one" in out
    assert "No finished runs" in out
    assert "needs a number" in stack.cli("run", "--allow-sleep", "--repeat", "0", expect=1)


def test_repeat_stays_on_the_task_of_the_run_it_resumes(make_stack):
    stack = unattended(make_stack, reviewers=[], judges=[])  # the config's default task is toy-bepay
    stack.cli("new-run", "--task", "cron-next")
    stack.cli("run", "--allow-sleep", "--repeat", "2")
    folders = sorted(n for n in os.listdir(stack.runs) if os.path.isdir(os.path.join(stack.runs, n)))
    assert len(folders) == 2 and all("cron-next" in n for n in folders), folders


def test_one_model_can_be_built_twice_from_the_same_plan(make_stack):
    stack = unattended(make_stack, builders=[{"label": "good-a", "model": "mock-good"},
                                             {"label": "good-b", "model": "mock-good"}], reviewers=[], judges=[])
    stack.cli("run", "--allow-sleep")
    summary = stack.summary()
    assert summary["good-a"]["passed"] == summary["good-b"]["passed"] == HIDDEN_COUNTS["toy-bepay"]
    out = stack.cli("compare")
    assert "| good-a | 1 |" in out and "| good-b | 1 |" in out


def test_run_checks_before_it_starts(make_stack):
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"])
    wait_for_http(stack.mock_url + "/v1/models")
    stack.write_config()
    assert "need a command-line agent" in stack.cli("run", expect=1)

    stack.write_config(agent={"command": "not a list"})
    assert "must be a list" in stack.cli("run", expect=1)
    stack.write_config(agent={"command": ["x"], "idle_timeout_s": 0})
    assert "idle_timeout_s" in stack.cli("run", expect=1)
    stack.write_config(agent={"command": ["no-such-agent-here"]})
    assert "was not found" in stack.cli("run", expect=1)
    assert "not found. Only" in stack.cli("check", expect=None)

    agent = {"command": [sys.executable, AGENT]}
    stack.write_config(agent=agent, judges=[{"label": "j", "type": "api", "base_url": "http://x/v1", "model": "m",
                                             "api_key_env": "RELAYBENCH_TEST_UNSET_KEY"}])
    assert "RELAYBENCH_TEST_UNSET_KEY" in stack.cli("run", expect=1)
    stack.write_config(agent=agent, planner={"label": "gone", "model": "not-on-the-server"})
    assert "Not offered by the server" in stack.cli("run", expect=1)
    stack.write_config(agent=agent, backend={"type": "openai-compatible", "url": "http://127.0.0.1:%d" % free_port()})
    assert "not reachable" in stack.cli("run", expect=1)
    assert not os.path.exists(os.path.join(stack.runs, "CURRENT")), "nothing starts until the checks pass"


def test_run_with_task_refuses_to_abandon_a_run(make_stack):
    stack = unattended(make_stack, "1-plan:nothing")
    stack.cli("run", "--allow-sleep", expect=1)
    assert "isn't finished" in stack.cli("run", "--allow-sleep", "--task", "cron-next", expect=1)


@pytest.mark.skipif(not shutil.which("systemd-inhibit"), reason="needs systemd")
def test_run_holds_a_sleep_inhibitor_only_while_it_runs(make_stack):
    def listed():
        out = subprocess.run(["systemd-inhibit", "--list", "--no-pager"], capture_output=True, encoding="utf-8",
                             errors="replace").stdout
        return "relaybench" in out and "Unattended run" in out

    for how_it_ends in ("signal", "crash"):
        stack = unattended(make_stack, "1-plan:hang", sub=how_it_ends, idle_timeout_s=120)
        stack.env["SCRIPTED_AGENT_CHILD"] = os.path.join(stack.state, "child.pid")
        proc = start_run_in_background(stack)
        with open(os.path.join(stack.state, "run-output.log"), encoding="utf-8") as fh:
            if "could not keep" in fh.read():
                proc.terminate()
                proc.wait()
                pytest.skip("this session may not take inhibitor locks")
        assert wait_until(listed)
        with open(os.path.join(stack.runs, "agent.pid"), encoding="utf-8") as fh:
            agent_pid = int(fh.read())
        if how_it_ends == "signal":
            proc.terminate()
        else:
            proc.kill()
        proc.wait()
        if how_it_ends == "crash":
            os.killpg(agent_pid, signal.SIGKILL)
        assert wait_until(lambda: not listed()), "the inhibitor outlived the run"
        for proxy_pid in stack.leftover_proxies():
            os.kill(proxy_pid, signal.SIGKILL)


def load_pipeline_module():
    spec = importlib.util.spec_from_file_location("pipeline_under_test", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allow_sleep_takes_no_inhibitor_and_missing_tool_only_warns(make_stack, monkeypatch, capsys):
    stack = unattended(make_stack, reviewers=[], judges=[])
    assert "Keeping the machine awake" not in stack.cli("run", "--allow-sleep")
    module = load_pipeline_module()
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    assert module._keep_awake() is None
    assert "GUIDE-UNATTENDED.md" in capsys.readouterr().out
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    assert module._keep_awake() is None


@posix_only
def test_macos_keep_awake_arguments(tmp_path, monkeypatch):
    """Not a test of caffeinate itself, which needs a Mac: only of how it is called."""
    module = load_pipeline_module()
    record = tmp_path / "args"
    fake = tmp_path / "caffeinate"
    fake.write_text("#!/bin/sh\necho \"$@\" > %s\nexec sleep 30\n" % record)
    fake.chmod(0o755)
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(fake))
    holder = module._keep_awake()
    assert holder is not None
    assert wait_until(record.exists)
    assert wait_until(lambda: record.read_text().strip() == "-is -w %d" % os.getpid())
    module._release_awake(holder)
    assert holder.poll() is not None


def test_agent_version_cannot_stall_a_run(tmp_path):
    module = load_pipeline_module()
    script = tmp_path / "agent"
    script.write_text("#!/bin/sh\necho\necho my-agent 1.2.3\n")
    script.chmod(0o755)
    assert module.agent_version([str(script), "--other"]) == "my-agent 1.2.3"
    # Something it leaves running in the background keeps the output open, and must not matter.
    script.write_text("#!/bin/sh\nsleep 30 &\necho my-agent 1.2.3\n")
    started = time.time()
    assert module.agent_version([str(script)], timeout=5) == "my-agent 1.2.3"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    assert module.agent_version([str(script)], timeout=1) is None
    assert time.time() - started < 10
    assert module.agent_version(["no-such-agent-here"]) is None


def test_machine_info_is_best_effort(monkeypatch):
    module = load_pipeline_module()
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    answers = {"machdep.cpu.brand_string": "Apple M5 Max\n", "hw.memsize": "137438953472\n"}
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **kw: Result(answers[cmd[-1]]))
    assert module.machine_info() == {"cpu": "Apple M5 Max", "memory_gb": 128}

    def missing(cmd, **kw):
        raise OSError("no sysctl")
    monkeypatch.setattr(module.subprocess, "run", missing)
    assert module.machine_info() == {"cpu": None, "memory_gb": None}
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    assert module.machine_info() == {"cpu": None, "memory_gb": None}


def test_stage_clock_does_not_count_sleep():
    module = load_pipeline_module()
    clock = module.StageClock(1000.0, 50.0)
    clock.tick(1001.0, 51.0, active=False)
    clock.tick(1002.0, 52.0, active=False)
    assert (clock.awake, clock.idle, clock.slept) == (2.0, 2.0, 0)
    clock.tick(1003.0, 53.0, active=True)
    assert (clock.awake, clock.idle) == (3.0, 0)
    # Asleep for an hour. Where the monotonic clock pauses, it barely moves; on other systems it jumps too.
    clock.tick(4604.0, 54.0, active=False)
    assert (clock.awake, clock.idle, clock.slept) == (3.0, 0, 3601.0)
    clock.tick(8205.0, 3655.0, active=False)
    assert (clock.awake, clock.slept) == (3.0, 7202.0)
    clock.tick(8206.0, 3656.0, active=False)
    assert (clock.awake, clock.idle) == (4.0, 1.0)


def test_sleep_is_flagged_in_the_stage_result(make_stack):
    stack = unattended(make_stack, reviewers=[], judges=[])
    stack.cli("run", "--allow-sleep")
    module = load_pipeline_module()
    cfg = stack.load_config()
    outcome = {"exit_code": 0, "stopped": None, "error": None, "seconds": 60, "slept_s": 1800}
    with open(os.path.join(stack.run_dir(), "run.json"), encoding="utf-8") as fh:
        run_id = json.load(fh)["run_id"]
    result = module._settle_stage(cfg, run_id, "plan", os.path.join(stack.run_dir(), "1-plan"), "plan", outcome, 0)
    assert result["status"] == "done"
    assert "machine slept for 30 min, so wall time is unreliable" in result["flags"]


def test_uncommitted_work_ignores_instructions_and_agent_leftovers(tmp_path):
    module = load_pipeline_module()
    folder = str(tmp_path)
    module.init_stage_repo(folder)
    for name in ("TASK.md", "plan.md", "PROMPT.md"):
        (tmp_path / name).write_text("original\n")
    module.git(folder, "add", "-A")
    module.git(folder, "commit", "-q", "-m", "start")
    (tmp_path / "plan.md").write_text("changed\n")
    (tmp_path / ".cline").mkdir()
    (tmp_path / ".cline" / "state.json").write_text("{}")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_text("")
    assert module._uncommitted_work(folder) == []
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a b.py").write_text("x = 1\n")
    assert module._uncommitted_work(folder) == ["pkg/a b.py"]


def test_unit_helpers():
    spec = importlib.util.spec_from_file_location("pipeline_under_test", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    letters = [module.build_letter(i) for i in (0, 25, 26, 51, 52, 701, 702)]
    assert letters == ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"]
    assert module.sorted_letters(["AA", "B", "A", "Z"]) == ["A", "B", "Z", "AA"]
    assert module.model_listed("pub/m", ["pub/m@8bit"]) and module.model_listed("pub/m@8bit", ["pub/m"])
    assert not module.model_listed("pub/m", ["pub/mm"])
    taken = {}
    assert module.make_label("Org/Model:Tag v2", taken) == "model-tag-v2"
    assert module.make_label("other/Model:Tag v2", taken) == "model-tag-v2-2"
    assert module.make_label("Org/Model:Tag v2", taken) == "model-tag-v2"
    assert module.backend_root({"backend": {"url": "http://x:1/v1/"}}) == "http://x:1"
    assert module.backend_root({"backend": {"url": "https://x/api/v1"}}) == "https://x/api"


@pytest.mark.parametrize("variant", ["lenient", "newline"])
def test_forbidden_ogm_formats_are_caught(make_stack, variant):
    """A build with a very permissive parser once passed all but one of the format tests."""
    model = "mock-" + variant
    stack = make_stack(["mock-planner", "mock-good", model, "mock-reviewer"])
    stack.write_config(builders=[{"label": "mock-good", "model": "mock-good"}, {"label": model, "model": model}])
    stack.start_proxy()
    stack.through_tests()
    summary = stack.summary()
    assert summary["mock-good"]["passed"] == HIDDEN_COUNTS["toy-bepay"]
    with open(os.path.join(FIXTURES, "toy-bepay", "variants.json"), encoding="utf-8") as fh:
        must_fail = json.load(fh)[variant]["must_fail"]
    assert sorted(summary[model]["failing"]) == sorted(must_fail)


def test_run_refuses_a_proxy_from_before_an_update(make_stack):
    """A proxy started before a pull keeps its old code, and could silently ignore request_overrides."""
    stack = unattended(make_stack)

    class OldProxy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ok": True, "pid": 4242}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", stack.proxy_port), OldProxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = stack.cli("run", "--allow-sleep", expect=1)
        assert "started before the code last changed" in out and "4242" in out
        assert not os.path.exists(os.path.join(stack.runs, "CURRENT")), "nothing may have started"
        assert "started before the code last changed" in stack.cli("check", expect=None)
    finally:
        server.shutdown()
        server.server_close()

    # A proxy running the current code is fine, and stays running afterwards.
    stack.start_proxy()
    assert "started before the code last changed" not in stack.cli("check", expect=None)
    assert "started before the code last changed" not in stack.cli("run", "--allow-sleep")
    assert proxy_is_up(stack)
    assert os.path.exists(os.path.join(stack.run_dir(), "REPORT.md"))


def test_init_keeps_pinned_settings(make_stack):
    stack = make_stack(["org/model-a", "org/model-b"])
    wait_for_http(stack.mock_url + "/v1/models")
    answers = "\n".join(["2", stack.mock_url + "/v1", "", str(stack.proxy_port), "1", "2", "", "", "y", "1",
                         stack.runs, "toy-bepay", ""])
    stack.write_config(request_overrides={"max_tokens": 16384})
    out = stack.cli("init", "--force", stdin=answers)
    assert "Keeping your pinned settings" in out
    assert stack.load_config()["request_overrides"] == {"max_tokens": 16384}

    # A value that was already invalid must not make init impossible to run.
    stack.write_config(request_overrides={"seed": 1})
    assert "Keeping your pinned settings" not in stack.cli("init", "--force", stdin=answers)
    assert "request_overrides" not in stack.load_config()


def test_compare_keeps_pinned_and_unpinned_runs_apart(make_stack):
    stack = unattended(make_stack, reviewers=[], judges=[])
    stack.cli("run", "--allow-sleep")
    cfg = stack.load_config()
    cfg["request_overrides"] = {"max_tokens": 16384}
    stack.save_config(cfg)
    stack.cli("run", "--allow-sleep")
    out = stack.cli("compare")
    assert out.count("## `toy-bepay`: 1 run(s)") == 2, out
    assert "`{\"max_tokens\": 16384}`" in out and "pinned settings" in out


def test_harness_commit_says_when_files_have_changed(tmp_path):
    repo = tmp_path / "harness"
    repo.mkdir()
    shutil.copy(PIPELINE, str(repo / "pipeline.py"))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git"] + command, cwd=str(repo), env=env, check=True, capture_output=True)

    def commit():
        return subprocess.run([sys.executable, "-c", "import pipeline; print(pipeline.harness_commit())"], cwd=str(repo),
                              env=env, check=True, capture_output=True, encoding="utf-8").stdout.strip()

    assert re.fullmatch(r"[0-9a-f]+", commit())
    (repo / "not-tracked.txt").write_text("doesn't count")
    assert re.fullmatch(r"[0-9a-f]+", commit())
    with open(str(repo / "pipeline.py"), "a", encoding="utf-8") as fh:
        fh.write("# changed\n")
    assert commit().endswith(" (with uncommitted changes)")
