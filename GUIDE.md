# relaybench guide

This guide takes you from nothing to a finished test run:

```
planner model ──► plan.md ──┬──► builder 1 builds ──► reviewer reviews ──┐
                  (frozen)  └──► builder 2 builds ──► reviewer reviews ──┴──► judges score
```

It uses **LM Studio** as the model server and **Cline** in VS Code as the agent, as a worked example.
Neither is required: see [Backends](#backends) and [Other agents](#other-agents). To do a whole run with
one command and no editor, see [Unattended runs](#unattended-runs).

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

Then:

```bash
pip install -r requirements.txt
```

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

It asks for your server, lists the models it finds, and asks which plays which role, who judges, and
where runs go. It writes `pipeline.json`, which Git ignores. Run it again whenever you like.

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
| **Max Output Tokens** | one fixed value for every stage, such as `16384` |
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

The scores stay hidden so you stay blind. It warns you if a build timed out or lost tests. To see the
numbers anyway, use `test --show`. Full output is in the run's `tests` folder.

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

This writes `4-judging/judge-packet.md` with the task, the plan, every build with its test results,
and every review, labelled only by letter. Then, for each judge:

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

### 9. Write the report

```bash
python pipeline.py report
```

`runs/<run-id>/REPORT.md` shows speed per stage, the settings the agent sent, hidden test results,
scores per judge with averages, integrity flags, and the blind key. Fill in **My notes**, including
your hardware and agent settings, and run `report` again. It keeps your notes and updates the copy in
`results/`. Reports name your models, so `results/` is ignored by Git. Commit them only if you want to.

---

# Part C — After the run

**Run again.** There is nothing to reset: `python pipeline.py new-run` makes a new folder. Old runs
stay in `runs/` until you delete them.

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

# Unattended runs

`python pipeline.py run` does Part B on its own, using an agent with a command-line mode. The manual flow
is unchanged, and you can use either for any run.

**Status: tested with a scripted stand-in only.** `run` has not been used with a real agent or a real
model yet, so check your first run closely.

### Set it up

1. **Install a command-line agent and point it at the proxy** (base URL `http://127.0.0.1:1235/v1`, model
   `local-model`, any non-empty key). It must be able to run without asking anything, because nobody is
   there to answer.
2. **Add it to `pipeline.json`:**

   ```json
   "agent": {
     "command": ["my-agent", "--some-auto-approve-flag", "Follow the instructions in PROMPT.md"],
     "stage_timeout_s": 3600,
     "idle_timeout_s": 600
   }
   ```

   `command` runs once per stage, inside the stage folder. `{base_url}`, `{model}` and `{folder}` in it are
   filled in. The agent's environment also has `OPENAI_BASE_URL`, `OPENAI_API_BASE`, `OPENAI_MODEL` and a
   dummy `OPENAI_API_KEY`, so a real key in your shell doesn't reach an agent under test.
3. **Prefer `api` judges** if the run should finish without you.
4. Run `python pipeline.py run`. To set a task: `python pipeline.py run --task cron-next`.

Its progress goes to the terminal. Each stage's agent output is in `<run>/agent-logs/`.

**Cline, for example.** Its [CLI reference](https://docs.cline.bot/cli/cli-reference.md) documents `cline auth`
with `--provider`, `--apikey`, `--baseurl` and `--modelid`, and `--auto-approve true` for running. Its other
pages don't fully agree on these flags, and we haven't run it, so **confirm with `cline --help`**. The
command-line version keeps its own settings, separate from the VS Code extension's.

A run on a task with a slow model can pass the one-hour default, so set `stage_timeout_s` to suit.

### When a stage goes wrong

Nothing is retried, because that would give one model a chance the others didn't get.

| What happened | What `run` does |
|---|---|
| No plan, or an empty one | Stops, since everything depends on it. Fix the cause and run it again. |
| The agent made **no model calls through the proxy** | The stage fails, even if files appeared. The agent may be using another model, so the work can't be credited to this one. Plans and reviews are renamed `*.rejected`. |
| A build changed nothing | That build fails: no tests, review or judging, and it shows as failed in the report. |
| A build has uncommitted work | The harness commits it, and the report says so. |
| A review is missing or empty | That review fails. Judges see "No review was written". |
| The agent exits with an error, but left its output | The output counts, and the report says so. |
| Nothing has happened for `idle_timeout_s`, or the stage hits `stage_timeout_s` | The agent and everything it started are stopped. Any output counts, and the report says so. A model call in progress is never "idle", however slow, so only `stage_timeout_s` can end a stuck call. |
| A model won't load | That stage fails, and the run carries on. |
| An `api` judge fails | The report is still written. Run `python pipeline.py judge`, then `report`. |

The exit code is 0 when the report is written, even if some stages failed, so read the report's
*Unattended stages* section. It is 1 if the plan or every build failed or an `api` judge failed, and 130 if
stopped.

**Interrupted?** Ctrl+C stops the agent and the proxy `run` started. `python pipeline.py run` then resumes
that run: the unfinished stage starts over in its folder, with whatever files are already there, and the
report's *Attempts* column shows it. To drop the run instead: `python pipeline.py new-run`, then `run`.
A run counts as finished once it has a report, so the next `run` starts a new one. If `run` itself was killed
outright, an agent may still be working. `run` tells you and waits until you've stopped it.

### Using a container

`command` can start a container, so the agent sees only its stage folder. This is the way to stop an
agent from reaching the hidden tests. A sketch, **not tested**, and how the network reaches the proxy
depends on your setup:

```json
"command": ["docker", "run", "--rm", "--network=host", "-v", "{folder}:/work", "-w", "/work",
            "my-agent-image", "my-agent", "--some-auto-approve-flag",
            "Follow the instructions in PROMPT.md"]
```

The hidden tests still run on your machine, outside the container, when `run` reaches the test step.

# Long and unattended runs

A run takes a long time, and a sleeping machine stops it. This applies to manual runs too.

`run` keeps the machine awake by itself where it can (`--allow-sleep` turns that off), and warns if it
can't. It uses `caffeinate` on macOS and `systemd-inhibit` on Linux. If the machine sleeps anyway, that time
doesn't count against the stage's limits, and the report marks the stage's wall time as unreliable.

**Whichever you use, plan for these:**

- **On SSH, use `tmux`** (or `nohup`), or a dropped connection ends the session. `run` then stops
  cleanly and can resume, but that won't help you overnight.
- **Keep power the same** across runs you want to compare. Battery, power-saving modes and heat all change
  speed. Note them under *My notes*.
- **Check a battery will last,** or plug in.

### macOS

```bash
caffeinate -i python pipeline.py run     # what `run` does for you
caffeinate -i                            # for a manual run: leave this open in a terminal, Ctrl+C to end
```

- **Closing a MacBook's lid sleeps it anyway**: `caffeinate` doesn't prevent that. Leave the lid open.
  Apple's own exception is closed-lid use with an external display and power.
- **Low Power Mode** changes speeds. Check it's the same in System Settings → Battery for runs you compare.
- To stop sleep without `caffeinate`, look in System Settings → Battery or Energy. The wording varies by
  Mac and macOS version.

### Linux

```bash
# for a manual run: leave this open in a terminal, Ctrl+C to end
systemd-inhibit --what=idle:sleep --who=relaybench --why="long run" --mode=block sleep infinity
```

- **Closing a laptop's lid suspends it by default.** Leave it open, or hold that off for one run as well:

  ```bash
  systemd-inhibit --what=handle-lid-switch --why="long run" --mode=block python pipeline.py run
  ```

  To change it permanently, set `HandleLidSwitch=ignore` in `/etc/systemd/logind.conf`, and
  `HandleLidSwitchExternalPower=ignore` too if it's set. Read `man logind.conf` first, because this affects
  every session.
- **Desktops have their own settings.** If the machine still suspends, turn off automatic suspend in your
  desktop's power settings. The names vary. We've checked that the lock is held, but not on every desktop.
- **Power profiles** (such as `power-saver`) change speeds. Use the same one for runs you compare.
- Without systemd, `run` warns and carries on, and you'll need your desktop's settings.

### Windows

`run` can't keep Windows awake, so it warns and carries on. Windows has never been tested with relaybench.
This turns off sleep and hibernation when plugged in (use `-dc` for battery), until you set them back:

```
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

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

An agent with a command-line mode can also do a whole run by itself: see [Unattended runs](#unattended-runs).

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
| `python: command not found` | Try `python3`, or activate the virtual environment. |
| `pipeline.json not found` | Run `python pipeline.py init`, from the repo folder. |
| `pytest is not installed` | Activate the virtual environment, then `pip install -r requirements.txt`. |
| `lms: command not found` | See Part A, step 4. Without `lms`, see [Backends](#backends). |
| `check` says the model server isn't reachable | Start the server, and compare its URL with `backend.url`. |
| `check` says the proxy isn't running | Part A, step 6. The proxy window must stay open. |
| `check` says the proxy forwards elsewhere | You changed the backend. Restart the proxy. |
| `check` says a model isn't offered | The key in `pipeline.json` has a typo. Compare with `python pipeline.py models`. |
| `run` says an agent may still be working | A killed `run` left it behind. Stop that process. If the pid belongs to something else, delete `agent.pid` in the runs folder. |
| A stage fails with "no model calls reached the proxy" | The agent isn't using the proxy. Check its base URL, and see its log in `<run>/agent-logs/`. |
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
| `Address already in use` starting the proxy | A proxy is already running, or `proxy_port` is taken. Use that one, or choose another port in both the config and the agent. |
| A score file isn't counted | Every build needs all five scores from 1 to 5 in a JSON block with a `builds` key. Compare with the scoring sheet in the packet. |
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
python pipeline.py report     # after the judges

python pipeline.py run        # all of it, unattended (needs "agent" in pipeline.json)
```

In each stage folder: new agent session → `Follow the instructions in PROMPT.md`.

# References

- [LM Studio: `lms load`](https://lmstudio.ai/docs/cli/local-models/load) and [`lms ls`](https://lmstudio.ai/docs/cli/local-models/ls)
- [LM Studio: headless mode and Just-in-Time loading](https://lmstudio.ai/docs/advanced/headless)
- [LM Studio: listing models with the REST API](https://lmstudio.ai/docs/developer/rest/list)
- [Cline documentation](https://docs.cline.bot)
