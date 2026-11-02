"""Stands in for a coding agent: reads a stage folder, calls the proxy, writes files, commits.

It proves the harness and proxy work end to end. It does not test any real agent.
"""
import json
import os
import re
import subprocess
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
        "Content-Type": "application/json", "Authorization": "Bearer agent-dummy-key"})
    text = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            raw = raw.strip()
            if raw.startswith(b"data:") and raw[5:].strip() != b"[DONE]":
                for choice in json.loads(raw[5:].strip()).get("choices") or []:
                    text.append((choice.get("delta") or {}).get("content") or "")
    return "".join(text)


def run_agent(folder, base_url, model="local-model", extra_text=""):
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
    if "# Your role: developer" in prompt:
        subprocess.run(["git", "add", "-A"], cwd=folder, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "Implement task"], cwd=folder, check=True, capture_output=True)
    return answer
