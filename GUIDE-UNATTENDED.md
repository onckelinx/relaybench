# Unattended run guide

Start one command, walk away, and come back to a finished report. This guide goes from nothing to that
point, using **LM Studio** and the **Cline command-line agent** on macOS or Linux. Do the steps in order.

To drive each stage yourself in an editor instead, use the [manual guide](GUIDE-MANUAL.md).

**Before you start:**

- **Nobody approves anything.** Every command a model decides to run executes with your user's
  privileges, for as long as the run lasts. Use a [container](#using-a-container) or a VM if that matters
  to you, and read [Security](METHODOLOGY.md#security).
- **Status:** `run` has had one real run so far: the toy task with Cline's CLI 3.0.74 and LM Studio on
  macOS, with `paste` judges and no failed stages. Failure handling, `api` judges, other agents, `--repeat`,
  `compare` and pinned settings have only been tested with a scripted stand-in, so check your first run closely.
- **Windows** has never been tested.

## What you need

| You need | Check with |
|---|---|
| Python 3.9 or newer | `python3 --version` |
| Git | `git --version` |
| Node.js and npm, for Cline | `npm --version` |
| LM Studio, with your models downloaded | `lms ls` |

**If `lms` isn't found:** open LM Studio once, run `~/.lmstudio/bin/lms bootstrap`, and open a new terminal.

You need at least a planner and one builder, and two or more builders is the point. One model can play
several roles. Each model has to fit in memory on its own, because only one is loaded at a time.

## Connected over SSH? Start tmux first

If you close an SSH session, whatever it was running stops. `tmux` keeps your work alive on the remote
machine. Not on SSH? Skip this, and use a second terminal window wherever step 6 asks for one.

```bash
tmux new -s relay        # macOS: brew install tmux, if it isn't found
```

| To do this | Press or type |
|---|---|
| Open a new window | `Ctrl+B`, then `C` |
| Switch to the next window | `Ctrl+B`, then `N` |
| Leave everything running | `Ctrl+B`, then `D` |
| See your sessions | `tmux ls` |
| Come back later | `tmux attach -t relay` |
| Change session when you're already inside tmux | `tmux switch-client -t relay`, or `Ctrl+B`, then `S` to pick from a list |

If `tmux attach` says `sessions should be nested with care`, you're already inside tmux, so use `switch-client`.

## Set up

You only do this part once.

### 1. Get the code

```bash
git clone https://github.com/onckelinx/relaybench.git
cd relaybench
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Check:** your prompt now starts with `(.venv)`.

Run `source .venv/bin/activate` again in **every** new terminal or tmux window, from the `relaybench`
folder. If you see `command not found` for `pip` or `python`, this is almost always why.

**Optional:** `python -m pytest` runs the whole pipeline against a mock server, in about 4 minutes.

### 2. Start the model server

```bash
lms server start
```

**Check:** `curl http://127.0.0.1:1234/v1/models` prints text starting with `{`.

We recommend turning off **Just in Time Model Loading**, under **Developer → Server Settings** in LM
Studio. With it on, a wrong model name can quietly load a model with a small context.

### 3. Install the agent

The agent gives models their tools for editing files and running commands. The command-line version of
Cline keeps its own settings, separate from the VS Code extension's, so set it up even if you already use
the extension.

```bash
npm i -g cline
```

npm may print `deprecated` and `install-scripts` warnings. You can ignore them if `cline --help` works.

### 4. Write your configuration

```bash
python pipeline.py init
```

It asks one question at a time, and the value in `[brackets]` is what you get by pressing Enter. It writes
`pipeline.json`. If you make a mistake, press `Ctrl+C` and start again: nothing is saved until the end.

| Question | What to answer |
|---|---|
| Choose (model server) | `1` for LM Studio |
| Server URL | Enter |
| Environment variable with the server's API key | Enter |
| Context length in tokens | Enter, unless you know you want another value |
| GPU offload | **Enter**, for `max`. `off` means no GPU, so models run far slower. |
| Port for the logging proxy | Enter |
| Planner | One number from the list of models, such as `3` |
| Builders | Numbers separated by commas, such as `6,7` |
| Reviewers | Numbers separated by commas, or Enter for none |
| Judge 1 type | See below |
| Label | A short name for this judge, such as `chatgpt` |
| Judge 2 type | Another judge, or Enter to finish the list |
| Choose (agent) | `2` for Cline's command-line agent. See [Other agents](#other-agents) for `3`. |
| Runs folder | Enter |
| Default task | Enter for the toy task, which is best for a first run |

When it finishes, it prints what to do next, and the steps below go through the same things.

**Which kind of judge?**

- **`paste`** is the simplest. You paste a packet into a web chat, such as ChatGPT or Claude, and paste
  the answer back. `run` does everything else first, then lists what's left for you.
- **`api`** lets the run finish completely by itself. It needs an OpenAI-compatible API and a key. `init`
  asks for the base URL, the model id and the **name** of an environment variable holding the key. Set
  that variable with `export` in the same window you start `run` from, or `run` will stop and say so.
- **`human`** is a person who fills in a score file.

### 5. Connect the agent to the proxy

`init` printed this command, with your port in it. It connects Cline to the logging proxy, **not** to LM
Studio. You only need to do it once:

```bash
cline auth --provider openai --apikey dummy --baseurl http://127.0.0.1:1235/v1 --modelid local-model
```

`dummy` is fine, because the key is never used. `local-model` is the one name the agent ever asks for, and
the harness decides which real model answers to it. This worked with Cline 3.0.74. If it's rejected, your
version's options differ: compare with `cline auth --help`. To see what Cline will use, run `cline config`.

`init` set Cline up to run as `cline -y`. `-y` is its flag for unsupervised runs, and it worked in a real run
with version 3.0.74. Check that `cline --help` still lists it in yours.

### 6. Start the logging proxy

Open a **second** window (in tmux: `Ctrl+B`, then `C`). In it:

```bash
cd relaybench                     # if you're not already there
source .venv/bin/activate
python proxy/llm_proxy.py
```

**Leave it running**, and go back to your first window (in tmux: `Ctrl+B`, then `N`). Each model call adds
a line in this window.

### 7. Test the agent on a small job

A wrong setting would otherwise only show up as failed stages, later on. This test calls the agent exactly
as `run` will. Replace `<label>` with any model's label from `pipeline.json`:

```bash
python pipeline.py load <label>
mkdir -p /tmp/agent-test && cd /tmp/agent-test
cline -y "Create a file hello.txt containing hi, then run python3 --version and save its output in version.txt" < /dev/null > out.txt 2>&1
cat hello.txt version.txt
cd -
```

**Check both:**

1. Both files print. That shows the agent can write files and run commands.
2. New lines appeared in the **proxy window**. That shows the agent is using your local model.

| What you see | What it means |
|---|---|
| Files appear, but no new proxy lines | **Don't start a run.** The agent is using some other model. Go back to step 5. |
| `hello.txt` appears, but `version.txt` doesn't | `-y` allows only a limited set of tools, which may be why. Change `"-y"` to `"--auto-approve", "true"` under `agent` in `pipeline.json`, and test again with those flags. |
| Neither file appears | Read `/tmp/agent-test/out.txt`. |

## Run

### 8. Start the run

```bash
python pipeline.py check
python pipeline.py run
```

**Check:** `check` ends with `Everything looks good.` Fix any `[FAIL]` line before starting the run.

To use another task: `python pipeline.py run --task cron-next`. The tasks are listed in
[tasks/README.md](tasks/README.md).

### 9. Leave it running

- **Over SSH:** press `Ctrl+B`, then `D`. You can disconnect now. To come back: `tmux attach -t relay`.
- **Laptop:** **leave the lid open.** `run` keeps the machine awake, but on a MacBook, and on most Linux
  laptops, closing the lid sleeps it anyway. See [Keeping the machine awake](#keeping-the-machine-awake).
- **How long?** A toy run often takes 30–60 minutes, depending on your models and hardware.

A line is printed as each stage ends, with its outcome, time and number of model calls:

```
  [ OK ] plan: done after 3.5 min, 12 model call(s)
```

Hidden test scores stay hidden until the report, so you stay blind to which model did what. To see where a
run has got to, and what its agent is doing, type this in another window, with the environment activated:

```bash
python pipeline.py status
tail -n 30 "$(ls -t runs/$(cat runs/CURRENT)/agent-logs/*.log | head -1)"
```

Agent logs show which model wrote what, so if you're a `human` judge, don't read them before you've scored.

### 10. Judges and the report

When `run` is done, it writes `REPORT.md` in the run's folder and prints where it is. `api` judges are
already included. If you have `paste` or `human` judges, it lists them as still open.

**For each `paste` judge:**

1. Start a **new** chat with that judge, using the same model every time.
2. Attach `4-judging/judge-packet.md` from the run's folder, and send `Follow the instructions in this file.`
3. Copy the **complete** answer with the chat's copy button, and paste it into
   `4-judging/scores-<label>.md`, below the first line. Copying only the JSON leaves out the reasoning.

`run` creates that score file when it makes the packet, after all the reviews, so `4-judging` doesn't exist
before then.

If the answer's format is off, ask: `Rewrite your answer in exactly the format under "Required output" in the scoring sheet.`

**On SSH?** The packet is on the remote machine and your browser isn't. Copy it across, from the computer
with the browser:

```bash
scp <you>@<remote-machine>:<path-to-relaybench>/runs/<run-id>/4-judging/judge-packet.md .
```

You can edit score files on that computer too. Fetch one, and put it back when it's done:

```bash
scp <you>@<remote-machine>:<path-to-relaybench>/runs/<run-id>/4-judging/scores-<label>.md .
scp scores-<label>.md <you>@<remote-machine>:<path-to-relaybench>/runs/<run-id>/4-judging/
```

Use the same `<run-id>` both ways, which `cat runs/CURRENT` shows on the remote machine. Build letters are
shuffled for each run, so a score file put in the wrong run replaces that run's file, and its scores count for
the wrong models.

**For each `human` judge:** fill in `4-judging/scores-<label>.md`, replacing every `null`.

Then rebuild the report:

```bash
python pipeline.py report
```

**Check** these in `runs/<run-id>/REPORT.md`:

- **Unattended stages:** every stage should say `done`, with model calls above zero. The *Notes* column
  shows anything that went wrong. See [When a stage goes wrong](#when-a-stage-goes-wrong).
- **Attempts:** above 1 means a stage restarted after an interruption, so treat its numbers with care.
- **Hidden tests:** the *Own tests* column shows what happened when the harness ran each build's own tests.
  Judges see this too. `none found` means pytest found no tests in that build.
- **Integrity flags:** whether a model may have seen something it shouldn't have.
- **The top of the report:** hardware, agent version and quantisation are filled in where the system says.
  `not reported` means it couldn't tell, so add those under *My notes*.
- **My notes:** add your hardware, power setup and agent version, then run `python pipeline.py report`
  again. It keeps your notes.

Finally, stop the proxy with `Ctrl+C` in its window. In tmux, `tmux kill-session -t relay` closes everything.

## Next time

Setup is done, so a new run only needs this:

```bash
tmux new -s relay                 # if you're on SSH
cd relaybench
source .venv/bin/activate
lms server start
python pipeline.py run --task toy-bepay
```

`run` starts the proxy by itself when none is running, and stops that one when it ends. Once a run has a
report, the next `run` starts a new one. To change models or judges for new runs, run `python pipeline.py init`
again. It offers to keep your agent, so you can press Enter at that question. To add a judge to a run that has
already started, use `python pipeline.py add-judge`.

**Comparing runs?** Keep the agent, its version, the judges and the power setup the same. Don't compare
scores directly with manual runs, because a command-line agent can behave differently from an editor one.
In our real run, for example, Cline's CLI sent no temperature or output limit, so each model used its
defaults, while the manual guide sets a fixed output limit. The report records the mode and command you
used, and *Settings sent to the model* shows what reached the models. To make those the same, see
[Pinning model settings](#pinning-model-settings).

## Several runs in a row

One run is one sample, and models are nondeterministic, so a small difference can be chance. To do several
complete runs, each with its own new plan, and compare them at the end:

```bash
python pipeline.py run --task toy-bepay --repeat 3
```

If a run is unfinished, it's resumed and counts as the first of the three, and the others use its task. To
compare finished runs at any time:

```bash
python pipeline.py compare --task toy-bepay
```

For each model, this shows the runs, failed builds, hidden tests passed, judge totals and speed, and saves
the table in `results/`. A failed build counts as 0 tests passed. Runs with a different task version, prompts,
scoring sheet, agent or pinned settings go in separate tables, because their results don't compare.

- **Judges:** with `paste` judges, each run waits for two more chats from you, so judging many runs by hand
  is a lot of copying. `api` judges suit this better. Without judges, you still get tests and speed for every
  run, and any you judge later show up in `compare`. To judge an earlier run, add `--run <id>` to `status`,
  `add-judge`, `judge` and `report`.
- **Time:** multiply. A toy run often takes 30–60 minutes.
- **The same plan for several builds:** to measure one model against itself with the planner held still,
  list it twice under `builders` in `pipeline.json`, with a different `label` each time. `init` can't do this,
  so it's a hand edit.

---

# Reference

## When a stage goes wrong

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
| A model won't load | That stage fails, and the run carries on. A planner that won't load stops the run. |
| An `api` judge fails | The report is still written. Run `python pipeline.py judge`, then `report`. |
| You run `skip` | That stage stops and the run moves on. See [If a model gets stuck](#if-a-model-gets-stuck). |

Each stage's agent output is in `agent-logs/` in the run's folder, which is the first place to look.

The exit code is 0 when the report is written, even if some stages failed, so always read the report. It
is 1 if the plan or every build failed or an `api` judge failed, and 130 if the run was stopped.

## If a model gets stuck

A model can loop until it reaches `stage_timeout_s`. If you're watching and it's clearly stuck, stop just that
model, from another window with the environment activated:

```bash
python pipeline.py skip "stuck in a loop on its own tests"
```

The agent and anything it started are stopped, and the run moves on to the next stage. It's treated the same
as reaching the limit: whatever the model built is committed, tested, reviewed and judged. The report's
*Notes* column says `stopped by hand: <note>`, because this was your decision and not the model's result.
Judges don't see the note.

- A review that hasn't been written yet fails, and the run carries on.
- A planner needs `skip --force`, because the run ends without a plan.
- The note is optional.
- `skip` needs someone awake. Overnight, `stage_timeout_s` is the limit, so lower it if time matters. A cap on
  `max_tokens` also helps with a single answer that never ends. See [Pinning model settings](#pinning-model-settings).

## If a run is interrupted

`Ctrl+C`, or a dropped connection outside tmux, stops the agent and any proxy `run` started. To continue:

```bash
python pipeline.py run
```

It resumes where it was. The unfinished stage starts over in its folder, with whatever files are already
there, and the report's *Attempts* column shows that. To abandon the run instead:
`python pipeline.py new-run`, then `python pipeline.py run`.

If `run` itself was killed outright, an agent may still be working. `run` tells you and won't start until
you've stopped it.

## Agent settings

`init` writes these under `agent` in `pipeline.json`. Edit that file to change the limits. Running `init` again
keeps what you've set if you choose to keep your agent.

| Setting | Meaning | Default |
|---|---|---|
| `command` | The agent's command line. It runs once per stage, inside the stage's folder. | Required |
| `stage_timeout_s` | Most time one stage may take. Raise it for a slow model on a hard task. | `3600` |
| `idle_timeout_s` | Stop a stage after this long with no model call running or starting. | `600` |

In `command`, `{base_url}`, `{model}` and `{folder}` are filled in for you. The agent's environment also has
`OPENAI_BASE_URL`, `OPENAI_API_BASE`, `OPENAI_MODEL` and a dummy `OPENAI_API_KEY`, so a real key in your
shell doesn't reach an agent under test.

**Keep API keys out of `command`**, because the report prints it.

## Pinning model settings

The report's *Settings sent to the model* table shows what each stage's calls asked for. Cline's CLI sent no
temperature or output limit in our real run, so each model used its own defaults. To make them the same for
every model, add this to the top level of `pipeline.json`:

```json
"request_overrides": { "max_tokens": 16384 },
```

The proxy then replaces whatever the agent sends, on every model call, and the report marks those values as
*pinned*. It only takes `temperature` and `max_tokens`. A new run picks up a change; a run in progress keeps
what it started with. Running `init` again keeps these settings. A proxy started before the code last changed
may ignore them, so `run` won't start until you've stopped that proxy.

- **`max_tokens`** is the useful one: too low cuts answers off, so choose a generous value.
- **`temperature`** is usually better left out, so that each model uses its own default. Some models loop at
  a temperature of 0, so check their model cards before pinning it.

## Other agents

Any agent works if it has a command-line mode, can use a custom OpenAI-compatible base URL, can edit files
and run commands, and never stops to ask a question. In `init`, choose `3` and type its command, with any flag
it needs to run without asking. The instructions are added at the end of the command, so it needs to accept
its prompt there. Point it at `http://127.0.0.1:1235/v1` with model `local-model`, then do the test in step 7
with that agent. Common agent folders are left out when builds are copied for review. If yours leaves other
files that name the model, watch for warnings in the report's *Integrity flags*.

## Other model servers

Not using LM Studio? See [Backends](GUIDE-MANUAL.md#backends). Choose `2` for the first question in `init`.
Skip `lms` in steps 2 and 7 of this guide, and start your own server instead.

## Using a container

`command` can start a container, so the agent sees only its stage folder. This is the way to stop an agent
from reaching the hidden tests. A sketch, **not tested**, and how the network reaches the proxy depends on
your setup:

```json
"command": ["docker", "run", "--rm", "--network=host", "-v", "{folder}:/work", "-w", "/work",
            "my-agent-image", "my-agent", "--some-auto-approve-flag",
            "Follow the instructions in PROMPT.md"]
```

The hidden tests still run on your machine, outside the container, when `run` reaches the test step.

## Keeping the machine awake

`run` keeps the machine awake by itself where it can, with `caffeinate -is` on macOS and `systemd-inhibit` on
Linux. `--allow-sleep` turns that off, and it warns if it can't. If the machine sleeps anyway, that time
doesn't count against the stage's limits, and the report marks the stage's wall time as unreliable.

**On any system:**

- **Keep power the same** across runs you want to compare. Battery, power-saving modes and heat all change
  speed. Note them under *My notes*.
- **Check a battery will last,** or plug in.

### macOS

- **Closing a MacBook's lid sleeps it anyway**: `caffeinate` doesn't prevent that. Leave the lid open.
  Apple's own exception is closed-lid use with an external display and power.
- **The screen can be off.** A real run worked over SSH with the screen off and the lid open, with
  `caffeinate -is` keeping the Mac awake. `-i` stops idle sleep, and `-s` also stops system sleep when plugged in.
- **To keep the Mac awake outside a run,** for example while you set things up over SSH, this carries on after
  you disconnect:

  ```bash
  nohup caffeinate -is >/dev/null 2>&1 &
  ```

  It keeps running until you stop it, so remember to, with `pkill caffeinate`. That also stops the one `run`
  is using, so don't do it during a run.
- **Low Power Mode** changes speeds. Check it's the same in System Settings → Battery for runs you compare.
- To stop sleep without `caffeinate`, look in System Settings → Battery or Energy. The wording varies by
  Mac and macOS version.

### Linux

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

Windows has never been tested. `run` can't keep it awake, so it warns and carries on. This turns off sleep
and hibernation when plugged in (use `-dc` for battery), until you set them back:

```
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

## Troubleshooting

| Problem | What to do |
|---|---|
| `pip: command not found` or `python: command not found` | Activate the environment: `source .venv/bin/activate`, from the `relaybench` folder. |
| `pipeline.json not found` | Run `python pipeline.py init`, from the `relaybench` folder. |
| `request_overrides can only hold ...` | See [Pinning model settings](#pinning-model-settings). |
| `pipeline.json is not valid JSON` | A hand edit left out a comma or quote. The message gives a line number. Or run `init` again. |
| `Unattended runs need a command-line agent` | Run `python pipeline.py init` again, and choose an agent. |
| `The agent command ... was not found` | The agent isn't installed, or isn't on your `PATH`. Try the first word of `command` by itself. |
| `The model server is not reachable` | Start it: `lms server start`. |
| `Not offered by the server` | A model id in `pipeline.json` has a typo. Compare with `python pipeline.py models`. |
| `check` says the proxy isn't running | See step 6, or skip it: `run` starts the proxy itself. |
| `check` says the proxy forwards elsewhere | You changed the backend. Restart the proxy. |
| `The running proxy ... was started before the code last changed` | You updated relaybench while a proxy was running. Stop it with `Ctrl+C` in its window. `run` starts a fresh one. |
| `Address already in use` starting the proxy | A proxy is already running, perhaps in another tmux session, so look with `tmux ls`. Use that one, or choose another port in both `pipeline.json` and your agent. |
| A stage fails with "no model calls reached the proxy" | The agent isn't using the proxy. See step 5, and read its log in `agent-logs/` in the run's folder. |
| `... is already working here` | A `run` is still going, probably in another window. Look with `tmux ls` and `tmux attach -t relay`. |
| `run` says an agent may still be working | A killed `run` left it behind. Stop that process. If the pid belongs to something else, delete `agent.pid` in the runs folder. |
| `set the environment variable ... first` | An `api` judge needs its key. Use `export` in this window, then start again. |
| `Run ... isn't finished` | You gave `--task` while another run is unfinished. Finish it with `python pipeline.py run`, or use `new-run --task <name>` and then `run`. |
| A certificate error from a server or an `api` judge | Apple's built-in Python uses an old TLS library, which may be the cause. Check with `python -c "import ssl; print(ssl.OPENSSL_VERSION)"`. If it says `LibreSSL`, try making the `.venv` with a Python from python.org or Homebrew. |
| A score file isn't counted | Every build needs all five scores from 1 to 5 in a JSON block with a `builds` key. Compare with the scoring sheet in the packet. If the report says it belongs to no judge, add that judge with `python pipeline.py add-judge`. |
| You left a judge out at `init`, or want another one | Use `python pipeline.py add-judge`, at any point in a run or after its report. Add `--run <id>` for an earlier run. |
| A model is stuck in a loop | See [If a model gets stuck](#if-a-model-gets-stuck). |
| Something else | More answers are in the [manual guide's troubleshooting](GUIDE-MANUAL.md#troubleshooting). |
