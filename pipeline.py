#!/usr/bin/env python3
"""relaybench: a test harness for local coding models.

Workflow: planner model writes plan.md -> builder models implement it ->
reviewer models review each build (blind) -> judges score everything.

A coding agent of your choice talks to the logging proxy under one fixed model
name, so its settings never change between stages. This script switches which
real model answers to that name, and tells the proxy which run and stage is
active through <logs_dir>/current-stage.json.

A person normally drives the agent, stage by stage, with `next`. With a
command-line agent in the config, `run` does the whole run unattended.

Run `python pipeline.py help` for the list of commands, or see GUIDE-MANUAL.md and GUIDE-UNATTENDED.md.
The harness is standard library only (Python 3.9+); hidden tests need pytest.
"""
import argparse
import datetime
import fnmatch
import hashlib
import importlib.util
import json
import os
import platform
import random
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("RELAYBENCH_CONFIG") or os.path.join(HERE, "pipeline.json")
PLACEHOLDER = "PASTE-MODEL-KEY-HERE"
LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
JUDGE_TYPES = ("paste", "api", "human")
CRITERIA = ["correctness", "plan_adherence", "code_quality", "edge_cases", "own_tests"]
REVIEW_FIELDS = ["real_issues", "false_alarms", "missed_important", "accuracy", "usefulness"]
# Agents and tools leave these behind. Some of them name the model, which would break blinding.
SKIP_IN_COPY = (".git", ".claude*", ".cline*", ".aider*", ".continue*", ".cursor*", ".roo*", ".windsurf*",
                ".opencode*", ".vscode", ".idea", "__pycache__", ".pytest_cache", ".venv", "venv",
                "node_modules", "PROMPT.md", ".DS_Store", "Thumbs.db")
SKIP_IN_PACKET = SKIP_IN_COPY + ("TASK.md", "plan.md", "REVIEW.md", ".gitignore")
# A label this short would match ordinary words, so it is not searched for.
MIN_SCAN_LABEL = 4
# In a virtual environment, and on Windows, `python` is the name that always works.
PY = "python" if os.name == "nt" or sys.prefix != sys.base_prefix else "python3"
CMD = PY + " pipeline.py"
AGENT_POLL_S = 1
# A gap this long between polls means the machine slept, not that the stage was busy.
SLEEP_GAP_S = 30
AGENT_DEFAULTS = {"stage_timeout_s": 3600, "idle_timeout_s": 600}
STAGE_MESSAGE = "Follow the instructions in PROMPT.md"
AGENT_EXAMPLE = """  "agent": { "command": ["my-agent", "--some-auto-approve-flag", "%s"] }""" % STAGE_MESSAGE
# Printed instructions are for a person, so `run` turns them off.
UNATTENDED = False

HELP = """
Commands (run them from the repository folder):

  {cmd} init             Write pipeline.json interactively (lists your models)
  {cmd} check            Check that everything is installed and running
  {cmd} models           List the models the server offers
  {cmd} new-run          Start a new run (creates the plan folder)
  {cmd} next             Start the next stage (switches to the right model)
  {cmd} status           Show the current run and what comes next
  {cmd} freeze           Freeze plan.md and create the build folders
  {cmd} test             Run the hidden tests on every build
  {cmd} blind            Anonymise builds and create the review folders
  {cmd} packet           Create the judge packet
  {cmd} judge            Send the packet to the "api" judges
  {cmd} report           Collect speed, tests and scores into REPORT.md
  {cmd} run              Do all of the above unattended, with a command-line agent
  {cmd} compare          Compare finished runs, per model

Less common:

  {cmd} start <stage>    (Re)start one stage, e.g. build-<label>
  {cmd} load <label>     Switch to one model by hand (outside a run)
  {cmd} smoke [label]    Send a tiny test message through the proxy

Options:

  --task <name>          With new-run or run: use this task instead of the configured one
                         With compare: only this task
  --repeat <n>           With run: do n runs in a row, then compare them
  --force                With freeze, blind: redo. Outputs that depended on the old result
                         are moved to a _stale-<time> folder in the run, not deleted.
                         With judge: ask the api judges again, replacing their answers.
                         With init: overwrite pipeline.json without asking.
  --show                 With test: print per-model results (this un-blinds you)
  --allow-sleep          With run: don't keep the machine awake

Labels are the names in pipeline.json. Set RELAYBENCH_CONFIG to use another config file.
""".format(cmd=CMD)


# ----------------------------------------------------------------- helpers

def say(msg=""):
    print(msg)


def ok(msg):
    print("  [ OK ] " + msg)


def fail(msg):
    print("  [FAIL] " + msg)


def warn(msg):
    print("  [WARN] " + msg)


def die(msg):
    print("\nERROR: " + msg)
    sys.exit(1)


def next_step(lines):
    if UNATTENDED:
        return
    print("\nNext:")
    for line in lines:
        print("  " + line)
    print()


def validate_config(cfg):
    """Stop early with a clear message: labels end up in stage names and file names."""
    if not isinstance(cfg.get("planner"), dict):
        die("pipeline.json needs a \"planner\" with a label and a model.")
    if not cfg.get("builders"):
        die("pipeline.json needs at least one entry in \"builders\".")
    keys = {}
    models = [cfg["planner"]] + list(cfg.get("builders", [])) + list(cfg.get("reviewers", []))
    for model in models:
        label = model.get("label", "")
        if not LABEL_RE.match(label):
            die("Label '%s' is not valid. Use lowercase letters, digits, dots, dashes and underscores." % label)
        if keys.get(label, model.get("model")) != model.get("model"):
            die("Label '%s' is used for two different models." % label)
        keys[label] = model.get("model")
    for role in ("builders", "reviewers"):
        labels = [m["label"] for m in cfg.get(role, [])]
        if len(labels) != len(set(labels)):
            die("Each model can appear only once in \"%s\"." % role)
    judges = cfg.get("judges", [])
    labels = [j.get("label", "") for j in judges]
    if len(labels) != len(set(labels)):
        die("Each judge needs its own label.")
    for judge in judges:
        if not LABEL_RE.match(judge.get("label", "")):
            die("Judge label '%s' is not valid." % judge.get("label", ""))
        if judge.get("type") not in JUDGE_TYPES:
            die("Judge '%s' needs a type: %s." % (judge["label"], ", ".join(JUDGE_TYPES)))
        if judge["type"] == "api" and not (judge.get("base_url") and judge.get("model")):
            die("Judge '%s' has type \"api\", so it needs base_url and model." % judge["label"])
    backend = cfg.get("backend", {})
    if backend.get("type", "lmstudio") not in ("lmstudio", "openai-compatible"):
        die("backend.type must be \"lmstudio\" or \"openai-compatible\".")
    overrides = cfg.get("request_overrides")
    if overrides is not None:
        if not isinstance(overrides, dict) or set(overrides) - {"temperature", "max_tokens"}:
            die("request_overrides can only hold temperature and max_tokens.")
        temperature, limit = overrides.get("temperature", 0), overrides.get("max_tokens", 1)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or temperature < 0:
            die("request_overrides.temperature must be a number of 0 or more.")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            die("request_overrides.max_tokens must be a whole number above 0.")
    agent = cfg.get("agent")
    if agent is not None:
        command = agent.get("command") if isinstance(agent, dict) else None
        if not (isinstance(command, list) and command and all(isinstance(c, str) and c for c in command)):
            die("agent.command must be a list of strings, like:\n" + AGENT_EXAMPLE)
        for name in AGENT_DEFAULTS:
            value = agent.get(name, AGENT_DEFAULTS[name])
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                die("agent.%s must be a number of seconds above 0." % name)


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        die("%s not found. Create it with:  %s init\n"
            "(or copy pipeline.example.json to pipeline.json and edit it)" % (CONFIG_PATH, CMD))
    except ValueError as exc:
        die("%s is not valid JSON: %s\n"
            "Tip: look for a missing comma or quote near the line number above." % (CONFIG_PATH, exc))
    validate_config(cfg)
    return cfg


def resolve_dir(value):
    return os.path.normpath(os.path.join(HERE, os.path.expanduser(value)))


def runs_root(cfg):
    return resolve_dir(cfg.get("runs_dir", "runs"))


def logs_root(cfg):
    return resolve_dir(cfg.get("logs_dir", "logs"))


def log_file(cfg):
    return os.path.join(logs_root(cfg), "proxy-log.jsonl")


def stage_file(cfg):
    return os.path.join(logs_root(cfg), "current-stage.json")


def tasks_root(cfg):
    return resolve_dir(cfg.get("tasks_dir", "tasks"))


def task_dir(cfg, task):
    return os.path.join(tasks_root(cfg), task)


def list_tasks(cfg):
    root = tasks_root(cfg)
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root) if os.path.exists(os.path.join(root, n, "TASK.md")))


def served_name(cfg):
    return cfg.get("served_model_name", "local-model")


def backend_of(cfg):
    return cfg.get("backend", {})


def backend_root(cfg):
    """Backend URL without a trailing slash or /v1."""
    url = backend_of(cfg).get("url", "http://127.0.0.1:1234").strip().rstrip("/")
    return url[:-3].rstrip("/") if url.endswith("/v1") else url


def proxy_url(cfg):
    return "http://127.0.0.1:%d" % int(cfg.get("proxy_port", 1235))


def all_models(cfg):
    """Works on a config and on a run's snapshot of it."""
    out = [dict(cfg["planner"], role="planner")]
    out += [dict(m, role="builder") for m in cfg.get("builders", [])]
    out += [dict(m, role="reviewer") for m in cfg.get("reviewers", [])]
    return out


def find_model(source, label):
    for model in all_models(source):
        if model["label"] == label:
            return model
    labels = ", ".join(m["label"] for m in all_models(source))
    die("No model with label '%s'.\nKnown labels: %s" % (label, labels))


def lms_bin():
    found = shutil.which("lms")
    if found:
        return found
    for name in ("lms", "lms.exe"):
        candidate = os.path.join(os.path.expanduser("~"), ".lmstudio", "bin", name)
        if os.path.exists(candidate):
            return candidate
    return None


def uses_alias(cfg):
    """True when models are loaded under the shared name. Otherwise the proxy rewrites `model`."""
    return backend_of(cfg).get("type", "lmstudio") == "lmstudio" and bool(lms_bin())


def backend_headers(cfg):
    env_name = backend_of(cfg).get("api_key_env")
    key = os.environ.get(env_name) if env_name else None
    return {"Authorization": "Bearer " + key} if key else {}


def http_json(url, payload=None, timeout=10, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    all_headers = {"Content-Type": "application/json"} if data else {}
    all_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=all_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def server_problem(cfg, exc):
    """Why the server can't be used. A rejected certificate isn't the same as a server that is down."""
    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
        return ("The model server at %s has a certificate this Python doesn't trust: %s"
                % (backend_root(cfg), exc))
    return "The model server is not reachable at %s." % backend_root(cfg)


def loaded_model_ids(cfg):
    result = http_json(backend_root(cfg) + "/v1/models", timeout=5, headers=backend_headers(cfg))
    return [m.get("id") for m in result.get("data", [])]


def _models_from_lmstudio_api(root, headers):
    """LM Studio's native API. Unlike /v1/models it also lists models that are not loaded."""
    try:
        result = http_json(root + "/api/v1/models", timeout=5, headers=headers)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    rows = result.get("models") if isinstance(result, dict) else None
    return [m["key"] for m in rows or [] if m.get("key") and m.get("type", "llm") == "llm"]


def _models_from_lms_cli():
    lms = lms_bin()
    if not lms:
        return []
    try:
        out = subprocess.run([lms, "ls", "--json"], capture_output=True, encoding="utf-8",
                             errors="replace", timeout=30).stdout
        rows = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    ids = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("type", "llm") != "embedding":
            key = row.get("modelKey") or row.get("key") or row.get("path") or row.get("id")
            if key:
                ids.append(key)
    return ids


def _models_from_openai_api(root, headers):
    try:
        result = http_json(root + "/v1/models", timeout=5, headers=headers)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [m["id"] for m in result.get("data", []) if m.get("id") and "embed" not in m["id"].lower()]


def list_models(kind, root, alias, headers=None):
    """Model ids the backend offers, without embeddings and without the shared name."""
    headers = headers or {}
    ids = []
    if kind == "lmstudio":
        ids = _models_from_lmstudio_api(root, headers) or _models_from_lms_cli()
    ids = ids or _models_from_openai_api(root, headers)
    return sorted(set(i for i in ids if i != alias))


def model_listed(key, ids):
    key = key.lower()
    for model_id in ids:
        model_id = model_id.lower()
        if key == model_id or model_id.startswith(key + "@") or key.startswith(model_id + "@"):
            return True
    return False


def get_stage(cfg):
    try:
        with open(stage_file(cfg), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def set_stage(cfg, run_id, stage, label, model_id=None, sentinels=None, overrides=None):
    os.makedirs(logs_root(cfg), exist_ok=True)
    tmp = stage_file(cfg) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"run_id": run_id, "stage": stage, "model": label, "model_id": model_id,
                   "sentinels": sentinels or [], "overrides": overrides or {},
                   "since": datetime.datetime.now().isoformat(timespec="seconds")}, fh, indent=2)
    os.replace(tmp, stage_file(cfg))  # the proxy reads this file at any moment


def current_run(cfg):
    root = runs_root(cfg)
    pointer = os.path.join(root, "CURRENT")
    if not os.path.exists(pointer):
        die("There is no current run yet. Start one with:  %s new-run" % CMD)
    with open(pointer, encoding="utf-8") as fh:
        run_id = fh.read().strip()
    path = os.path.join(root, run_id)
    if not os.path.isdir(path):
        die("The current run folder is missing: %s\nStart a new run with:  %s new-run" % (path, CMD))
    with open(os.path.join(path, "run.json"), encoding="utf-8") as fh:
        run = json.load(fh)
    return run_id, path, run


