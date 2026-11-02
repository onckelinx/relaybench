"""End-to-end tests: the real CLI and the real proxy process against a mock model server.

No model and no real agent are involved: tests/scripted_agent.py plays the agent's part.
Set RELAYBENCH_WRITE_EXAMPLE=<file> to save the toy run's report as a labelled example.
"""
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
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
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
HIDDEN_COUNTS = {"toy-bepay": 41, "cron-next": 100, "wal-kv": 57}

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
            fh.write(EXAMPLE_BANNER + stack.text_in_run("REPORT.md"))


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
    answers = "1\n%s\n\n65536\nmax\n%d\n1\n2,3\n4\nhuman\n\n\n%s\ntoy-bepay\n" % (stack.mock_url, stack.proxy_port, stack.runs)
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
    assert "4 |" in report and "41 of 41" in report

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
    answers = "\n".join(["2", stack.mock_url + "/v1", "", str(stack.proxy_port), "n", "p/planner", "p/builder", "", "human", "", "", stack.runs, "", ""])
    out = stack.cli("init", stdin=answers)
    assert "No models found" in out
    assert stack.load_config()["planner"]["model"] == "p/planner"


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
    assert res["timeout"] and res["passed"] == 0 and res["expected"] == 41
    assert stack.summary()["mock-good"]["passed"] == 41
    stack.cli("blind")
    stack.review_all()
    stack.cli("packet")
    assert "0 of 41 (timeout" in stack.text_in_run("4-judging", "judge-packet.md")


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


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl to make a test certificate")
def test_https_backend(make_stack, tmp_path):
    cert, key = str(tmp_path / "cert.pem"), str(tmp_path / "key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                    "-days", "2", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    stack = make_stack(["mock-planner", "mock-good", "mock-buggy", "mock-reviewer"],
                       mock_args=["--tls-cert", cert, "--tls-key", key], env={"SSL_CERT_FILE": cert})
    time.sleep(0.5)
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
