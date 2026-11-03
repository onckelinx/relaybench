"""Stands in for a coding agent: reads a stage folder, calls the proxy, writes files, commits.

It proves the harness and proxy work end to end. It does not test any real agent.

The tests call run_agent directly for manual runs. Run as a script, it is the command-line agent of
`pipeline.py run`, and --misbehave makes it fail in one stage the way a real agent might.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

FILE_BLOCK = re.compile(r'<file path="([^"]+)">\n(.*?)</file>', re.DOTALL)


def _read(folder, name):
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _call(base_url, model, messages):
    payload = {"model": model, "stream": True, "temperature": 0.2, "max_tokens": 16384, "messages": messages}
    req = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "agent-dummy-key")})
    text = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            raw = raw.strip()
            if raw.startswith(b"data:") and raw[5:].strip() != b"[DONE]":
                for choice in json.loads(raw[5:].strip()).get("choices") or []:
                    text.append((choice.get("delta") or {}).get("content") or "")
    return "".join(text)


def run_agent(folder, base_url, model="local-model", extra_text="", commit=True):
    """Do one stage, in two model calls like a (very small) agent loop."""
    prompt = _read(folder, "PROMPT.md")
    user = "\n\n".join([prompt, _read(folder, "TASK.md"), _read(folder, "plan.md"), extra_text])
    messages = [{"role": "system", "content": "You are a coding agent."}, {"role": "user", "content": user}]
    answer = _call(base_url, model, messages)
    for rel, body in FILE_BLOCK.findall(answer):
        target = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    _call(base_url, model, messages + [{"role": "assistant", "content": answer},
                                       {"role": "user", "content": "Files written. Finish up."}])
    if commit and "# Your role: developer" in prompt:
        subprocess.run(["git", "add", "-A"], cwd=folder, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "Implement task"], cwd=folder, check=True, capture_output=True)
    return answer


def start_child():
    """Something the agent started, which must not outlive it. Its pid goes where the test can find it."""
    # It ignores SIGTERM on purpose, so only a forced kill of the whole process group can end it.
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"
    child = subprocess.Popen([sys.executable, "-c", code])
    with open(os.environ["SCRIPTED_AGENT_CHILD"], "w", encoding="utf-8") as fh:
        fh.write(str(child.pid))


def write_without_model(folder):
    prompt = _read(folder, "PROMPT.md")
    if "# Your role: developer" in prompt:
        with open(os.path.join(folder, "from_another_model.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=folder, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "Work"], cwd=folder, check=True, capture_output=True)
    else:
        name = "plan.md" if "# Your role: planner" in prompt else "REVIEW.md"
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write("Written by another model.\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    parser.add_argument("--misbehave", action="append", default=[], help="<folder prefix>:<mode>")
    parser.add_argument("prompt", nargs="?", help="what a real agent is told; this one follows its script")
    args = parser.parse_args()
    folder = os.getcwd()
    mode = "normal"
    for item in args.misbehave:
        prefix, _, wanted = item.partition(":")
        if os.path.basename(folder).startswith(prefix):
            mode = wanted
    if mode == "hang":
        start_child()
        time.sleep(600)
    elif mode == "no-calls":
        write_without_model(folder)
    elif mode == "nothing":
        _call(args.base_url, args.model, [{"role": "user", "content": "Reply with exactly these two words: pipeline OK"}])
    elif mode == "no-commit":
        run_agent(folder, args.base_url, args.model, commit=False)
    elif mode == "exit-1":
        run_agent(folder, args.base_url, args.model)
        sys.exit(1)
    elif mode == "leave-child":
        run_agent(folder, args.base_url, args.model)
        start_child()
    else:
        run_agent(folder, args.base_url, args.model)


if __name__ == "__main__":
    main()
