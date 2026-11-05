# Manual run guide

You drive your coding agent through each stage yourself, in an editor. This guide takes you from nothing to
a finished test run:

```
planner model ──► plan.md ──┬──► builder 1 builds ──► reviewer reviews ──┐
                  (frozen)  └──► builder 2 builds ──► reviewer reviews ──┴──► judges score
```

It uses **LM Studio** as the model server and **Cline** in VS Code as the agent, as a worked example.
Neither is required: see [Backends](#backends) and [Other agents](#other-agents).

To do a whole run with one command and no editor, use the [unattended guide](GUIDE-UNATTENDED.md) instead.

**How it fits together.** Your agent is set up **once**, to talk to a small logging proxy on your
machine, and it always asks for a model called `local-model`. The harness makes sure whichever model
a stage needs answers to that name. So you never change the agent's settings between stages.

```
coding agent ──► proxy (127.0.0.1:1235, logs speed) ──► model server (127.0.0.1:1234)
                                                          └─ "local-model" = whatever
                                                             model the stage needs
```

In commands below, use `python3` instead of `python` if `python` isn't found outside a virtual
environment. Inside the virtual environment from Part A, `python` always works.

## Words used in this guide

| Word | Meaning |
|---|---|
| **Run** | One complete pass through the workflow. Each run gets its own folder in `runs/`. |
| **Stage** | One step of a run: plan, build or review. Each model stage has its own folder with a `PROMPT.md`. |
| **Label** | The short name of a model in this harness, such as `my-builder`. |
| **Model key** | The server's own id for a model, such as `publisher/model-name`. |
| **Context length** | How many tokens a model can keep in view at once. |
| **Proxy** | The script in `proxy/` between your agent and the server. It logs the speed and tokens of every call. |

---

# Part A — Install

### 1. Prerequisites

You need Python 3.9 or newer, Git, a model server and a coding agent.

```bash
python3 --version      # Windows: python --version
git --version
```

### 2. Get the code

```bash
git clone https://github.com/onckelinx/relaybench.git
cd relaybench
python3 -m venv .venv  # Windows: python -m venv .venv
```

Activate the environment. Do this in every new terminal you use for relaybench:

| Shell | Command |
|---|---|
| macOS / Linux | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |

**Check:** your prompt now starts with `(.venv)`. Then:

```bash
pip install -r requirements.txt
```

If you see `command not found` for `pip` or `python`, the environment isn't activated.

### 3. Choose and download models

You need at least a planner and one builder; two or more builders is the point. One model can play
several roles. Download them in your server, and check that your machine has enough memory for each
one **on its own**: LM Studio mode loads one model at a time.

For a coding agent, a context of at least 32K tokens is a sensible floor, as agents send long
instructions with every request. Bigger takes more memory.

### 4. Prepare LM Studio (LM Studio only)

**The `lms` command.** The harness uses it to load models and set their context length. Run `lms ls`.
If it isn't found, open LM Studio once, then run the bootstrap and open a new terminal:

| Platform | Command |
|---|---|
| macOS / Linux | `~/.lmstudio/bin/lms bootstrap` |
| Windows | `cmd /c %USERPROFILE%\.lmstudio\bin\lms.exe bootstrap` |

**Just-in-Time loading (recommended off).** In LM Studio's **Developer** section, open **Server
Settings** and turn off **Just in Time Model Loading**. With it on, a request with the wrong model
name can quietly load another model with a small default context. With it off, mistakes show up as
clear errors.

**Start the server** in the **Developer** section, or with `lms server start`.

**Check:** `curl http://127.0.0.1:1234/v1/models` prints text starting with `{`. (`data` is empty
when no model is loaded and Just-in-Time loading is off. That is fine.)

### 5. Write your configuration

```bash
python pipeline.py init
```

It asks one question at a time, and the value in `[brackets]` is what you get by pressing Enter. It writes
`pipeline.json`, which Git ignores. If you make a mistake, press `Ctrl+C` and start again: nothing is saved
until the end. You can run it again whenever you like.

| Question | What to answer |
|---|---|
| Choose (model server) | `1` for LM Studio, `2` for [another server](#backends) |
| Server URL | Enter, for LM Studio on this machine |
| Environment variable with the server's API key | Enter, unless your server needs a key |
| Context length in tokens | Enter, unless you know you want another value |
| GPU offload | **Enter**, for `max`. `off` means no GPU, so models run far slower. |
| Port for the logging proxy | Enter |
| Planner | One number from the list of models, such as `3` |
| Builders | Numbers separated by commas, such as `6,7` |
| Reviewers | Numbers separated by commas, or Enter for none |
| Judge 1 type | `paste` for a web chat you copy to and from, `api` for an API `judge` can call, `human` for a person |
| Label | A short name for this judge, such as `chatgpt` |
| Judge 2 type | Another judge, or Enter to finish the list |
| Choose (agent) | Enter for none. This is only for [unattended runs](GUIDE-UNATTENDED.md). |
| Runs folder | Enter |
| Default task | Enter for the toy task, which is best for a first run |

Prefer a text editor? Copy `pipeline.example.json` to `pipeline.json`, and use
`python pipeline.py models` for the keys. The [README](README.md#configuration) lists every setting.

### 6. Start the logging proxy

In a **second terminal**, in the repo folder, with the environment activated:

```bash
python proxy/llm_proxy.py
```

**Leave it open** whenever you run stages. Each model call adds a line here: stage, model, tokens in
and out, time to first token, speed. Stop it with `Ctrl+C`. Your firewall may ask about it the first
time; it only listens on your own machine. Restart it after you change the backend or logs settings.

### 7. Run the check

```bash
python pipeline.py check
```

**Check:** every line says `[ OK ]` and the last says `Everything looks good.` It exits with an
error if something is wrong, and each `[FAIL]` says what to do.

### 8. Test each model once

```bash
python pipeline.py load <label>
python pipeline.py smoke
```

Repeat for each label. `smoke` should end with `Smoke test passed.` and show tokens and tokens/sec.

### 9. Set up your agent (once)

Connect the agent to the proxy, not to the server. In Cline, open **Settings**:

| Setting | Value |
|---|---|
| **API Provider** | **OpenAI Compatible** |
| **Base URL** | `http://127.0.0.1:1235/v1` |
| **API Key** | anything; the field just can't be empty |
| **Model ID** | `local-model` |
| **Context Window Size** | the same as `backend.context_length` in `pipeline.json` |
| **Max Output Tokens** | one fixed value for every stage, such as `32768`. To enforce it for any agent, see [Pinning model settings](GUIDE-UNATTENDED.md#pinning-model-settings). |
| **Temperature** | leave empty if allowed, so each model uses its own default. Some models loop at temperature 0; check their model cards. |

Exact labels vary by version. Also consider:

- **A compact prompt option, if there is one.** Cline's *Use Compact Prompt* sends local models a
  shorter set of instructions, which many handle better.
- **Same permissions for every stage.** Allow reading and editing project files, and safe commands.
  If one model waits for your approval and another doesn't, their wall times can't be compared. Never
  allow anything outside the workspace.
- **Same power settings for every stage.** Speed depends on power mode, on laptops especially.
  Choose one setup, and note it in the report.
- **Cloud providers.** Keep local-only testing simple: don't add cloud keys or other providers to
  this agent profile. Then if the proxy or the server isn't running, the agent just shows a connection
  error instead of sending your code somewhere else.

### 10. Test the agent once

```bash
python pipeline.py load <label>
```

Open any empty folder in your agent, start a new session, and send:

```
Reply with the word OK and finish.
```

**Check:** the agent answers, and the **proxy window** shows a new line with stage `manual-<label>`.
That proves the agent goes through the proxy. Close the folder.

---

# Part B — Run the pipeline

The rhythm is **`python pipeline.py next`, do what it says, repeat.**
At any moment, `python pipeline.py status` shows where you are.

Two rules:

1. **Start every model stage with `next`** (or `start <stage>`). It switches the model and tells the
   proxy which stage is running. If you skip it, your agent talks to the wrong model and the speed
   log is filed under the wrong stage.
2. **Start a new agent session for each stage**, with that stage's folder as the workspace, and
   close it afterwards.

### 1. Create a run

```bash
python pipeline.py new-run
```

To use a different task: `python pipeline.py new-run --task cron-next`. See [tasks/README.md](tasks/README.md).

### 2. Plan stage

```bash
python pipeline.py next
```

It switches to the planner and prints the folder to open. In your agent:

1. Open that folder.
2. Start a new session. In Cline, check that the switch at the bottom says **Act**: in **Plan** mode
   it can't create files.
3. Send:

   ```
   Follow the instructions in PROMPT.md
   ```

4. Watch the **proxy window**: lines with stage `plan` should appear.
5. Wait for `PLAN DONE`.

**If the planner prints the plan but doesn't save it:** send `Save that plan to plan.md now.` If that
still fails, save it by hand as `plan.md` in that folder, and note in your report that the planner
couldn't save files.

### 3. Check and freeze the plan

Read `plan.md`. Does it have the six sections `PROMPT.md` asks for? You may fix obvious problems by
hand, but write down what you changed. Then:

```bash
python pipeline.py freeze
```

Every builder now has the same plan, and its own folder with its own Git repo.

### 4. Build stages

For each builder:

```bash
python pipeline.py next
```

Open the printed folder, new session, `Follow the instructions in PROMPT.md`, and wait for `BUILD DONE`.
If the model forgot to commit, send `Commit your work with git now.` Then check for at least two commits:

```bash
git log --oneline
```

**If a model gets stuck** (repeating itself, looping on errors for more than about 10 minutes): cancel
it, note what happened, and either nudge it once or commit what is there yourself:

```bash
git add -A
git commit -m "Stopped by hand"
```

Getting stuck is a real finding about the model, not a failure of the test.

### 5. Run the hidden tests

```bash
python pipeline.py test
```

This also runs each build's own tests, on a copy, so that judges can see whether they pass. The scores stay
hidden so you stay blind. It warns you if a build timed out or lost tests. To see the numbers anyway, use
`test --show`. Full output is in the run's `tests` folder.

### 6. Anonymise the builds

```bash
python pipeline.py blind
```

Don't open `blind-key.json` if you want to stay blind. `blind` warns if a build changed the frozen
plan, or mentions a model's label.

### 7. Review stages

For each review:

```bash
python pipeline.py next
```

Open the printed folder, new session, `Follow the instructions in PROMPT.md`, and wait for
`REVIEW DONE`. Reviewers work on a copy, so even if one edits code, the original builds stay untouched.

### 8. Judge

```bash
python pipeline.py packet
```

This writes `4-judging/judge-packet.md` with the task, the plan, every build with its test results and
numbered code, and then every review, labelled only by letter. Then, for each judge:

- **`api` judges:** run `python pipeline.py judge`. It sends the packet and saves each answer.
- **`paste` judges:** start a **new** chat with the judge, using the same model every run. Attach the
  packet, send `Follow the instructions in this file.`, and paste the complete answer into
  `4-judging/scores-<label>.md`. Use the chat's copy button for the whole answer: selecting text can
  drop the formatting, and copying only the JSON block leaves out the reasoning. If the answer is in
  a canvas or a file, ask for it directly in the chat. If its format is off, ask:
  `Rewrite your answer in exactly the format under "Required output" in the scoring sheet.`
- **`human` judges:** read the packet and fill in `4-judging/scores-<label>.md`, replacing every
  `null`. Ideally this is someone who didn't run the stages.

**Check:** `python pipeline.py status` shows `Judge scores complete (n of n)`. If a judge is missing,
its file has no JSON section with all scores from 1 to 5.

**Want another judge?** Each run keeps its own list of judges, so changing `pipeline.json` won't add one. Use
`python pipeline.py add-judge`, which creates the score file too. Add `--run <id>` for an earlier run.

### 9. Write the report

```bash
python pipeline.py report
```

`runs/<run-id>/REPORT.md` shows your hardware and quantisation where the system says, speed per stage, the
settings sent to the model, hidden and own test results, scores per judge with averages, integrity flags,
and the blind key. Fill in **My notes**, including your power setup and agent settings, and anything the
report lists as `not reported`, then run `report` again. It keeps your notes and updates the copy in
`results/`. Reports name your models, so `results/` is ignored by Git. Commit them only if you want to.

---

# Part C — After the run

**Run again.** There is nothing to reset: `python pipeline.py new-run` makes a new folder. Old runs
stay in `runs/` until you delete them.

**Compare runs.** `python pipeline.py compare` shows each model's tests, judge totals and speed across finished
runs. Runs with a different task version, prompts, scoring sheet or agent go in separate tables.

**Redo one stage.** `python pipeline.py start <stage>`, for example `start build-<label>`. `status`
and the error message list stage names. The report counts how often each stage was started.

**Redo all builds or the anonymisation.** `freeze --force` or `blind --force`. Old results that
depended on them are moved to a `_stale-<time>` folder in the run, because old scores would otherwise
be credited to the wrong models.

**Make the task harder, or write your own.** See [tasks/README.md](tasks/README.md).

**Swap or add models.** Edit `pipeline.json` or run `init` again. A running run keeps the models it
started with; a new run picks up the change. Any number of builders and reviewers is fine: every
reviewer reviews every build, in folders `…-by-reviewer-2` and so on. Labels use lowercase letters,
digits, dots, dashes and underscores.

---

# Keeping the machine awake

A sleeping machine stops a run, and a run takes a long time. Leave one of these running in a spare terminal
until you're done, and press `Ctrl+C` to end it:

| System | Command |
|---|---|
| macOS | `caffeinate -is` |
| Linux | `systemd-inhibit --what=idle:sleep --who=relaybench --why="long run" --mode=block sleep infinity` |

**Closing a laptop's lid usually sleeps it anyway**, so leave it open. Lids, power settings and Windows are
covered under [Keeping the machine awake](GUIDE-UNATTENDED.md#keeping-the-machine-awake) in the other guide.

# Backends

**LM Studio with `lms`** (`"type": "lmstudio"`). The harness unloads everything and loads each stage's
model under the shared name, with `backend.context_length` and `backend.gpu`. This gives the best
control: one model in memory at a time, with the same context for all.

**LM Studio without `lms`.** The harness can't load models, so it falls back to the behaviour below.
Turn Just-in-Time loading **on**, and know that context length is whatever LM Studio chooses.

**Any other OpenAI-compatible server** (`"type": "openai-compatible"`). There is no load step: the
proxy rewrites `model` in each request to the stage's real model id, and lists only `local-model` at
`/v1/models`, so your agent's settings still never change. The server has to serve a model by name:

- Servers that load models on demand generally work out of the box. The first call's load time counts
  in that stage's time to first token.
- A server that only runs one fixed model at a time needs you to switch it by hand before each stage.
- **Set context length on the server**, to the same value for every model. The harness can't control
  it in this mode, and the report says so. A small default can silently truncate an agent's long prompt.
- For a server that needs a key, put it in an environment variable and name that variable in
  `backend.api_key_env`. The proxy adds it to requests, so the agent's key field can hold anything.
- For a remote URL, remember that your code goes there too.

```json
"backend": { "type": "openai-compatible", "url": "http://127.0.0.1:11434/v1" }
```

# Other agents

Any agent works if it can use a custom OpenAI-compatible base URL and can edit files and run commands:

1. Base URL `http://127.0.0.1:1235/v1`, model `local-model`, any non-empty API key.
2. Give it the same context window as your server, if it asks.
3. Run it with the stage folder as its workspace, in a new session each time.
4. Tell it: `Follow the instructions in PROMPT.md`.

An agent with a command-line mode can also do a whole run by itself: see the
[unattended guide](GUIDE-UNATTENDED.md).

Some agents leave files in the workspace that can name the model. Common ones are skipped when builds
are copied for review (see `SKIP_IN_COPY` in `pipeline.py`). If yours leaves others, add them there,
and watch for `blind` warnings.

# Privacy

- **Prompts, code and answers** go from your agent to the proxy to your model server. With a local
  server, they stay on your machine.
- **Your agent** may have telemetry of its own. Check its settings, and your editor's.
- **Judging** sends the packet to whichever cloud judges you choose, by design. `api` judges are only
  called when you run `judge`.

# Troubleshooting

| Problem | What to do |
|---|---|
| `pip: command not found` | Activate the virtual environment: see Part A, step 2. |
| `python: command not found` | Try `python3`, or activate the virtual environment. |
| `pipeline.json not found` | Run `python pipeline.py init`, from the repo folder. |
| `pytest is not installed` | Activate the virtual environment, then `pip install -r requirements.txt`. |
| `lms: command not found` | See Part A, step 4. Without `lms`, see [Backends](#backends). |
| `check` says the model server isn't reachable | Start the server, and compare its URL with `backend.url`. |
| `check` says the proxy isn't running | Part A, step 6. The proxy window must stay open. |
| `check` says the proxy forwards elsewhere | You changed the backend. Restart the proxy. |
| `check` says a model isn't offered | The key in `pipeline.json` has a typo. Compare with `python pipeline.py models`. |
| `init` finds no models | With Just-in-Time off and `lms` missing, LM Studio may list only loaded models. Type the ids by hand, or load one first. |
| `load` or `next` fails while loading | Same as above, or the model is still downloading, or it doesn't fit in memory. Try it in the server's own chat to see the error. |
| The agent shows a connection error | The proxy or the server isn't running. |
| The agent shows *model not found* or a 400/404 | No stage is active. Run `next` (or `start <stage>`) again. The agent's model must be exactly `local-model`. |
| An error about the context length | The agent's context window must not exceed what the server loaded. Change `backend.context_length`, change the agent to match, and run `start <stage>`. |
| The agent doesn't create files | In Cline, the switch is on **Plan**. Set it to **Act**. |
| The proxy window shows nothing while the agent works | The agent's base URL isn't the proxy's. |
| The proxy logs calls under the wrong stage | You skipped `next`. Run `start <stage>` for the stage you're in. |
| The first reply takes very long | Normal: the model first reads the agent's instructions, and may first have to load. It shows as a high `ttft`. |
| A model loops or ignores its tools | Cancel, nudge it once, and note it in the report. |
| `The running proxy ... was started before the code last changed` | You updated relaybench while a proxy was running. Stop it with `Ctrl+C` and start it again. |
| `Address already in use` starting the proxy | A proxy is already running, or `proxy_port` is taken. Use that one, or choose another port in both the config and the agent. |
| You left a judge out at `init`, or want another one | Use `python pipeline.py add-judge`, at any point in a run or after its report. Add `--run <id>` for an earlier run. |
| A score file isn't counted | If the report says it belongs to no judge, use `python pipeline.py add-judge`. Otherwise, every build needs all five scores from 1 to 5 in a JSON block with a `builds` key. Compare with the scoring sheet in the packet. |
| `judge` fails with a 400 about tokens or length | The packet may be too large for that model. Try a model with more context, or add `"max_tokens"` to the judge if its API requires it. |
| The machine gets very slow | Close other heavy apps. In LM Studio, check that only one model is loaded: `lms ps`. |

# Quick reference

```bash
python pipeline.py help       # all commands
python pipeline.py status     # where am I, what's next
python pipeline.py check      # is everything installed and running

python pipeline.py new-run    # start a run
python pipeline.py next       # start the next stage, or tell me which command is next
python pipeline.py freeze     # after the plan
python pipeline.py test       # after the builds
python pipeline.py blind      # before the reviews
python pipeline.py packet     # after the reviews
python pipeline.py judge      # call the api judges
python pipeline.py add-judge  # add a judge to a run that has already started
python pipeline.py cross-check  # optional: judges check each other's reports, as notes only
python pipeline.py report     # after the judges
python pipeline.py compare    # across finished runs
```

In each stage folder: new agent session → `Follow the instructions in PROMPT.md`.

# References

- [LM Studio: `lms load`](https://lmstudio.ai/docs/cli/local-models/load) and [`lms ls`](https://lmstudio.ai/docs/cli/local-models/ls)
- [LM Studio: headless mode and Just-in-Time loading](https://lmstudio.ai/docs/advanced/headless)
- [LM Studio: listing models with the REST API](https://lmstudio.ai/docs/developer/rest/list)
- [Cline documentation](https://docs.cline.bot)