def git(folder, *args):
    return subprocess.run(["git"] + list(args), cwd=folder, check=True, capture_output=True,
                          encoding="utf-8", errors="replace")


def commit_count(folder):
    # Without this check Git walks up and counts the commits of whichever repo holds the runs folder.
    if not os.path.isdir(os.path.join(folder, ".git")):
        return 0
    try:
        out = git(folder, "rev-list", "--count", "HEAD").stdout.strip()
        return int(out)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0


def init_stage_repo(folder):
    """Give a stage folder its own repo, so Git commands in it can't reach a repo further up."""
    git(folder, "init", "-q")
    git(folder, "config", "user.name", "relaybench")
    git(folder, "config", "user.email", "relaybench@localhost")
    git(folder, "config", "commit.gpgsign", "false")
    with open(os.path.join(folder, ".git", "info", "exclude"), "a", encoding="utf-8") as fh:
        fh.write("\n__pycache__/\n.pytest_cache/\n")


def is_skipped(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def copy_prompt(name, folder):
    shutil.copyfile(os.path.join(HERE, "prompts", name + ".md"), os.path.join(folder, "PROMPT.md"))


def copy_tree_filtered(src, dest, skip):
    def ignore(_dir, names):
        return [n for n in names if is_skipped(n, skip)]
    shutil.copytree(src, dest, ignore=ignore)


def _make_writable_and_retry(func, path, _exc):
    os.chmod(path, stat.S_IRWXU)
    func(path)


def force_rmtree(path):
    """rmtree that also works on Windows, where Git objects and the frozen plan are read-only."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(path, onerror=_make_writable_and_retry)


def tree_hash(folder):
    digest = hashlib.sha256()
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            full = os.path.join(root, name)
            digest.update(os.path.relpath(full, folder).replace(os.sep, "/").encode())
            with open(full, "rb") as fh:
                digest.update(fh.read())
    return digest.hexdigest()[:16]


def harness_commit():
    if not os.path.isdir(os.path.join(HERE, ".git")):
        return None
    try:
        return git(HERE, "rev-parse", "--short", "HEAD").stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def machine_info():
    """CPU and memory, best effort: speeds mean little without them, and they are easy to forget to note."""
    cpu, memory = None, None
    try:
        if platform.system() == "Darwin":
            def sysctl(name):
                return subprocess.run(["sysctl", "-n", name], capture_output=True, encoding="utf-8",
                                      errors="replace", timeout=5).stdout.strip()
            cpu, memory = sysctl("machdep.cpu.brand_string"), int(sysctl("hw.memsize"))
        elif platform.system() == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
                cpu = next((line.split(":", 1)[1].strip() for line in fh if line.startswith("model name")), None)
            with open("/proc/meminfo", encoding="utf-8", errors="replace") as fh:
                memory = next((int(line.split()[1]) * 1024 for line in fh if line.startswith("MemTotal")), None)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    return {"cpu": cpu or None, "memory_gb": round(memory / 1024 ** 3) if memory else None}


def model_details(cfg):
    """Each model's format and quantisation, keyed by model id, if the server says. Only LM Studio's own API does."""
    if backend_of(cfg).get("type", "lmstudio") != "lmstudio":
        return {}
    try:
        rows = http_json(backend_root(cfg) + "/api/v1/models", timeout=5, headers=backend_headers(cfg)).get("models")
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return {}
    details = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        quantisation = row.get("quantization")
        if isinstance(quantisation, dict):
            quantisation = quantisation.get("name")
        words = [str(w) for w in (row.get("format"), quantisation) if w]
        if row.get("key") and words:
            details[row["key"]] = " ".join(words)
    return details


def agent_version(command, timeout=10):
    """What the agent says its version is. Through a file and with a limit, so that it can't stall a run."""
    work = tempfile.mkdtemp(prefix="relaybench-version-")
    try:
        output = os.path.join(work, "version.txt")
        if _run_logged([command[0], "--version"], work, dict(os.environ), output, timeout):
            return None
        with open(output, encoding="utf-8", errors="replace") as fh:
            return next((line.strip()[:80] for line in fh if line.strip()), None)
    except OSError:
        return None
    finally:
        force_rmtree(work)


def is_unchanged_starter(full_path, rel_path, starter):
    """True for starter files (like the starter README) that the builder did not change."""
    return same_file_content(full_path, os.path.join(starter, rel_path))


def same_file_content(a_path, b_path):
    if not (os.path.isfile(a_path) and os.path.isfile(b_path)):
        return False
    with open(a_path, "rb") as a, open(b_path, "rb") as b:
        return a.read() == b.read()


def read_blind_key(path):
    key_path = os.path.join(path, "blind-key.json")
    if not os.path.exists(key_path):
        return None
    with open(key_path, encoding="utf-8") as fh:
        return json.load(fh)


def build_letter(index):
    """0 -> A, 25 -> Z, 26 -> AA, so any number of builders can be anonymised."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def sorted_letters(letters):
    return sorted(letters, key=lambda s: (len(s), s))


def build_folder(path, run, label):
    """Numbered, not named after the model: reviewers can see folder names in their paths."""
    index = [b["label"] for b in run["builders"]].index(label) + 1
    return os.path.join(path, "2-build-%d" % index)


def model_stages(path, run):
    """All model stages of a run, in order: (stage name, folder, model label, kind)."""
    stages = [("plan", os.path.join(path, "1-plan"), run["planner"]["label"], "plan")]
    for builder in run["builders"]:
        stages.append(("build-" + builder["label"], build_folder(path, run, builder["label"]),
                       builder["label"], "build"))
    key = read_blind_key(path)
    if key:
        for r_index, reviewer in enumerate(run["reviewers"], start=1):
            for letter in sorted_letters(key["builds"]):
                name = "review-%s-by-reviewer-%d" % (letter, r_index)
                stages.append((name, os.path.join(path, "3-" + name), reviewer["label"], "review"))
    return stages


def stage_done(kind, folder):
    if kind == "plan":
        return os.path.exists(os.path.join(folder, "plan.md"))
    if kind == "build":
        return os.path.isdir(folder) and commit_count(folder) > 1
    return os.path.exists(os.path.join(folder, "REVIEW.md"))


def stage_sentinels(path, run, kind, label):
    """Strings the model has no reason to see in this stage (see proxy/llm_proxy.py)."""
    sentinels = ["hidden_tests", "blind-key"]
    labels = [b["label"] for b in run["builders"]]
    for other in labels:
        if kind == "build" and other == label:
            continue
        sentinels.append(os.path.basename(build_folder(path, run, other)))
        if len(other) >= MIN_SCAN_LABEL:
            sentinels.append(other)
    return sentinels


def _read_stage_results(path):
    """Outcomes recorded by `run`. Manual runs have none."""
    try:
        with open(os.path.join(path, "stage-results.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_stage_results(path, results):
    tmp = os.path.join(path, "stage-results.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    os.replace(tmp, os.path.join(path, "stage-results.json"))


def _forget_stage_results(path, should_forget):
    results = _read_stage_results(path)
    kept = {name: res for name, res in results.items() if not should_forget(name)}
    if len(kept) != len(results):
        _write_stage_results(path, kept)


def failed_stages(path):
    return {name for name, res in _read_stage_results(path).items() if res.get("status") == "failed"}


def failed_builders(path, run):
    failed = failed_stages(path)
    return [b["label"] for b in run["builders"] if "build-" + b["label"] in failed]


def ensure_loaded(cfg, source, label):
    """Make `label` the model that answers. `source` is the run's snapshot, or the config outside a run."""
    model = find_model(source, label)
    if model["model"] == PLACEHOLDER:
        die("The model key for '%s' is still a placeholder in pipeline.json." % label)
    try:
        ids = loaded_model_ids(cfg)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        die(server_problem(cfg, exc) + " Fix that first.")
    if not uses_alias(cfg):
        if backend_of(cfg).get("type", "lmstudio") == "lmstudio":
            warn("LM Studio's 'lms' command was not found, so the harness can't set the context length. "
                 "Requests name the model directly, which needs Just-in-Time loading on in LM Studio.")
        say("\nCalls will be sent to %s (the proxy rewrites the model name)." % model["model"])
        return
    if get_stage(cfg).get("model") == label and ids == [served_name(cfg)]:
        say("\n%s is already loaded." % label)
        return
    lms = lms_bin()
    ctx = source.get("backend", backend_of(cfg)).get("context_length", 65536)
    say("\nUnloading all models...")
    if subprocess.run([lms, "unload", "--all"]).returncode != 0:
        warn("'lms unload --all' reported a problem. If another model is still loaded, speed numbers will suffer.")
    say("\nLoading %s (%s) with a context of %s tokens..." % (label, model["model"], ctx))
    say("This can take a minute for large models.\n")
    command = [lms, "load", model["model"], "--identifier", served_name(cfg), "--context-length", str(ctx)]
    gpu = source.get("backend", backend_of(cfg)).get("gpu")
    if gpu:
        command += ["--gpu", str(gpu)]
    if subprocess.run(command).returncode != 0:
        die("Loading failed. Check the model key in pipeline.json with:  %s models" % CMD)
    try:
        ids = loaded_model_ids(cfg)
    except (urllib.error.URLError, OSError, ValueError):
        die("The model is loaded, but the server is not reachable at %s." % backend_root(cfg))
    if served_name(cfg) in ids:
        say("\nLoaded %s. Your agent sees it as: %s" % (label, served_name(cfg)))
    else:
        warn("Loaded, but the server lists: %s (expected %s)" % (", ".join(ids) or "nothing", served_name(cfg)))


def stage_instructions(cfg, folder, kind):
    done_word = {"plan": "PLAN DONE", "build": "BUILD DONE", "review": "REVIEW DONE"}[kind]
    lines = [
        "1. Open this folder as the workspace of your coding agent:",
        "     %s" % folder,
        "2. Start a NEW agent session. It must be able to edit files and run commands.",
        "   (Agent settings, once: base URL %s/v1, model %s.)" % (proxy_url(cfg), served_name(cfg)),
        "3. Send:  " + STAGE_MESSAGE,
        "4. Wait for %s." % done_word,
    ]
    if kind == "build":
        lines.append("5. Make sure the work is committed: 'git log --oneline' shows at least 2 commits.")
    lines.append("%d. Close the session, then run:  %s next" % (6 if kind == "build" else 5, CMD))
    return lines


def start_stage(cfg, run_id, path, run, name, folder, label, kind):
    if not os.path.isdir(folder):
        die("Folder for stage '%s' does not exist: %s" % (name, folder))
    ensure_loaded(cfg, run, label)
    _forget_stage_results(path, lambda stage: stage == name)
    model_id = None if uses_alias(cfg) else find_model(run, label)["model"]
    set_stage(cfg, run_id, name, label, model_id, stage_sentinels(path, run, kind, label),
              run.get("request_overrides"))
    with open(os.path.join(path, "stage-history.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"stage": name, "at": datetime.datetime.now().isoformat(timespec="seconds")}) + "\n")
    say("\nSTAGE: %s   (model: %s)" % (name, label))
    say("Folder: %s" % folder)
    say("The proxy now logs every model call under this stage.")
    next_step(stage_instructions(cfg, folder, kind))


def move_to_stale(path, names):
    """Outputs of an older plan or anonymisation would be credited to the wrong model. Keep them apart."""
    existing = [n for n in names if os.path.exists(os.path.join(path, n))]
    if not existing:
        return None
    stale = os.path.join(path, "_stale-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    n = 2
    while os.path.exists(stale):
        stale = stale.rsplit("~", 1)[0] + "~" + str(n)
        n += 1
    os.makedirs(stale)
    for name in existing:
        os.rename(os.path.join(path, name), os.path.join(stale, name))
    say("Moved old outputs to %s" % stale)
    return stale


def find_label_text(text, labels):
    lowered = text.lower()
    return any(len(label) >= MIN_SCAN_LABEL and label.lower() in lowered for label in labels)


def files_mentioning_labels(folder, labels):
    hits = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            if name in ("TASK.md", "plan.md", "PROMPT.md"):
                continue
            full = os.path.join(root, name)
            if os.path.getsize(full) > 1000000:
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                if find_label_text(fh.read(), labels):
                    hits.append(os.path.relpath(full, folder).replace(os.sep, "/"))
    return hits


# ----------------------------------------------------------------- init

def ask(prompt, default=None):
    suffix = " [%s]" % default if default not in (None, "") else ""
    try:
        answer = input("%s%s: " % (prompt, suffix)).strip()
    except EOFError:
        die("'init' needs answers, but the input ended.")
    return answer if answer else (default if default is not None else "")


def ask_yes(prompt, default=False):
    answer = ask("%s (y/n)" % prompt, "y" if default else "n").lower()
    return answer.startswith("y")


def ask_int(prompt, default):
    while True:
        answer = ask(prompt, str(default))
        if answer.isdecimal() and int(answer) > 0:
            return int(answer)
        say("  Please type a whole number.")


def ask_env_name(prompt, default=""):
    while True:
        name = ask(prompt, default)
        if not name or ENV_NAME_RE.match(name):
            return name
        say("  That doesn't look like a variable name. Enter the NAME of the environment variable")
        say("  that holds the key, not the key itself. Keys are never written to the config.")


def make_label(model_id, taken):
    """A short, file-name-safe label from a model id. `taken` maps labels to the id using them."""
    base = re.sub(r"[^a-z0-9._-]+", "-", model_id.lower().rstrip("/").rsplit("/", 1)[-1]).strip("-._")
    base = base or "model"
    label, n = base, 2
    while taken.get(label, model_id) != model_id:
        label = "%s-%d" % (base, n)
        n += 1
    taken[label] = model_id
    return label


def pick_models(prompt, models, taken, many=False, required=True):
    """Ask for model numbers or ids. Returns [{label, model}]."""
    while True:
        answer = ask(prompt, "")
        chosen = []
        problem = None
        for item in [p.strip() for p in answer.split(",")] if answer else []:
            if item.isdecimal():
                if not 1 <= int(item) <= len(models):
                    problem = "There is no model number %s." % item
                    break
                item = models[int(item) - 1]
            if item not in chosen:
                chosen.append(item)
        if problem:
            say("  " + problem)
        elif required and not chosen:
            say("  Please choose at least one.")
        elif not many and len(chosen) > 1:
            say("  Please choose exactly one.")
        else:
            return [{"label": make_label(c, taken), "model": c} for c in chosen]


def ask_judges():
    judges = []
    say("\nJudges score the anonymous builds. Add as many as you like:")
    say("  paste  a cloud LLM in a web chat: you paste the packet in and its answer back")
    say("  api    a cloud LLM called directly with an OpenAI-compatible API")
    say("  human  a person who fills in a score file")
    while True:
        kind = ask("Judge %d type (paste, api, human; empty to finish)" % (len(judges) + 1), "").lower()
        if not kind:
            if judges or ask_yes("No judges means no scores in the report. Continue anyway?", False):
                return judges
            continue
        if kind not in JUDGE_TYPES:
            say("  Please type paste, api or human.")
            continue
        label = ask("  Label", "%s-%d" % (kind, len(judges) + 1)).lower()
        if not LABEL_RE.match(label) or label in [j["label"] for j in judges]:
            say("  Use a new label with lowercase letters, digits, dots, dashes and underscores.")
            continue
        judge = {"label": label, "type": kind}
        if kind == "api":
            judge["base_url"] = ask("  Base URL, for example https://api.example.com/v1").rstrip("/")
            judge["model"] = ask("  Model id")
            judge["api_key_env"] = ask_env_name("  Name of the environment variable with the API key", "JUDGE_API_KEY")
            if not (judge["base_url"] and judge["model"]):
                say("  An api judge needs a base URL and a model id. Try again.")
                continue
        judges.append(judge)


def current_agent():
    """The agent in an existing config, so that running init again doesn't lose it."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            agent = json.load(fh).get("agent")
    except (OSError, ValueError, AttributeError):
        return None
    command = agent.get("command") if isinstance(agent, dict) else None
    if isinstance(command, list) and command and all(isinstance(c, str) and c for c in command):
        return agent
    return None


def ask_agent(current):
    """Which command-line agent `run` uses. Returns its settings, or None for manual runs only."""
    say("\nA command-line agent lets '%s run' do a whole run unattended:" % CMD)
    say("  1. None: I'll drive an agent myself, stage by stage")
    say("  2. Cline's command-line agent")
    say("  3. Another command-line agent")
    if current:
        say("  4. Keep the current one: %s" % shlex.join(current["command"]))
    while True:
        choice = ask("Choose", "4" if current else "1")
        if choice == "1":
            return None
        if choice == "4" and current:
            return current
        if choice == "2":
            command = ["cline", "-y", STAGE_MESSAGE]
        elif choice == "3":
            say("  Type its command, with any flag it needs to run without asking. The instructions are added at")
            say("  the end. Leave out API keys, because reports show this command.")
            try:
                words = shlex.split(ask("  Command"))
            except ValueError as exc:
                say("  That command can't be read: %s." % exc)
                continue
            if not words:
                say("  Please type a command, or choose 1 for none.")
                continue
            command = words + [STAGE_MESSAGE]
        else:
            say("  Please type a number from the list.")
            continue
        if not shutil.which(command[0]):
            warn("'%s' was not found on this machine. Install it before using '%s run'." % (command[0], CMD))
        return dict(AGENT_DEFAULTS, command=command)


def cmd_init(_cfg, args):
    say("\nThis writes %s. Press Ctrl+C to stop without saving.\n" % CONFIG_PATH)
    if os.path.exists(CONFIG_PATH) and not args.force:
        if not ask_yes("That file already exists. Overwrite it?", False):
            say("Nothing changed.\n")
            return

    say("Model server:\n  1. LM Studio\n  2. Another OpenAI-compatible server")
    kind = "openai-compatible" if ask("Choose", "1") == "2" else "lmstudio"
    if kind == "lmstudio":
        url = ask("Server URL", "http://127.0.0.1:1234")
    else:
        url = ""
        while not url:
            url = ask("Server URL, for example http://127.0.0.1:11434/v1")
    key_env = ask_env_name("Environment variable with the server's API key (empty if none)", "")
    backend = {"type": kind, "url": url, "api_key_env": key_env}
    if kind == "lmstudio":
        backend["context_length"] = ask_int("Context length in tokens", 65536)
        backend["gpu"] = ask("GPU offload for 'lms load' (max, off, or 0-1)", "max")
    proxy_port = ask_int("Port for the logging proxy", 1235)
    alias = "local-model"

    probe = {"backend": backend}
    root = backend_root(probe)
    headers = backend_headers(probe)
    while True:
        models = list_models(kind, root, alias, headers)
        if models:
            say("\nModels on %s:" % root)
            for number, model_id in enumerate(models, start=1):
                say("  %2d. %s" % (number, model_id))
            break
        say("\nNo models found at %s. Is the server running, with models available?" % root)
        if not ask_yes("Try again?", True):
            say("You can type model ids by hand below.")
            break

    say("\nFor each role, type model numbers or ids.")
    taken = {}
    planner = pick_models("Planner (one model)", models, taken)[0]
    builders = pick_models("Builders (comma-separated, any number)", models, taken, many=True)
    reviewers = pick_models("Reviewers (comma-separated, may be empty)", models, taken, many=True, required=False)
    judges = ask_judges()
    agent = ask_agent(current_agent())

    say()
    runs_dir = ask("Runs folder", "runs")
    probe_tasks = list_tasks({})
    if probe_tasks:
        say("Tasks: " + ", ".join(probe_tasks))
    task = ask("Default task", "toy-bepay" if "toy-bepay" in probe_tasks else (probe_tasks[0] if probe_tasks else ""))

    cfg = {
        "task": task,
        "tasks_dir": "tasks",
        "runs_dir": runs_dir,
        "logs_dir": "logs",
        "results_dir": "results",
        "backend": backend,
        "proxy_port": proxy_port,
        "served_model_name": alias,
        "test_timeout_s": 600,
        "planner": planner,
        "builders": builders,
        "reviewers": reviewers,
        "judges": judges,
    }
    if agent:
        cfg["agent"] = agent
    validate_config(cfg)
    os.makedirs(os.path.dirname(os.path.abspath(CONFIG_PATH)), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)
    say("\nWrote %s" % CONFIG_PATH)
    steps = []
    agent_url = "http://127.0.0.1:%d/v1" % proxy_port
    if agent and agent["command"][0] == "cline":
        steps += ["Connect Cline to the proxy, once:",
                  "  cline auth --provider openai --apikey dummy --baseurl %s --modelid %s" % (agent_url, alias)]
    elif agent:
        steps.append("Point your agent at the proxy, once: base URL %s, model %s, any non-empty key" % (agent_url, alias))
    steps += ["Start the proxy in another window:  %s proxy/llm_proxy.py" % PY,
              "Then check your setup:             %s check" % CMD]
    if agent:
        steps.append("Then start an unattended run:      %s run" % CMD)
    next_step(steps)


# ----------------------------------------------------------------- commands

def cmd_check(cfg, _args):
    say("\nChecking your setup...\n")
    problems = 0

    if sys.version_info >= (3, 9):
        ok("Python %d.%d" % sys.version_info[:2])
    else:
        fail("Python 3.9 or newer is needed (you have %d.%d)" % sys.version_info[:2])
        problems += 1

    if shutil.which("git"):
        ok("Git: " + subprocess.run(["git", "--version"], capture_output=True, encoding="utf-8",
                                    errors="replace").stdout.strip())
    else:
        fail("Git is not installed")
        problems += 1

    if pytest_available():
        ok("pytest is installed (runs the hidden tests)")
    else:
        fail("pytest is not installed in this Python. Run:  %s -m pip install -r requirements.txt" % PY)
        problems += 1

    kind = backend_of(cfg).get("type", "lmstudio")
    if kind == "lmstudio":
        if lms_bin():
            ok("LM Studio CLI: " + lms_bin())
        else:
            warn("LM Studio CLI 'lms' not found. Models can't be loaded with a set context length "
                 "(see 'Backends' in GUIDE-MANUAL.md)")

    missing = [m["label"] for m in all_models(cfg) if not m.get("model") or m["model"] == PLACEHOLDER]
    if missing:
        fail("Model keys still missing in pipeline.json for: " + ", ".join(missing))
        problems += 1
    else:
        ok("All model keys filled in")

    try:
        ids = loaded_model_ids(cfg)
        stage = get_stage(cfg)
        loaded = ", ".join(ids) if ids else "nothing"
        if ids and stage.get("model") and uses_alias(cfg):
            loaded += " (= %s)" % stage["model"]
        ok("Model server is running at %s. It lists: %s" % (backend_root(cfg), loaded))
        offered = list_models(kind, backend_root(cfg), served_name(cfg), backend_headers(cfg))
        if not offered:
            warn("Could not list the server's models, so the model keys were not checked")
        elif not missing:
            for model in all_models(cfg):
                if model_listed(model["model"], offered):
                    ok("Available: %s (%s)" % (model["label"], model["model"]))
                else:
                    fail("Not offered by the server: %s (%s). Compare with:  %s models"
                         % (model["label"], model["model"], CMD))
                    problems += 1
    except (urllib.error.URLError, OSError, ValueError) as exc:
        fail(server_problem(cfg, exc))
        problems += 1

    try:
        health = http_json(proxy_url(cfg) + "/__health", timeout=5)
        ok("Logging proxy is running")
        if health.get("upstream") and health["upstream"] != backend_root(cfg):
            fail("The proxy forwards to %s, but the config says %s. Restart the proxy."
                 % (health["upstream"], backend_root(cfg)))
            problems += 1
    except (urllib.error.URLError, OSError, ValueError):
        fail("Logging proxy is not running. Start it in another window:  %s proxy/llm_proxy.py" % PY)
        problems += 1

    judges = cfg.get("judges", [])
    if not judges:
        warn("No judges configured, so the report will have no scores")
    for judge in judges:
        if judge["type"] == "api":
            if judge.get("api_key_env") and not os.environ.get(judge["api_key_env"]):
                warn("Judge '%s': environment variable %s is not set" % (judge["label"], judge["api_key_env"]))
            if judge.get("model") == PLACEHOLDER:
                fail("Judge '%s': the model id is still a placeholder" % judge["label"])
                problems += 1

    if cfg.get("agent"):
        tool = cfg["agent"]["command"][0]
        if shutil.which(tool):
            ok("Agent for unattended runs: " + tool)
        else:
            warn("Agent '%s' not found. Only '%s run' needs it." % (tool, CMD))

    ok("Runs folder: " + runs_root(cfg))
    tasks = list_tasks(cfg)
    if cfg.get("task") in tasks:
        ok("Task: " + cfg["task"])
    else:
        fail("Task '%s' not found. Available: %s" % (cfg.get("task"), ", ".join(tasks) or "none"))
        problems += 1

    say()
    if problems:
        say("%d problem(s) found. Fix the [FAIL] lines above, then run this check again.\n" % problems)
        sys.exit(1)
    say("Everything looks good.\n")


def cmd_models(cfg, _args):
    kind = backend_of(cfg).get("type", "lmstudio")
    ids = list_models(kind, backend_root(cfg), served_name(cfg), backend_headers(cfg))
    if not ids:
        die("No models found at %s. Is the server running?" % backend_root(cfg))
    say("\nModels on %s (copy the key into pipeline.json, or run 'init'):\n" % backend_root(cfg))
    for model_id in ids:
        say("  " + model_id)
    say()


def cmd_new_run(cfg, args):
    task = args.task or cfg.get("task")
    source = task_dir(cfg, task or "")
    if not task or not os.path.exists(os.path.join(source, "TASK.md")):
        die("Task '%s' not found. Available: %s" % (task, ", ".join(list_tasks(cfg)) or "none"))
    missing = [m["label"] for m in all_models(cfg) if not m.get("model") or m["model"] == PLACEHOLDER]
    if missing:
        die("Fill in the model keys in pipeline.json first (missing: %s). Tip:  %s init" % (", ".join(missing), CMD))
    if len(cfg["builders"]) < 2:
        warn("With one builder there is nothing to compare, and blinding has no effect.")

    root = runs_root(cfg)
    os.makedirs(root, exist_ok=True)
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M") + "-" + task
    n = 2
    while os.path.exists(os.path.join(root, run_id)):
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M") + "-" + task + "-" + str(n)
        n += 1
    path = os.path.join(root, run_id)
    os.makedirs(path)

    plan_dir = os.path.join(path, "1-plan")
    os.makedirs(plan_dir)
    shutil.copyfile(os.path.join(source, "TASK.md"), os.path.join(plan_dir, "TASK.md"))
    copy_prompt("plan", plan_dir)
    starter = os.path.join(source, "starter")
    if os.path.isdir(starter):
        copy_tree_filtered(starter, os.path.join(plan_dir, "starter"), SKIP_IN_COPY)
    init_stage_repo(plan_dir)

    backend = dict(backend_of(cfg))
    known = model_details(cfg)
    run = {
        "run_id": run_id,
        "task": task,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "platform": "%s %s" % (platform.system(), platform.machine()),
        "python": platform.python_version(),
        "harness_commit": harness_commit(),
        "machine": machine_info(),
        "hashes": {
            "task": tree_hash(source),
            "prompts": tree_hash(os.path.join(HERE, "prompts")),
            "scoring": tree_hash(os.path.join(HERE, "scoring")),
        },
        "backend": {"type": backend.get("type", "lmstudio"),
                    "context_length": backend.get("context_length"), "gpu": backend.get("gpu")},
        "context_controlled": uses_alias(cfg),
        "test_timeout_s": int(cfg.get("test_timeout_s", 600)),
        "request_overrides": cfg.get("request_overrides") or {},
        "model_details": {m["label"]: known[m["model"]] for m in all_models(cfg) if m["model"] in known},
        "planner": cfg["planner"],
        "builders": cfg["builders"],
        "reviewers": cfg.get("reviewers", []),
        "judges": cfg.get("judges", []),
        "agent": cfg.get("agent"),
    }
    with open(os.path.join(path, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=2)
    with open(os.path.join(root, "CURRENT"), "w", encoding="utf-8") as fh:
        fh.write(run_id + "\n")

    say("\nNew run created: %s" % run_id)
    say("Folder: %s" % path)
    next_step(["%s next      (starts the plan stage)" % CMD])


def _pending_step(path, run):
    """What comes next: ('stage', stage-tuple), or the name of a harness step and its details."""
    stages = model_stages(path, run)
    failed = failed_stages(path)
    plan = stages[0]
    if not stage_done(plan[3], plan[1]):
        return "stage", plan
    if not os.path.exists(os.path.join(path, "plan.md")):
        return "freeze", None
    for stage in stages:
        if stage[3] == "build" and not stage_done(stage[3], stage[1]) and stage[0] not in failed:
            return "stage", stage
    if not os.path.exists(os.path.join(path, "tests", "summary.json")):
        return "test", None
    if not read_blind_key(path):
        return "blind", None
    for stage in stages:
        if stage[3] == "review" and not stage_done(stage[3], stage[1]) and stage[0] not in failed:
            return "stage", stage
    judge_dir = os.path.join(path, "4-judging")
    if not os.path.exists(os.path.join(judge_dir, "judge-packet.md")):
        return "packet", None
    open_judges = [j for j in run.get("judges", [])
                   if not _judge_scores(path, j, read_blind_key(path))["complete"]]
    if open_judges:
        return "judges", open_judges
    return "report", None


def _next_action(path, run):
    """Return ('stage', stage-tuple) for a model stage to start, or ('cmd', [lines]) otherwise."""
    step, detail = _pending_step(path, run)
    if step == "stage":
        return "stage", detail
    if step == "freeze":
        return "cmd", ["Read 1-plan/plan.md (fix obvious problems by hand if needed), then run:",
                       "  %s freeze" % CMD]
    if step == "judges":
        lines = []
        if any(j["type"] == "api" for j in detail):
            lines.append("%s judge      (calls the api judges)" % CMD)
        for judge in detail:
            if judge["type"] == "paste":
                lines.append("Paste %s's answer into %s" % (judge["label"], _score_file(path, judge["label"])))
            elif judge["type"] == "human":
                lines.append("Have %s fill in %s" % (judge["label"], _score_file(path, judge["label"])))
        lines.append("Then run:  %s report" % CMD)
        return "cmd", lines
    return "cmd", ["%s %s" % (CMD, step)]


def cmd_next(cfg, _args):
    run_id, path, run = current_run(cfg)
    kind, what = _next_action(path, run)
    if kind == "stage":
        name, folder, label, stage_kind = what
        if stage_kind == "build" and os.path.isdir(folder) and commit_count(folder) == 1 \
                and get_stage(cfg).get("stage") == name:
            warn("Build '%s' has no commits since the starting point. If the model finished, "
                 "commit its work first. Restarting this stage." % label)
        start_stage(cfg, run_id, path, run, name, folder, label, stage_kind)
    else:
        next_step(what)


def cmd_start(cfg, args):
    run_id, path, run = current_run(cfg)
    for name, folder, label, kind in model_stages(path, run):
        if name == args.label:
            start_stage(cfg, run_id, path, run, name, folder, label, kind)
            return
    names = ", ".join(s[0] for s in model_stages(path, run))
    die("No stage called '%s' in this run.\nStages: %s" % (args.label, names))


def cmd_status(cfg, _args):
    run_id, path, run = current_run(cfg)
    say("\nCurrent run: %s\nFolder:      %s" % (run_id, path))
    stage = get_stage(cfg)
    if stage.get("run_id") == run_id:
        say("Active stage: %s (model: %s)" % (stage.get("stage"), stage.get("model")))
    say()

    def line(done, text):
        print("  [%s] %s" % ("x" if done else " ", text))

    stages = model_stages(path, run)
    failed = failed_stages(path)
    line(stage_done("plan", stages[0][1]), "Plan written by %s" % run["planner"]["label"])
    line(os.path.exists(os.path.join(path, "plan.md")), "Plan frozen, build folders created")
    for name, folder, label, kind in stages:
        if kind == "build":
            line(stage_done(kind, folder), "Build by %s committed%s"
                 % (label, " (FAILED)" if name in failed else ""))
    line(os.path.exists(os.path.join(path, "tests", "summary.json")), "Hidden tests run")
    line(bool(read_blind_key(path)), "Builds anonymised, review folders created")
    for name, folder, label, kind in stages:
        if kind == "review":
            line(stage_done(kind, folder), "Review written: %s%s"
                 % (name[len("review-"):], " (FAILED)" if name in failed else ""))
    line(os.path.exists(os.path.join(path, "4-judging", "judge-packet.md")), "Judge packet created")
    key = read_blind_key(path)
    judges = run.get("judges", [])
    complete = [j for j in judges if key and _judge_scores(path, j, key)["complete"]]
    line(bool(judges) and len(complete) == len(judges), "Judge scores complete (%d of %d)" % (len(complete), len(judges)))
    line(os.path.exists(os.path.join(path, "REPORT.md")), "Report written")

    kind, what = _next_action(path, run)
    if kind == "stage":
        next_step(["%s next      (starts stage: %s)" % (CMD, what[0])])
    else:
        next_step(what)


def cmd_load(cfg, args):
    ensure_loaded(cfg, cfg, args.label)
    model_id = None if uses_alias(cfg) else find_model(cfg, args.label)["model"]
    set_stage(cfg, "manual", "manual-" + args.label, args.label, model_id)
    say("Calls are logged as stage 'manual-%s' until you start a run stage.\n" % args.label)


def cmd_smoke(cfg, args):
    label = args.label or get_stage(cfg).get("model")
    if not label:
        die("Load a model first, e.g.:  %s load %s" % (CMD, cfg["planner"]["label"]))
    url = proxy_url(cfg) + "/run/smoke/%s/v1/chat/completions" % label
    payload = {
        "model": served_name(cfg) if uses_alias(cfg) else find_model(cfg, label)["model"],
        "max_tokens": 600,
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "Reply with exactly these two words: pipeline OK"}],
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer relaybench",
    })
    say("\nSending a test message to '%s' through the proxy..." % label)
    text = []
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                raw = raw.strip()
                if not raw.startswith(b"data:"):
                    continue
                try:
                    event = json.loads(raw[5:].strip())
                except ValueError:
                    continue
                for choice in event.get("choices") or []:
                    text.append((choice.get("delta") or {}).get("content") or "")
    except urllib.error.HTTPError as exc:
        die("The model returned HTTP %s: %s" % (exc.code, exc.read()[:500].decode("utf-8", "replace")))
    except (urllib.error.URLError, OSError) as exc:
        die("Could not reach the proxy (%s). Is it running?" % exc)

    reply = "".join(text).strip()
    say("Model replied: %s" % (reply[:200] if reply else "(no text, it may have used all tokens on thinking)"))
    entry = None
    for _attempt in range(30):  # the proxy writes its log just after the reply ends
        for rec in _read_log(cfg):
            if rec.get("run_id") == "smoke" and rec.get("stage") == label and rec.get("epoch", 0) >= started - 1:
                entry = rec
        if entry:
            break
        time.sleep(0.1)
    if entry:
        say("Proxy logged: input %s tokens, output %s tokens, first token after %.1f s, %.1f tokens/sec"
            % (entry.get("input_tokens"), entry.get("output_tokens"),
               entry.get("ttft_s") or 0, entry.get("gen_tps") or 0))
        say("\nSmoke test passed.\n")
    else:
        warn("The reply came back but nothing was logged. Is the proxy log at %s?" % log_file(cfg))
        sys.exit(1)


def cmd_freeze(cfg, args):
    run_id, path, run = current_run(cfg)
    source = os.path.join(path, "1-plan", "plan.md")
    if not os.path.exists(source):
        die("1-plan/plan.md does not exist yet. Finish the plan stage first.")
    if os.path.getsize(source) < 300:
        warn("plan.md is very short (%d bytes). Check it before continuing." % os.path.getsize(source))
    folders = [build_folder(path, run, b["label"]) for b in run["builders"]]
    if any(os.path.exists(f) for f in folders) and not args.force:
        die("Build folders already exist. Use 'freeze --force' to set them aside and recreate them.")
    move_to_stale(path, [os.path.basename(f) for f in folders] + _outputs_after_build(path))
    _forget_stage_results(path, lambda stage: stage != "plan")

    frozen = os.path.join(path, "plan.md")
    if os.path.exists(frozen):
        os.chmod(frozen, stat.S_IRUSR | stat.S_IWUSR)
    shutil.copyfile(source, frozen)
    os.chmod(frozen, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    task = task_dir(cfg, run["task"])
    starter = os.path.join(task, "starter")
    for folder in folders:
        if os.path.isdir(starter):
            shutil.copytree(starter, folder, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            os.makedirs(folder)
        shutil.copyfile(os.path.join(path, "1-plan", "TASK.md"), os.path.join(folder, "TASK.md"))
        shutil.copyfile(source, os.path.join(folder, "plan.md"))
        copy_prompt("build", folder)
        init_stage_repo(folder)
        git(folder, "add", "-A")
        git(folder, "commit", "-q", "-m", "Starting point: task, plan and prompt")

    say("\nPlan frozen: %s" % frozen)
    say("Created %d build folder(s), each with its own Git repo:" % len(run["builders"]))
    for builder, folder in zip(run["builders"], folders):
        say("  %s  (%s)" % (folder, builder["label"]))
    next_step(["%s next      (starts the first build stage)" % CMD])


def _outputs_after_build(path):
    return ["tests", "blind-key.json", "4-judging", "REPORT.md"] + \
        [n for n in os.listdir(path) if n.startswith("3-review-")]


def pytest_available():
    return importlib.util.find_spec("pytest") is not None


def _kill_tree(proc, grace_s=0):
    """Stop a process started in its own session, with everything it started."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        proc.kill()
        proc.wait()
        return
    if grace_s:  # some wrappers, such as `docker run`, need the signal to stop what they started
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=grace_s)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    proc.wait()


def _run_logged(command, cwd, env, output_path, timeout):
    """Run a command with its output in a file: a pipe would hang on a process the build left running."""
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    with open(output_path, "wb") as out:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, **kwargs)
        try:
            proc.wait(timeout=timeout)
            return False
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return True


def _pytest_command(*extra):
    return [sys.executable, "-m", "pytest", ".", "-q", "-p", "no:cacheprovider", "--rootdir", "."] + list(extra)


def _expected_test_count(hidden, env_base, timeout):
    """How many tests the task has. Hidden tests guard their imports, so no build is needed to collect them."""
    work = tempfile.mkdtemp(prefix="relaybench-collect-")
    try:
        copy = os.path.join(work, "hidden")
        shutil.copytree(hidden, copy, ignore=shutil.ignore_patterns("__pycache__"))
        empty = os.path.join(work, "build")
        os.makedirs(empty)
        output = os.path.join(work, "collect.txt")
        env = dict(env_base, BUILD_DIR=empty)
        _run_logged(_pytest_command("--collect-only"), copy, env, output, timeout)
        with open(output, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        count = sum(1 for line in lines if "::" in line and not line.startswith(("ERROR", "FAILED")))
        return count or None
    finally:
        force_rmtree(work)


def _parse_junit(path):
    """Results from pytest's XML report. `collection_error` means tests were lost, not failed."""
    result = {"ran": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failing": [],
              "collection_error": False}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return result
    for case in root.iter("testcase"):
        cls = (case.get("classname") or "").split(".")[-1]
        name = "%s.%s" % (cls, case.get("name")) if cls else case.get("name")
        failure, error, skipped = case.find("failure"), case.find("error"), case.find("skipped")
        if error is not None and "collection failure" in (error.get("message") or ""):
            result["collection_error"] = True
            continue
        result["ran"] += 1
        if error is not None:
            result["errors"] += 1
            result["failing"].append(name)
        elif failure is not None:
            result["failed"] += 1
            result["failing"].append(name)
        elif skipped is not None:
            result["skipped"] += 1
        else:
            result["passed"] += 1
    return result


def _run_own_tests(folder, env, timeout, output_path):
    """A build's own tests, on a fresh copy. Judges score them, and reading alone can't tell whether they pass."""
    work = tempfile.mkdtemp(prefix="relaybench-own-")
    try:
        copy = os.path.join(work, "build")
        copy_tree_filtered(folder, copy, SKIP_IN_COPY)
        junit = os.path.join(work, "junit.xml")
        timed_out = _run_logged(_pytest_command("--junitxml", junit), copy, env, output_path, timeout)
        res = _parse_junit(junit)
    finally:
        force_rmtree(work)
    res["timeout"] = timed_out
    return res


def _own_tests_text(res):
    own = res.get("own_tests")
    if not own:  # no build, or a run from before these were measured
        return "–"
    if own.get("timeout"):
        return "timed out"
    if not own.get("ran"):
        return "could not be collected" if own.get("collection_error") else "none found"
    extra = ", some could not be collected" if own.get("collection_error") else ""
    return "%d of %d passed%s" % (own.get("passed", 0), own["ran"], extra)


def _passed_text(res):
    total = res.get("expected") or res.get("ran", "?")
    flags = [word for word, on in (("no build: the agent failed", res.get("no_build")),
                                   ("timeout", res.get("timeout")),
                                   ("collection error", res.get("collection_error")),
                                   ("incomplete", res.get("incomplete"))) if on]
    return "%s of %s%s" % (res.get("passed", "?"), total, " (%s)" % ", ".join(flags) if flags else "")


def cmd_test(cfg, args):
    run_id, path, run = current_run(cfg)
    if not pytest_available():
        die("pytest is not installed in this Python (%s).\nRun:  %s -m pip install -r requirements.txt"
            % (sys.executable, PY))
    task = task_dir(cfg, run["task"])
    hidden = os.path.join(task, "hidden_tests")
    if not os.path.isdir(hidden):
        die("No hidden tests found at %s" % hidden)
    if tree_hash(task) != run.get("hashes", {}).get("task"):
        warn("The task folder has changed since this run started. Results may not compare.")
    timeout = int(cfg.get("test_timeout_s", 600))
    out_dir = os.path.join(path, "tests")
    os.makedirs(out_dir, exist_ok=True)
    base_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    expected = _expected_test_count(hidden, base_env, min(timeout, 120))
    summary = {}
    show = args is not None and args.show
    no_build = failed_builders(path, run)
    say("\nRunning hidden tests...\n")
    for builder in run["builders"]:
        label = builder["label"]
        folder = build_folder(path, run, label)
        if label in no_build:
            # Testing it would score the starter code, which can pass tests on its own.
            summary[label] = {"ran": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "failing": [],
                              "collection_error": False, "expected": expected, "timeout": False,
                              "incomplete": False, "no_build": True}
            continue
        if not os.path.isdir(folder):
            warn("%s: build folder missing, skipped" % label)
            continue
        if commit_count(folder) <= 1:
            warn("%s: no commits since the starting point. Did the build finish?" % label)
        try:
            if git(folder, "status", "--porcelain").stdout.strip():
                warn("%s: has uncommitted changes. Tests and judges see the working tree." % label)
        except (subprocess.CalledProcessError, OSError):
            pass
        # Fresh copies per build: tests may write files, and neither the task nor the next build should see them.
        work = tempfile.mkdtemp(prefix="relaybench-test-")
        try:
            build_copy = os.path.join(work, "build")
            hidden_copy = os.path.join(work, "hidden")
            copy_tree_filtered(folder, build_copy, SKIP_IN_COPY)
            shutil.copytree(hidden, hidden_copy, ignore=shutil.ignore_patterns("__pycache__"))
            junit = os.path.join(work, "junit.xml")
            output_path = os.path.join(out_dir, label + ".txt")
            timed_out = _run_logged(_pytest_command("--junitxml", junit), hidden_copy,
                                    dict(base_env, BUILD_DIR=build_copy), output_path, timeout)
            res = _parse_junit(junit)
        finally:
            force_rmtree(work)
        res["expected"] = expected
        res["timeout"] = timed_out
        res["incomplete"] = bool(expected and res["ran"] != expected)
        res["own_tests"] = _run_own_tests(folder, base_env, timeout, os.path.join(out_dir, label + "-own.txt"))
        summary[label] = res
        if timed_out:
            with open(output_path, "a", encoding="utf-8") as fh:
                fh.write("\nTIMEOUT after %d seconds\n" % timeout)
        if show:
            say("  %-24s %s%s  (own tests: %s)" % (label, _passed_text(res),
                                                   ("   failing: " + ", ".join(res["failing"])) if res["failing"] else "",
                                                   _own_tests_text(res)))
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    if not show:
        troubled = sum(1 for r in summary.values() if r["timeout"] or r["collection_error"] or r["incomplete"])
        say("  Tested %d build(s). Scores stay hidden to keep you blind until the report." % len(summary))
        if troubled:
            warn("%d build(s) timed out or lost tests. To look anyway:  %s test --show" % (troubled, CMD))
    say("\nFull output saved in: %s  (opening it un-blinds you)" % out_dir)
    next_step(["%s blind" % CMD])


def cmd_blind(cfg, args):
    run_id, path, run = current_run(cfg)
    key_path = os.path.join(path, "blind-key.json")
    if os.path.exists(key_path) and not args.force:
        die("Builds are already anonymised. Use 'blind --force' to redo it "
            "(the reviews and scores are set aside, because the letters change).")
    for builder in run["builders"]:
        if not os.path.isdir(build_folder(path, run, builder["label"])):
            die("Build folder for %s is missing. Run 'freeze' and the build stages first." % builder["label"])
    move_to_stale(path, ["blind-key.json", "4-judging", "REPORT.md"]
                  + [n for n in os.listdir(path) if n.startswith("3-review-")])

    no_build = failed_builders(path, run)
    labels = [b["label"] for b in run["builders"] if b["label"] not in no_build]
    if not labels:
        die("No build succeeded, so there is nothing to anonymise.")
    _forget_stage_results(path, lambda stage: stage.startswith("review-"))
    random.shuffle(labels)
    builds = {build_letter(i): label for i, label in enumerate(labels)}
    reviewers = {"reviewer-%d" % (i + 1): r["label"] for i, r in enumerate(run["reviewers"])}

    frozen_task = os.path.join(path, "1-plan", "TASK.md")
    frozen_plan = os.path.join(path, "plan.md")
    warnings = []
    known_labels = [m["label"] for m in all_models(run)]
    for letter in sorted_letters(builds):
        folder = build_folder(path, run, builds[letter])
        for name, frozen in (("TASK.md", frozen_task), ("plan.md", frozen_plan)):
            if not same_file_content(os.path.join(folder, name), frozen):
                warnings.append("Build %s changed %s. Reviewers and judges get the frozen copy." % (letter, name))
        for rel in files_mentioning_labels(folder, known_labels):
            warnings.append("Build %s: `%s` mentions a model label, which can un-blind it." % (letter, rel))

    with open(key_path, "w", encoding="utf-8") as fh:
        json.dump({"builds": builds, "reviewers": reviewers, "warnings": warnings}, fh, indent=2)

    starter = os.path.join(task_dir(cfg, run["task"]), "starter")
    for r_index, reviewer in enumerate(run["reviewers"], start=1):
        for letter in sorted_letters(builds):
            src = build_folder(path, run, builds[letter])
            dest = os.path.join(path, "3-review-%s-by-reviewer-%d" % (letter, r_index))
            copy_tree_filtered(src, dest, SKIP_IN_COPY)
            shutil.copyfile(frozen_task, os.path.join(dest, "TASK.md"))
            shutil.copyfile(frozen_plan, os.path.join(dest, "plan.md"))
            readme = os.path.join(dest, "README.md")
            if os.path.exists(readme) and is_unchanged_starter(readme, "README.md", starter):
                os.remove(readme)
            copy_prompt("review", dest)
            init_stage_repo(dest)

    for warning in warnings:
        warn(warning)
    say("\nBuilds anonymised as %s. The key is saved in blind-key.json (don't share it with the judges)."
        % ", ".join("Build " + letter for letter in sorted_letters(builds)))
    say("Created %d review folder(s)." % (len(builds) * len(run["reviewers"])))
    if run["reviewers"]:
        next_step(["%s next      (starts the first review stage)" % CMD])
    else:
        next_step(["%s packet" % CMD])


def _code_files(folder, starter):
    """The files to show judges, and the names of starter files the builder left unchanged."""
    files, unchanged = [], []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not is_skipped(d, SKIP_IN_PACKET))
        for name in sorted(names):
            if is_skipped(name, SKIP_IN_PACKET) or name.endswith((".pyc", ".png", ".jpg", ".zip")):
                continue
            full = os.path.join(root, name)
            if os.path.getsize(full) > 100000:
                continue
            rel = os.path.relpath(full, folder)
            if starter and is_unchanged_starter(full, rel, starter):
                if name != "README.md":
                    unchanged.append(rel.replace(os.sep, "/"))
                continue
            files.append(full)
    return files, unchanged


LANG = {".py": "python", ".md": "markdown", ".json": "json", ".js": "javascript", ".ts": "typescript",
        ".html": "html", ".css": "css", ".sh": "bash", ".toml": "toml", ".yml": "yaml", ".yaml": "yaml"}


def _score_file(path, label):
    return os.path.join(path, "4-judging", "scores-%s.md" % label)


def _human_template(key):
    letters = sorted_letters(key["builds"])
    data = {
        "builds": {letter: {c: None for c in CRITERIA} for letter in letters},
        "reviews": {"%s/%s" % (letter, reviewer): {f: None for f in REVIEW_FIELDS}
                    for reviewer in sorted(key["reviewers"]) for letter in letters},
        "preferred_build": None,
        "one_line_verdict": "",
    }
    return ("# Judge scores\n\n"
            "Read `judge-packet.md` and score it with the scoring sheet it contains.\n"
            "Don't open `blind-key.json` or the run's `tests` folder before you finish.\n"
            "Replace every `null` below with a number (and fill in the verdict), then save.\n\n"
            "## Notes\n\n\n"
            "## Scores (JSON)\n\n```json\n%s\n```\n" % json.dumps(data, indent=2))


def cmd_packet(cfg, _args):
    run_id, path, run = current_run(cfg)
    key = read_blind_key(path)
    if not key:
        die("Run '%s blind' first." % CMD)
    if not os.path.exists(os.path.join(path, "tests", "summary.json")):
        say("Hidden tests have not been run yet; running them now.")
        cmd_test(cfg, None)
    with open(os.path.join(path, "tests", "summary.json"), encoding="utf-8") as fh:
        tests = json.load(fh)

    def read(p):
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()

    def fenced(rel, full):
        lang = LANG.get(os.path.splitext(rel)[1], "")
        with open(full, encoding="utf-8", errors="replace") as fh:
            rows = fh.read().rstrip("\n").split("\n")  # not read(): stripping leading lines would shift the numbers
        width = len(str(len(rows)))
        numbered = "\n".join("%*d | %s" % (width, n, row) for n, row in enumerate(rows, start=1))
        return ["### `%s`" % rel.replace(os.sep, "/"), "", "````" + lang, numbered, "````", ""]

    source = task_dir(cfg, run["task"])
    starter = os.path.join(source, "starter")
    parts = ["# Judge packet", "", read(os.path.join(HERE, "prompts", "judge.md")), "",
             "---", "", read(os.path.join(HERE, "scoring", "scoresheet.md")), "",
             "---", "", "# The task", "", read(os.path.join(path, "1-plan", "TASK.md")), ""]
    if os.path.isdir(starter):
        starter_files = [f for f in _code_files(starter, None)[0] if os.path.basename(f) != "README.md"]
        if starter_files:
            parts += ["---", "", "# Starter code", "", "Every build started from these files.", ""]
            for full in starter_files:
                parts += fenced(os.path.relpath(full, starter), full)
    parts += ["---", "", "# The plan", "", read(os.path.join(path, "plan.md")), ""]

    labels = [m["label"] for m in all_models(run)]
    missing_reviews = []
    leaks = []
    reviews = []
    for letter in sorted_letters(key["builds"]):
        label = key["builds"][letter]
        folder = build_folder(path, run, label)
        res = tests.get(label, {})
        section = ["---", "", "# Build %s" % letter, "",
                   "## Hidden acceptance tests", "",
                   "Passed **%s**." % _passed_text(res)]
        if res.get("failing"):
            section += ["", "Failing tests: " + ", ".join("`%s`" % t for t in res["failing"])]
        if res.get("own_tests"):
            section += ["", "## Own tests", "", "The harness ran this build's own tests: **%s**." % _own_tests_text(res)]
            if res["own_tests"].get("failing"):
                section += ["", "Failing: " + ", ".join("`%s`" % t for t in res["own_tests"]["failing"])]
        files, unchanged = _code_files(folder, starter)
        section += ["", "## Files", ""]
        if unchanged:
            section += ["Unchanged from the starter code: " + ", ".join("`%s`" % u for u in unchanged), ""]
        for full in files:
            section += fenced(os.path.relpath(full, folder), full)
        build_reviews = []
        for r_index in range(1, len(key["reviewers"]) + 1):
            review_file = os.path.join(path, "3-review-%s-by-reviewer-%d" % (letter, r_index), "REVIEW.md")
            build_reviews += ["## Review %s/reviewer-%d" % (letter, r_index), ""]
            if os.path.exists(review_file):
                build_reviews += [read(review_file), ""]
            else:
                build_reviews += ["(No review was written.)", ""]
                missing_reviews.append("%s/reviewer-%d" % (letter, r_index))
        if find_label_text("\n".join(section + build_reviews), labels):
            leaks.append(letter)
        parts += section
        reviews += build_reviews
    if reviews:
        # Apart from the builds, so that a review doesn't colour the first reading of the code it is about.
        parts += ["---", "", "# Reviews", "", "Score every build above before you score these.", ""] + reviews

    judge_dir = os.path.join(path, "4-judging")
    os.makedirs(judge_dir, exist_ok=True)
    packet = os.path.join(judge_dir, "judge-packet.md")
    with open(packet, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts) + "\n")
    for judge in run.get("judges", []):
        score_file = _score_file(path, judge["label"])
        if os.path.exists(score_file):
            continue
        if judge["type"] == "paste":
            with open(score_file, "w", encoding="utf-8") as fh:
                fh.write("<!-- Paste the complete answer from %s below this line, then save. -->\n\n" % judge["label"])
        elif judge["type"] == "human":
            with open(score_file, "w", encoding="utf-8") as fh:
                fh.write(_human_template(key))

    if missing_reviews:
        warn("These reviews are missing and marked as such in the packet: " + ", ".join(missing_reviews))
    if leaks:
        warn("The packet sections of %s mention a model label. Check them before judging, "
             "because this can un-blind the judges." % ", ".join("Build " + x for x in leaks))
    set_stage(cfg, run_id, "judging", None)
    say("\nJudge packet created: %s" % packet)
    lines = []
    for judge in run.get("judges", []):
        score_file = _score_file(path, judge["label"])
        if judge["type"] == "api":
            lines.append("%s: run  %s judge" % (judge["label"], CMD))
        elif judge["type"] == "paste":
            lines += ["%s: in a NEW chat, attach judge-packet.md, send 'Follow the instructions in this file.'" % judge["label"],
                      "  and paste the complete answer into %s" % score_file]
        else:
            lines.append("%s: fill in %s" % (judge["label"], score_file))
    lines.append("Then:  %s report" % CMD)
    next_step(lines)


def cmd_judge(cfg, args):
    run_id, path, run = current_run(cfg)
    key = read_blind_key(path)
    packet = os.path.join(path, "4-judging", "judge-packet.md")
    if not key or not os.path.exists(packet):
        die("Create the judge packet first:  %s packet" % CMD)
    with open(packet, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    failed = 0
    called = 0
    for judge in run.get("judges", []):
        if judge["type"] != "api":
            continue
        label = judge["label"]
        score_file = _score_file(path, label)
        if not args.force and _judge_scores(path, judge, key)["complete"]:
            say("%s: already scored. Use 'judge --force' to ask again." % label)
            continue
        if judge["model"] == PLACEHOLDER:
            fail("%s: the model id is still a placeholder." % label)
            failed += 1
            continue
        api_key = os.environ.get(judge.get("api_key_env") or "")
        if judge.get("api_key_env") and not api_key:
            fail("%s: set the environment variable %s first." % (label, judge["api_key_env"]))
            failed += 1
            continue
        url = judge["base_url"].rstrip("/") + "/chat/completions"
        say("%s: sending the packet (%d KB) to %s, model %s..."
            % (label, len(text) // 1024, urlsplit(url).netloc, judge["model"]))
        payload = {"model": judge["model"], "messages": [{"role": "user", "content": text}]}
        if judge.get("max_tokens"):
            payload["max_tokens"] = judge["max_tokens"]
        headers = {"Authorization": "Bearer " + api_key} if api_key else {}
        called += 1
        try:
            answer = http_json(url, payload, timeout=1800, headers=headers)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            fail("%s: HTTP %s: %s" % (label, exc.code, exc.read()[:500].decode("utf-8", "replace")))
            failed += 1
            continue
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            fail("%s: no usable answer (%s)" % (label, exc))
            failed += 1
            continue
        with open(score_file, "w", encoding="utf-8") as fh:
            fh.write((answer or "").strip() + "\n")
        state = _judge_scores(path, judge, key)
        if state["complete"]:
            ok("%s: scores saved in %s" % (label, score_file))
        else:
            fail("%s: the answer has no complete scores (%s). It is saved in %s"
                 % (label, ", ".join(state["problems"]) or "no JSON block", score_file))
            failed += 1
    if not called and not failed:
        say("No api judges to call.")
    if failed:
        sys.exit(1)
    kind, what = _next_action(path, run)
    if kind == "cmd":
        next_step(what)


def _valid_score(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= value <= 5


def _judge_scores(path, judge, key):
    """What one judge's file gives us. Null, missing or out-of-range scores don't count as scored."""
    state = {"data": None, "complete": False, "usable": False, "problems": []}
    score_file = _score_file(path, judge["label"])
    if not key or not os.path.exists(score_file):
        return state
    data = _parse_scores(score_file)
    if not isinstance(data, dict) or not isinstance(data.get("builds"), dict):
        return state
    state["data"] = data
    for letter in sorted_letters(key["builds"]):
        scores = data["builds"].get(letter)
        if isinstance(scores, dict) and all(_valid_score(scores.get(c)) for c in CRITERIA):
            state["usable"] = True
        else:
            state["problems"].append("Build %s incomplete" % letter)
    state["complete"] = state["usable"] and not state["problems"]
    return state


def _parse_scores(score_file):
    with open(score_file, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    found = _find_scores_json(text)
    if found is None:  # retry with typographic quotes and non-breaking spaces made plain
        found = _find_scores_json(text.replace(" ", " ").replace("“", '"').replace("”", '"'))
    return found


def _find_scores_json(text):
    # Chat apps don't always keep the ```json fence when copying, so look for any JSON object
    # with a "builds" key anywhere in the text, fenced or not. The last one wins.
    decoder = json.JSONDecoder()
    found = None
    pos = text.find("{")
    while pos != -1:
        try:
            data, end = decoder.raw_decode(text, pos)
        except ValueError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(data, dict) and "builds" in data:
            found = data
        pos = text.find("{", end)
    return found


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _f(value, digits=1):
    return "–" if value is None else ("%." + str(digits) + "f") % value


def _cell(value):
    return "–" if value is None else str(value)


def _read_log(cfg):
    rows = []
    if os.path.exists(log_file(cfg)):
        with open(log_file(cfg), encoding="utf-8", errors="replace") as fh:
            for row in fh:
                try:
                    rows.append(json.loads(row))
                except ValueError:
                    continue
    return rows


def _generation_speed(recs):
    """Tokens per second while generating, leaving out the wait for the first token."""
    timed = [r for r in recs if r.get("output_tokens") and r.get("ttft_s") is not None and r.get("total_s")]
    gen_time = sum(r["total_s"] - r["ttft_s"] for r in timed)
    return sum(r["output_tokens"] for r in timed) / gen_time if gen_time > 0 else None


def _distinct(values):
    seen = []
    for value in values:
        if value is not None and value not in seen:
            seen.append(value)
    return ", ".join(str(v) for v in seen) or "–"


def cmd_report(cfg, _args):
    run_id, path, run = current_run(cfg)
    key = read_blind_key(path) or {"builds": {}, "reviewers": {}}
    letter_of = {label: letter for letter, label in key["builds"].items()}
    no_build = failed_builders(path, run)

    def stage_name(stage):
        if stage == "plan":
            return "Plan (%s)" % run["planner"]["label"]
        if stage.startswith("build-"):
            label = stage[len("build-"):]
            return "Build %s (%s)" % (letter_of.get(label, "–" if label in no_build else "?"), label)
        match = re.match(r"review-([A-Z]+)-by-(reviewer-\d+)", stage)
        if match:
            return "Review of %s (%s) by %s" % (match.group(1), key["builds"].get(match.group(1), "?"),
                                                 key["reviewers"].get(match.group(2), match.group(2)))
        return stage

    entries = [rec for rec in _read_log(cfg) if rec.get("run_id") == run_id]
    results = _read_stage_results(path)

    order = []
    by_stage = {}
    for rec in entries:
        if rec["stage"] not in by_stage:
            order.append(rec["stage"])
            by_stage[rec["stage"]] = []
        by_stage[rec["stage"]].append(rec)

    attempts = {}
    history = os.path.join(path, "stage-history.jsonl")
    if os.path.exists(history):
        with open(history, encoding="utf-8") as fh:
            for row in fh:
                try:
                    name = json.loads(row).get("stage")
                except ValueError:
                    continue
                attempts[name] = attempts.get(name, 0) + 1

    machine = run.get("machine") or {}
    hardware = [machine.get("cpu"), "%s GB" % machine["memory_gb"] if machine.get("memory_gb") else None]
    hardware = ", ".join(h for h in hardware if h) or "not reported"
    details = run.get("model_details") or {}
    limits = ["hidden tests %s s" % run.get("test_timeout_s", "?")]
    agent_settings = run.get("agent") or {}
    context = run.get("backend", {}).get("context_length")
    context_text = "%s tokens" % context if run.get("context_controlled") else "not controlled by the harness (server setting)"
    hashes = run.get("hashes", {})
    lines = ["# Run report: %s" % run_id, "",
             "- Task: `%s`" % run["task"],
             "- Started: %s" % run["created"],
             "- Platform: %s, Python %s" % (run.get("platform", "?"), run.get("python", "?")),
             "- Mode: %s" % ("unattended, with `%s`" % shlex.join((run.get("agent") or {}).get("command", ["?"]))
                             if results else "manual"),
             *(["- Agent version: %s" % (run.get("agent_version") or "not reported")] if results else []),
             "- Hardware: %s" % hardware,
             "- Backend: %s" % run.get("backend", {}).get("type", "?"),
             "- Quantisation: %s" % ("; ".join("%s: %s" % item for item in sorted(details.items())) or "not reported"),
             "- Context length: %s" % context_text,
             "- Harness commit: %s · Task hash: %s · Prompts hash: %s · Scoring hash: %s" % (
                 run.get("harness_commit") or "–", hashes.get("task", "–"),
                 hashes.get("prompts", "–"), hashes.get("scoring", "–")),
             "- Planner: %s · Builders: %s · Reviewers: %s" % (
                 run["planner"]["label"],
                 ", ".join(b["label"] for b in run["builders"]),
                 ", ".join(r["label"] for r in run["reviewers"]) or "none")]
    if results:
        limits = ["stage %s s" % agent_settings.get("stage_timeout_s", AGENT_DEFAULTS["stage_timeout_s"]),
                  "idle %s s" % agent_settings.get("idle_timeout_s", AGENT_DEFAULTS["idle_timeout_s"])] + limits
    lines += ["- Limits: " + ", ".join(limits), "", "## Speed per stage (from the proxy log)", ""]
    any_estimate = False
    if order:
        lines += ["| Stage | Attempts | Model calls | Errors | Input tokens | Output tokens | Median time to first token | "
                  "Generation speed | Model time | Wall time |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for stage in order:
            recs = by_stage[stage]
            errors = sum(1 for r in recs if (r.get("status") or 0) >= 400)
            inp = sum(r.get("input_tokens") or 0 for r in recs)
            out = sum(r.get("output_tokens") or 0 for r in recs)
            estimated = any(r.get("usage_source") == "chunks" for r in recs)
            any_estimate = any_estimate or estimated
            model_time = sum(r.get("total_s") or 0 for r in recs)
            wall = (max(r["epoch"] + (r.get("total_s") or 0) for r in recs) - min(r["epoch"] for r in recs))
            lines.append("| %s | %s | %d | %d | %s | %s%s | %s s | %s tok/s | %s min | %s min |" % (
                stage_name(stage), attempts.get(stage, "–"), len(recs), errors, "{:,}".format(inp),
                "~" if estimated else "", "{:,}".format(out),
                _f(_median([r.get("ttft_s") for r in recs])),
                _f(_generation_speed(recs)),
                _f(model_time / 60), _f(wall / 60)))
        included = "tool use and any gap between attempts" if results else \
            "tool use, your approvals and any gap between attempts"
        lines += ["", "*Model time* = time spent waiting for the model. "
                      "*Wall time* = first to last model call in that stage (includes %s)." % included]
        if any_estimate:
            lines += ["", "`~` = the server sent no token counts for some calls, so those are counted in stream "
                          "chunks. Speeds for such stages are estimates; don't compare them across servers."]
        lines += ["", "## Settings sent to the model", "",
                  "| Stage | Temperature | Max tokens |", "|---|---|---|"]

        def setting(recs, field):
            pinned = any(field in (r.get("pinned") or []) for r in recs)
            return _distinct(r.get(field) for r in recs) + (" (pinned)" if pinned else "")

        for stage in order:
            recs = by_stage[stage]
            lines.append("| %s | %s | %s |" % (stage_name(stage), setting(recs, "temperature"),
                                               setting(recs, "max_tokens")))
        lines += ["", "`–` = not sent, so the server's or the model's default applied. "
                      "*pinned* = set by `request_overrides` in the config, replacing what the agent sent."]
    else:
        lines.append("No proxy log entries for this run. Was the proxy running, and was each stage "
                     "started with '%s next'?" % CMD)

    lines += ["", "## Hidden tests", ""]
    tests_path = os.path.join(path, "tests", "summary.json")
    if os.path.exists(tests_path):
        with open(tests_path, encoding="utf-8") as fh:
            tests = json.load(fh)
        lines += ["| Build | Model | Passed | Own tests | Failing tests |", "|---|---|---|---|---|"]
        for label, res in tests.items():
            lines.append("| %s | %s | %s | %s | %s |" % (letter_of.get(label, "–" if res.get("no_build") else "?"),
                                                         label, _passed_text(res), _own_tests_text(res),
                                                    ", ".join(res["failing"]) or "–"))
    else:
        lines.append("Not run yet.")

    lines += ["", "## Judge scores", ""]
    judges = {}
    for judge in run.get("judges", []):
        state = _judge_scores(path, judge, key)
        if state["usable"]:
            judges[judge["label"]] = state["data"]
            for problem in state["problems"]:
                lines.append("- `%s`: %s, so it is left out of that build's average." % (judge["label"], problem))
        else:
            lines.append("- `%s`: no usable scores yet." % judge["label"])
    if not run.get("judges"):
        lines.append("No judges configured.")
    if judges:
        lines += ["", "### Builds (1–5 per criterion, total out of 25)", "",
                  "| Build | Model | Judge | " + " | ".join(c.replace("_", " ") for c in CRITERIA) + " | Total |",
                  "|---|---|---|" + "---|" * (len(CRITERIA) + 1)]
        for letter in sorted_letters(key["builds"]):
            totals = []
            for judge, data in sorted(judges.items()):
                scores = (data.get("builds") or {}).get(letter)
                vals = [scores.get(c) for c in CRITERIA] if isinstance(scores, dict) else [None] * len(CRITERIA)
                total = sum(vals) if all(_valid_score(v) for v in vals) else None
                if total is not None:
                    totals.append(total)
                lines.append("| %s | %s | %s | %s | %s |" % (letter, key["builds"][letter], judge,
                                                             " | ".join(_cell(v) for v in vals), _cell(total)))
            lines.append("| %s | %s | **average** | | | | | | **%s** |" % (
                letter, key["builds"][letter], _f(sum(totals) / len(totals)) if totals else "–"))
        lines += ["", "### Reviews", "",
                  "| Review | Reviewer | Judge | Real issues | False alarms | Missed important | Accuracy | Usefulness |",
                  "|---|---|---|---|---|---|---|---|"]
        for judge, data in sorted(judges.items()):
            reviews = data.get("reviews") if isinstance(data.get("reviews"), dict) else {}
            for review_key, vals in sorted(reviews.items()):
                if not isinstance(vals, dict):
                    continue
                letter, _, reviewer = review_key.partition("/")
                lines.append("| %s (%s) | %s | %s | %s | %s | %s | %s | %s |" % (
                    letter, key["builds"].get(letter, "?"), key["reviewers"].get(reviewer, reviewer), judge,
                    _cell(vals.get("real_issues")), _cell(vals.get("false_alarms")),
                    _cell(vals.get("missed_important")), _cell(vals.get("accuracy")), _cell(vals.get("usefulness"))))
        lines += ["", "### Verdicts", ""]
        for judge, data in sorted(judges.items()):
            pref = data.get("preferred_build")
            lines.append("- **%s** prefers Build %s (%s): %s" % (
                judge, pref, key["builds"].get(str(pref), "?"), data.get("one_line_verdict") or ""))

    if results:
        lines += ["", "## Unattended stages", "",
                  "| Stage | Outcome | Model calls | Time | Notes |", "|---|---|---|---|---|"]
        for stage, res in results.items():
            notes = ([res["reason"]] if res.get("reason") else []) + res.get("flags", [])
            lines.append("| %s | %s | %s | %s min | %s |" % (
                stage_name(stage), res.get("status", "?"), res.get("model_calls", "?"),
                _f((res.get("seconds") or 0) / 60), "; ".join(notes) or "–"))

    lines += ["", "## Integrity flags", ""]
    flags = list(key.get("warnings", []))
    for stage in order:
        hits = sorted(set(h for r in by_stage[stage] for h in (r.get("integrity_hits") or [])))
        if hits:
            calls = sum(1 for r in by_stage[stage] if r.get("integrity_hits"))
            flags.append("%s: requests contained %s (%d call(s)). The model may have seen material it should not have."
                         % (stage_name(stage), ", ".join("`%s`" % h for h in hits), calls))
    if flags:
        lines += ["- " + flag for flag in flags]
    else:
        lines.append("None detected. Detection is best-effort: see METHODOLOGY.md.")

    lines += ["", "## Blind key", ""]
    for letter in sorted_letters(key["builds"]):
        lines.append("- Build %s = %s" % (letter, key["builds"][letter]))
    for reviewer, label in sorted(key["reviewers"].items()):
        lines.append("- %s = %s" % (reviewer, label))

    report = os.path.join(path, "REPORT.md")
    notes = ("- Hardware and power settings:\n- Agent, version and its settings:\n"
             "- What broke or was confusing:\n- What surprised me:\n- What to change for the next run:\n")
    if os.path.exists(report):
        with open(report, encoding="utf-8", errors="replace") as fh:
            previous = fh.read()
        marker = "\n## My notes\n"
        if marker in previous:
            notes = previous.split(marker, 1)[1].lstrip("\n")
    lines += ["", "## My notes", "", notes.rstrip("\n"), ""]

    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    results = resolve_dir(cfg.get("results_dir", "results"))
    os.makedirs(results, exist_ok=True)
    copy = os.path.join(results, run_id + ".md")
    shutil.copyfile(report, copy)
    say("\nReport written: %s" % report)
    say("Copy for your records: %s\n" % copy)


# ----------------------------------------------------------------- comparing runs

def _finished_runs(cfg, task):
    """Runs with a report, oldest first. Without one a run may be in progress, and you may still be blind."""
    root = runs_root(cfg)
    found = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        path = os.path.join(root, name)
        if not os.path.exists(os.path.join(path, "REPORT.md")):
            continue
        try:
            with open(os.path.join(path, "run.json"), encoding="utf-8") as fh:
                run = json.load(fh)
        except (OSError, ValueError):
            continue
        if not task or run.get("task") == task:
            found.append((name, path, run))
    return found


def _judge_total(path, run, key, letter):
    """A build's total for this run: the mean across judges who scored it completely."""
    totals = []
    for judge in run.get("judges", []):
        data = _judge_scores(path, judge, key)["data"] or {}
        scores = data.get("builds", {}).get(letter)
        if isinstance(scores, dict) and all(_valid_score(scores.get(c)) for c in CRITERIA):
            totals.append(sum(scores[c] for c in CRITERIA))
    return sum(totals) / len(totals) if totals else None


def _spread(values):
    if not values:
        return "–"
    mean = _f(sum(values) / len(values))
    return mean if len(values) == 1 else "%s (%s–%s)" % (mean, _f(min(values)), _f(max(values)))


def cmd_compare(cfg, args):
    runs = _finished_runs(cfg, args.task)
    if not runs:
        say("\nNo finished runs to compare%s.\n" % (" for task '%s'" % args.task if args.task else ""))
        return
    log = {}
    for rec in _read_log(cfg):
        log.setdefault((rec.get("run_id"), rec.get("stage")), []).append(rec)

    # Different tests, prompts, scoring or agent mean different results, so these never share a table.
    groups = {}
    for name, path, run in runs:
        hashes = run.get("hashes") or {}
        agent = (run.get("agent") or {}).get("command")
        mode = shlex.join(agent) if agent and _read_stage_results(path) else "manual"
        group = groups.setdefault((run.get("task"), hashes.get("task"), hashes.get("prompts"),
                                   hashes.get("scoring"), mode), {"runs": 0, "models": {}})
        group["runs"] += 1
        key = read_blind_key(path) or {"builds": {}}
        letter_of = {label: letter for letter, label in key["builds"].items()}
        try:
            with open(os.path.join(path, "tests", "summary.json"), encoding="utf-8") as fh:
                tests = json.load(fh)
        except (OSError, ValueError):
            tests = {}
        for label, res in tests.items():
            model = group["models"].setdefault(label, {"passed": [], "expected": None, "failed": 0, "judged": [], "speed": []})
            model["passed"].append(res.get("passed") or 0)
            model["expected"] = res.get("expected") or model["expected"]
            model["failed"] += 1 if res.get("no_build") else 0
            total = _judge_total(path, run, key, letter_of[label]) if label in letter_of else None
            if total is not None:
                model["judged"].append(total)
            speed = _generation_speed(log.get((run.get("run_id", name), "build-" + label), []))
            if speed is not None:
                model["speed"].append(speed)

    lines = ["# Comparing %d finished run(s)" % len(runs), ""]
    if len(groups) > 1:
        lines += ["These runs differ in their task, prompts, scoring or agent, so they are not averaged together.", ""]
    for (task, task_hash, prompts, scoring, mode), group in groups.items():
        lines += ["## `%s`: %d run(s)" % (task, group["runs"]), "",
                  "Mode: `%s` · Task hash: %s · Prompts hash: %s · Scoring hash: %s" % (
                      mode, task_hash or "–", prompts or "–", scoring or "–"), "",
                  "| Model | Runs | Failed builds | Hidden tests passed | Judged runs | Judge total, of 25 | Generation speed |",
                  "|---|---|---|---|---|---|---|"]
        for label, model in sorted(group["models"].items()):
            tests_text = _spread(model["passed"])
            if model["expected"]:
                tests_text += " of %d" % model["expected"]
            lines.append("| %s | %d | %d | %s | %d | %s | %s tok/s |" % (
                label, len(model["passed"]), model["failed"], tests_text, len(model["judged"]),
                _spread(model["judged"]), _f(_median(model["speed"]))))
        lines.append("")
    lines += ["Values are the mean, with the lowest and highest in brackets. A failed build counts as 0 tests passed. "
              "Speed is the median across runs."]
    if any(len(model["passed"]) < 5 for group in groups.values() for model in group["models"].values()):
        lines += ["", "**Some models have fewer than 5 runs here, so small differences may be chance.**"]
    text = "\n".join(lines) + "\n"
    say("\n" + text)
    results = resolve_dir(cfg.get("results_dir", "results"))
    os.makedirs(results, exist_ok=True)
    saved = os.path.join(results, "compare-%s.md" % (args.task or "all"))
    with open(saved, "w", encoding="utf-8") as fh:
        fh.write(text)
    say("Saved in: %s\n" % saved)


# ----------------------------------------------------------------- unattended runs

class StageClock:
    """How long a stage has really had. A jump between polls means the machine slept, which isn't the stage's time."""

    def __init__(self, wall, mono):
        self.wall, self.mono = wall, mono
        self.awake = self.idle = self.slept = 0

    def tick(self, wall, mono, active):
        wall_step, mono_step = wall - self.wall, mono - self.mono
        self.wall, self.mono = wall, mono
        if wall_step > SLEEP_GAP_S:
            self.slept += wall_step
            self.idle = 0
            return
        self.awake += mono_step
        self.idle = 0 if active else self.idle + mono_step


def _pid_alive(pid, group=False):
    if os.name == "nt":  # os.kill with signal 0 would stop the process there
        return False
    try:
        (os.killpg if group else os.kill)(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pid(pid_path):
    try:
        with open(pid_path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _write_pid(pid_path, pid):
    with open(pid_path, "w", encoding="utf-8") as fh:
        fh.write("%d\n" % pid)


def _remove_file(file_path):
    try:
        os.remove(file_path)
    except OSError:
        pass


def _proxy_activity(cfg):
    """(change counter, calls in flight), from the file the proxy keeps up to date."""
    try:
        with open(os.path.join(logs_root(cfg), "proxy-activity.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("seq"), data.get("in_flight", 0)
    except (OSError, ValueError, AttributeError):
        return None, 0


def _keep_awake():
    """Hold off sleep for as long as this process lives, even if it crashes. Returns the holder process."""
    system = platform.system()
    if system == "Darwin":
        tool = shutil.which("caffeinate")
        command = [tool, "-is", "-w", str(os.getpid())]
    elif system == "Linux":
        tool = shutil.which("systemd-inhibit")
        # The child ends when its stdin closes, and stdin closes when this process ends in any way.
        command = [tool, "--what=idle:sleep", "--who=relaybench", "--why=Unattended run", "--mode=block",
                   sys.executable, "-c", "import sys; sys.stdin.read()"]
    else:
        tool = None
    if not tool:
        warn("Can't keep this machine awake from here. See 'Keeping the machine awake' in GUIDE-UNATTENDED.md.")
        return None
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    holder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, **kwargs)
    time.sleep(0.3)
    if holder.poll() is not None:
        warn("%s could not keep this machine awake. See 'Keeping the machine awake' in GUIDE-UNATTENDED.md."
             % os.path.basename(tool))
        return None
    ok("Keeping the machine awake during this run (%s)" % os.path.basename(tool))
    return holder


def _release_awake(holder):
    if holder is None:
        return
    holder.stdin.close()
    holder.terminate()
    try:
        holder.wait(timeout=5)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait()


def _ensure_proxy(cfg, proxy):
    """Return the proxy this run started, starting one if nothing answers. A proxy you started is left alone."""
    try:
        health = http_json(proxy_url(cfg) + "/__health", timeout=5)
        if health.get("upstream") and health["upstream"] != backend_root(cfg):
            die("The running proxy forwards to %s, but the config says %s. Restart the proxy."
                % (health["upstream"], backend_root(cfg)))
        return proxy
    except (urllib.error.URLError, OSError, ValueError):
        pass
    os.makedirs(logs_root(cfg), exist_ok=True)
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    with open(os.path.join(logs_root(cfg), "proxy-output.log"), "ab") as out:
        proxy = subprocess.Popen([sys.executable, os.path.join(HERE, "proxy", "llm_proxy.py")], cwd=HERE,
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            http_json(proxy_url(cfg) + "/__health", timeout=1)
            say("Started the logging proxy.")
            return proxy
        except (urllib.error.URLError, OSError, ValueError):
            if proxy.poll() is not None:
                break
            time.sleep(0.1)
    die("The logging proxy did not start. See %s" % os.path.join(logs_root(cfg), "proxy-output.log"))


def _stop_proxy(proxy):
    if proxy is not None:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
            proxy.wait()


def _run_agent(cfg, agent, path, name, folder):
    """One agent process for one stage, until it exits or stops making progress."""
    values = {"{folder}": folder, "{base_url}": proxy_url(cfg) + "/v1", "{model}": served_name(cfg)}
    command = []
    for part in agent["command"]:
        for placeholder, value in values.items():
            part = part.replace(placeholder, value)
        command.append(part)
    # A dummy key on purpose: a real one in your environment should not go to an agent under test.
    env = dict(os.environ, OPENAI_BASE_URL=values["{base_url}"], OPENAI_API_BASE=values["{base_url}"],
               OPENAI_API_KEY="relaybench", OPENAI_MODEL=served_name(cfg))
    stage_limit = agent.get("stage_timeout_s", AGENT_DEFAULTS["stage_timeout_s"])
    idle_limit = agent.get("idle_timeout_s", AGENT_DEFAULTS["idle_timeout_s"])
    log_dir = os.path.join(path, "agent-logs")
    os.makedirs(log_dir, exist_ok=True)
    pid_path = os.path.join(runs_root(cfg), "agent.pid")
    kwargs = {"start_new_session": True} if os.name != "nt" else {}
    outcome = {"exit_code": None, "stopped": None, "error": None, "seconds": 0, "slept_s": 0}
    started = time.monotonic()
    with open(os.path.join(log_dir, name + ".log"), "ab") as out:
        try:
            proc = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            outcome["error"] = str(exc)
            return outcome
        _write_pid(pid_path, proc.pid)
        clock = StageClock(time.time(), started)
        last_seq = _proxy_activity(cfg)[0]
        try:
            while proc.poll() is None:
                time.sleep(AGENT_POLL_S)
                seq, in_flight = _proxy_activity(cfg)
                # A call in flight counts as activity: local models can think for minutes before the first token.
                clock.tick(time.time(), time.monotonic(), seq != last_seq or in_flight > 0)
                last_seq = seq
                if clock.awake > stage_limit:
                    outcome["stopped"] = "reached the stage limit of %d s" % stage_limit
                    break
                if clock.idle > idle_limit:
                    outcome["stopped"] = "no model call for %d s" % idle_limit
                    break
            outcome["exit_code"] = proc.poll()
        finally:
            # Also after a normal exit: a server the build left running would slow the next model down.
            _kill_tree(proc, grace_s=5 if proc.poll() is None else 0)
            _remove_file(pid_path)
    outcome["seconds"] = clock.awake
    outcome["slept_s"] = clock.slept
    return outcome


def _uncommitted_work(folder):
    """Changed files that are the build's own work, not the instructions or an agent's leftovers."""
    status = git(folder, "status", "--porcelain", "-z", "--untracked-files=all").stdout.split("\0")
    work = []
    tokens = iter(status)
    for token in tokens:
        if not token:
            continue
        rel = token[3:]
        if token[0] in "RC":  # a rename is followed by its old name
            next(tokens, None)
        if rel in ("TASK.md", "plan.md") or any(is_skipped(part, SKIP_IN_COPY) for part in rel.split("/")):
            continue
        work.append(rel)
    return work


def _set_aside(file_path):
    """Keep a rejected plan or review for reading, without it counting as the stage's result."""
    if os.path.exists(file_path):
        os.replace(file_path, file_path + ".rejected")


def _settle_stage(cfg, run_id, name, folder, kind, outcome, since):
    """Decide what an agent's stage produced. Returns its entry for stage-results.json."""
    deadline = time.monotonic() + 5
    while _proxy_activity(cfg)[1] > 0 and time.monotonic() < deadline:  # stats are logged as a call ends
        time.sleep(0.1)
    calls = sum(1 for r in _read_log(cfg) if r.get("run_id") == run_id and r.get("stage") == name
                and (r.get("epoch") or 0) >= since)
    result = {"status": "done", "reason": "", "flags": [], "model_calls": calls,
              "seconds": round(outcome["seconds"], 1), "exit_code": outcome["exit_code"]}
    if outcome["stopped"]:
        result["flags"].append("stopped: " + outcome["stopped"])
    elif outcome["exit_code"]:
        result["flags"].append("agent exited with code %s" % outcome["exit_code"])
    if outcome["slept_s"]:
        result["flags"].append("machine slept for %.0f min, so wall time is unreliable" % (outcome["slept_s"] / 60))

    output = {"plan": "plan.md", "review": "REVIEW.md"}.get(kind)
    output_path = os.path.join(folder, output) if output else None

    def rejected(reason):
        result["status"], result["reason"] = "failed", reason
        if output_path:
            _set_aside(output_path)
        return result

    if outcome["error"]:
        return rejected("the agent could not start: %s" % outcome["error"])
    if not calls:
        # Whatever it wrote didn't come from the model under test: the agent may be using its own default model.
        return rejected("no model calls reached the proxy, so the agent may not be pointed at it")
    if output_path:
        if not os.path.exists(output_path):
            return rejected("%s was not written" % output)
        with open(output_path, encoding="utf-8", errors="replace") as fh:
            if not fh.read().strip():
                return rejected("%s is empty" % output)
        return result
    try:
        work = _uncommitted_work(folder)
        if work:
            git(folder, "add", "-A")
            git(folder, "commit", "-q", "-m", "Uncommitted work, committed by relaybench")
            result["flags"].append("the agent didn't commit its work, so the harness did")
    except (subprocess.CalledProcessError, OSError) as exc:
        return rejected("committing the agent's work failed: %s" % exc)
    if commit_count(folder) <= 1:
        return rejected("the agent changed nothing")
    return result


def _run_stage(cfg, run_id, path, run, agent, stage):
    name, folder, label, kind = stage
    try:
        start_stage(cfg, run_id, path, run, name, folder, label, kind)
    except SystemExit:
        results = _read_stage_results(path)
        results[name] = {"status": "failed", "reason": "the model could not be loaded", "flags": [],
                         "model_calls": 0, "seconds": 0, "exit_code": None}
        _write_stage_results(path, results)
        fail("%s: the model could not be loaded" % name)
        return results[name]
    since = time.time()
    outcome = _run_agent(cfg, agent, path, name, folder)
    result = _settle_stage(cfg, run_id, name, folder, kind, outcome, since)
    results = _read_stage_results(path)
    results[name] = result
    _write_stage_results(path, results)
    summary = "%s: %s after %.1f min, %d model call(s)" % (name, result["status"], result["seconds"] / 60,
                                                          result["model_calls"])
    notes = ([result["reason"]] if result["reason"] else []) + result["flags"]
    (fail if result["status"] == "failed" else ok)(summary + (" (%s)" % "; ".join(notes) if notes else ""))
    return result


def _run_preflight(cfg, agent, source):
    """Stop now rather than after hours of building."""
    if not pytest_available():
        die("pytest is not installed in this Python. Run:  %s -m pip install -r requirements.txt" % PY)
    if not shutil.which("git"):
        die("Git is not installed.")
    if not shutil.which(agent["command"][0]):
        die("The agent command '%s' was not found." % agent["command"][0])
    try:
        loaded_model_ids(cfg)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        die(server_problem(cfg, exc) + " Fix that first.")
    offered = list_models(backend_of(cfg).get("type", "lmstudio"), backend_root(cfg), served_name(cfg),
                          backend_headers(cfg))
    for model in all_models(source):
        if offered and not model_listed(model["model"], offered):
            die("Not offered by the server: %s (%s). Compare with:  %s models" % (model["label"], model["model"], CMD))
    for judge in source.get("judges", []):
        if judge["type"] != "api":
            continue
        if judge.get("api_key_env") and not os.environ.get(judge["api_key_env"]):
            die("Judge '%s': set the environment variable %s first." % (judge["label"], judge["api_key_env"]))
        if judge.get("model") == PLACEHOLDER:
            die("Judge '%s': the model id is still a placeholder." % judge["label"])


def _resumable_run(cfg):
    """The current run's id if it has no report yet."""
    pointer = os.path.join(runs_root(cfg), "CURRENT")
    if not os.path.exists(pointer):
        return None
    with open(pointer, encoding="utf-8") as fh:
        run_id = fh.read().strip()
    path = os.path.join(runs_root(cfg), run_id)
    if not os.path.exists(os.path.join(path, "run.json")) or os.path.exists(os.path.join(path, "REPORT.md")):
        return None
    return run_id


def _run_steps(cfg, args, run_id, path, run, agent):
    """Work through the run until it has a report. Returns the process's exit code."""
    harness_args = argparse.Namespace(force=False, show=False)
    judge_problem = False
    judges_asked = False
    proxy = None
    try:
        while True:
            step, detail = _pending_step(path, run)
            if step == "stage":
                proxy = _ensure_proxy(cfg, proxy)
                result = _run_stage(cfg, run_id, path, run, agent, detail)
                if detail[3] == "plan" and result["status"] == "failed":
                    die("The plan stage failed, and every other stage depends on it. "
                        "Fix the problem and run '%s run' to try again." % CMD)
            elif step == "freeze":
                cmd_freeze(cfg, harness_args)
            elif step == "test":
                cmd_test(cfg, harness_args)
                if len(failed_builders(path, run)) == len(run["builders"]):
                    cmd_report(cfg, harness_args)
                    fail("No build succeeded, so nothing was reviewed or judged.")
                    return 1
            elif step == "blind":
                cmd_blind(cfg, harness_args)
            elif step == "packet":
                cmd_packet(cfg, harness_args)
            elif step == "judges" and not judges_asked and any(j["type"] == "api" for j in detail):
                judges_asked = True
                try:
                    cmd_judge(cfg, harness_args)
                except SystemExit:
                    judge_problem = True
            else:
                cmd_report(cfg, harness_args)
                break
    finally:
        _stop_proxy(proxy)
    failed = failed_stages(path)
    if failed:
        warn("%d stage(s) failed: %s" % (len(failed), ", ".join(sorted(failed))))
    key = read_blind_key(path)
    open_judges = [j for j in run.get("judges", []) if not _judge_scores(path, j, key)["complete"]]
    if open_judges:
        say("Still open, because these judges need a person or failed:")
        for judge in open_judges:
            say("  %s (%s): %s" % (judge["label"], judge["type"], _score_file(path, judge["label"])))
        say("Fill those in (or run '%s judge' for api judges), then run:  %s report\n" % (CMD, CMD))
    return 1 if judge_problem else 0


def cmd_run(cfg, args):
    global UNATTENDED
    if args.repeat < 1:
        die("--repeat needs a number of 1 or more.")
    agent = cfg.get("agent")
    root = runs_root(cfg)
    os.makedirs(root, exist_ok=True)
    run_pid = os.path.join(root, "run.pid")
    other = _read_pid(run_pid)
    if other and other != os.getpid() and _pid_alive(other):
        die("Another '%s run' is already working here (pid %d)." % (CMD, other))
    agent_pid_path = os.path.join(root, "agent.pid")
    agent_pid = _read_pid(agent_pid_path)
    if agent_pid and _pid_alive(agent_pid, group=True):
        die("An agent from an interrupted run may still be working (pid %d). Stop it first. "
            "If that pid is something else, delete %s." % (agent_pid, agent_pid_path))
    _remove_file(agent_pid_path)

    resume = _resumable_run(cfg)
    if resume and args.task:
        die("Run %s isn't finished. To leave it and start another:  %s new-run --task %s, then  %s run"
            % (resume, CMD, args.task, CMD))
    run_config = cfg
    if resume:
        with open(os.path.join(root, resume, "run.json"), encoding="utf-8") as fh:
            run_config = json.load(fh)
        agent = run_config.get("agent") or agent
    if not agent:
        die("Unattended runs need a command-line agent. Run '%s init' and choose one, or add it to "
            "pipeline.json:\n%s\nSee GUIDE-UNATTENDED.md." % (CMD, AGENT_EXAMPLE))
    _run_preflight(cfg, agent, run_config)

    UNATTENDED = True
    _write_pid(run_pid, os.getpid())
    holder = None
    if os.name != "nt":  # so closing the window or `kill` still cleans up the agent and the proxy
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:  # ignored means nohup, which must keep working
            signal.signal(signal.SIGHUP, signal.default_int_handler)
    try:
        if not args.allow_sleep:
            holder = _keep_awake()
        codes = []
        for number in range(1, args.repeat + 1):
            if args.repeat > 1:
                say("\n=== Run %d of %d ===" % (number, args.repeat))
            if resume and number == 1:
                say("\nResuming run %s. To start fresh instead:  %s new-run, then  %s run" % (resume, CMD, CMD))
            else:
                cmd_new_run(cfg, args)
            run_id, path, run = current_run(cfg)
            if "agent_version" not in run:
                run["agent"] = run.get("agent") or agent
                run["agent_version"] = agent_version(run["agent"]["command"])
                with open(os.path.join(path, "run.json"), "w", encoding="utf-8") as fh:
                    json.dump(run, fh, indent=2)
            try:
                codes.append(_run_steps(cfg, args, run_id, path, run, run["agent"]))
            except SystemExit as exc:
                if args.repeat == 1:
                    raise
                codes.append(exc.code if isinstance(exc.code, int) and exc.code else 1)
                warn("Run %d of %d stopped early. Carrying on with the next one." % (number, args.repeat))
        code = max(codes)
        if args.repeat > 1:
            cmd_compare(cfg, argparse.Namespace(task=run["task"]))
    except KeyboardInterrupt:
        say("\nStopped. Run '%s run' to resume; the unfinished stage starts again, and the report counts "
            "that as another attempt.\n" % CMD)
        code = 130
    finally:
        _release_awake(holder)
        _remove_file(run_pid)
    sys.exit(code)


# ----------------------------------------------------------------- main

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):  # a Windows console may not be able to print every character
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", default="help")
    parser.add_argument("label", nargs="?")
    parser.add_argument("--task")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--allow-sleep", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    commands = {
        "check": cmd_check, "models": cmd_models, "new-run": cmd_new_run, "next": cmd_next,
        "start": cmd_start, "status": cmd_status, "load": cmd_load, "smoke": cmd_smoke,
        "freeze": cmd_freeze, "test": cmd_test, "blind": cmd_blind, "packet": cmd_packet,
        "judge": cmd_judge, "report": cmd_report, "run": cmd_run, "compare": cmd_compare,
    }
    if args.command == "init":
        try:
            cmd_init(None, args)
        except KeyboardInterrupt:
            say("\nStopped. Nothing was saved.\n")
        return
    if args.command in ("help", "-h", "--help") or args.command not in commands:
        if args.command not in ("help", "-h", "--help"):
            say("Unknown command: %s" % args.command)
        say(HELP)
        return
    if args.command == "load" and not args.label:
        die("Tell me which model to load, e.g.:  %s load <label>" % CMD)
    if args.command == "start" and not args.label:
        die("Tell me which stage to start, e.g.:  %s start plan" % CMD)
    commands[args.command](load_config(), args)


if __name__ == "__main__":
    main()
