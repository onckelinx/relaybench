# Methodology

What relaybench measures, how it tries to keep the measurement fair, and where it falls short.
Read the [known limitations](#known-limitations-and-biases) before drawing conclusions from a run.

## Pipeline vs head-to-head

**Head-to-head** gives every model the same prompt and compares the outputs. It is simple and clean,
and it answers "which model is best at doing the whole job alone?"

**A pipeline** splits the job into roles, the way people often use local models: one to think, one
to write, one to check. It answers different questions:

- Which model should fill which role? Planning and implementing reward different strengths, and
  speed matters differently in each.
- Can a model work from someone else's plan, and can it explain where it departed from it?
- Can a reviewer find real problems in code it didn't write?

relaybench combines the two. **The run as a whole is a pipeline, and each role is a head-to-head:**
every builder gets exactly the same input, and every reviewer reviews every build.

What this design gives up:

- **Builders share the planner's mistakes.** A bad plan handicaps everyone, which is fair between
  builders but can hide a builder that would have done better alone.
- **It doesn't measure end-to-end ability.** A model that builds well from a plan may plan badly for
  itself. To approximate that, run once per candidate with that model as the planner.
- **Comparing planners takes several runs.** A run has one planner, and its effect shows up only
  through the builds, mixed with everything else.

## The stages

| Stage | Who | Input | Output |
|---|---|---|---|
| Plan | One planner model | `TASK.md`, any starter code | `plan.md` |
| Freeze | Harness | `plan.md` | A read-only plan and one Git repo per builder |
| Build | Each builder model | Task, frozen plan, starter code | A committed implementation with its own tests |
| Test | Harness | Each build | Hidden test results, and the results of the build's own tests |
| Blind | Harness | Builds | Letters A, B, C… in random order |
| Review | Each reviewer model | One anonymous build | `REVIEW.md` |
| Judge | Cloud models and/or people | A packet with task, plan, anonymous builds with numbered code, test results, then reviews | Scores |
| Report | Harness | All of the above, proxy log | `REPORT.md`, which reveals who was who |

## Frozen plan

Once `freeze` runs, the plan is read-only in the run folder, and each builder gets an identical copy
committed as the starting point of its own Git repo. This removes the largest source of variation
between builders: differing specifications.

You may fix obvious problems in the plan before freezing. If you do, write it down, because it is
then partly your plan. Builders can technically still edit their copy. `blind` compares it with the
frozen one, flags any change in the report, and gives reviewers and judges the frozen version.

Running `freeze --force` starts the builds again and moves everything that depended on the old
builds (tests, reviews, scores) to a `_stale-<time>` folder, so old results can't be credited to the
new builds.

## Blind A/B review

Knowing the author changes how people and models judge code. So:

- Builds get letters **in random order**, stored in `blind-key.json`. With more than 26 builders,
  letters continue with AA, AB and so on.
- Reviewers work on a copy with no Git history, in a folder named only by letter. Build folders are
  numbered, not named after their models, so a path can't give a model away.
- Known agent leftovers that can name a model (for example `.aider*` and `.cline*`) are not copied.
- Judges see starter code once, and each build lists the starter files it left unchanged, so the
  developer's own work stands out. Reviewers get the whole project, minus an unchanged starter README.
- `blind` and `packet` search for model labels in what reviewers and judges will read, and warn you.
  They name the build, without saying which model, so that you stay blind.
- `test` doesn't print per-model scores unless you pass `--show`: label plus score there, and letter
  plus score in the packet, would un-blind you.

The key is only revealed in the final report.

## Hidden tests

Each task has acceptance tests the models are told nothing about. They are the most objective signal
in a run, and judges get the results as strong evidence for correctness.

- Tests run with pytest against a **temporary copy** of the build, so they can't leave files behind
  that affect the next build.
- The denominator comes from collecting the tests separately, not from what a build managed to run.
  A build that hangs or can't be imported scores 0 of the full count and is marked `timeout` or
  `collection error`, rather than getting a misleading "1 of 1".
- A hash of the task folder is stored when the run starts. `test` warns if the task has changed since.
- Each included task's tests are checked against a reference solution and against deliberately wrong
  variants that must fail specific tests. Those checks are in `tests/test_e2e.py`.
- The report groups failing tests into areas by name, because one mistake can fail several tests. In a real
  run, non-ASCII digits were 4 of 41 tests, and the top five builds each failed for a single reason. The raw
  count stays the main result, and the areas change no score. They are only a guide, because a shared name
  doesn't prove a shared cause.
- Each build's own tests also run, on their own temporary copy. They don't affect its hidden test score,
  and judges see the result as evidence for scoring those tests.

Tests only check what they check. A build can pass by accident or fail on an ambiguous reading of the
task. That is why judges are told to read the code too. In an [earlier real run](https://github.com/onckelinx/relaybench/blob/86a38bb/examples/REPORT.md), both judges found
a crash in a build that had passed every hidden test, and the tests were extended afterwards.

## What reviewers and judges are held to

Vague reviews and loose scoring were the weakest part of the first real run, so the rules are explicit:

- **Reviewers** give evidence for each issue: an input or command, what should happen, and what actually
  happens. Only pure code-quality issues may skip this.
- **Judges** see numbered code, so that citations from them and from reviewers can be checked. Reviews come
  after all the builds, and judges score the builds first.
- **Counting** follows fixed rules in the scoring sheet: one root cause counts as one issue, each reported
  issue is either real or a false alarm, and a missed issue must break a requirement or a hidden test.
- **Edge cases** are capped: a build that crashes where the task forbids it scores at most 3. This was the
  judges' biggest disagreement in a real run, when one gave 4 and the other 2 for the same builds.
- **Own tests** are run with pytest, and judges are told so, because pytest also collects plain `test_`
  functions that `unittest` would miss.

An optional `cross-check` gives judges each other's reports, under neutral names, with the hidden test source.
In a real run this found a mix-up between two builds and bugs that one judge had missed. It also showed why
the answers are only notes: judges who couldn't see the test source claimed that existing tests were missing,
and scores from a second round would be anchored on the other judge.

Changing the prompts or the scoring sheet changes their hashes in the report, and `compare` keeps runs with
different hashes or different pinned settings apart.

## Metrics via proxy

Agents talk to `proxy/llm_proxy.py`, not to the model server, so timing is measured the same way for
every model, independent of the agent's own display. For each model call it records:

| Metric | Meaning |
|---|---|
| Input and output tokens | From the server's usage data where it provides it |
| Time to first token (TTFT) | From receiving the request to the first generated token, thinking included. Includes prompt processing, and model loading when a server loads on demand. |
| Generation speed | Output tokens divided by the time from first token to the end |
| Model time | Time spent waiting for the model |
| Wall time | First to last model call in a stage, so it includes tools, approvals and any gap between restarts |
| Attempts | How often the stage was started. A stage that needed restarts is a finding, not noise. |
| Temperature, max tokens | What was sent to the model, per stage: the agent's values, or `request_overrides` if you pinned them |

The proxy changes requests in as few ways as possible. It adds `stream_options.include_usage` so
token counts come from the server, rewrites the model name on servers without a load step, and applies
`request_overrides` if you set any. If a server still sends no usage data, tokens are counted as stream chunks, marked `~` in the report, and
should not be compared across servers.

Calls that were cancelled still count: a model that loops until you stop it is logged with what it
produced.

## Controlled variables

| Variable | How it is held constant |
|---|---|
| Plan | Frozen; identical copy per builder |
| Task, prompts, scoring | Same files for everyone; hashes recorded in the report, with the harness commit |
| Model choice | `run.json` keeps a snapshot, so editing the config mid-run can't change who plays what |
| Model in memory (LM Studio) | Everything is unloaded before each load, so only one model is in memory at a time |
| Context length (LM Studio) | Set by the harness on every load. On other servers it is **not** controlled, and the report says so. |
| Agent | Same name and URL for every stage. Its settings are yours to keep fixed; see below. |
| Temperature and output limit | Whatever the agent sends, which the report shows. `request_overrides` can pin them for every call. |
| Workspace | A fresh folder and a new agent session per stage |
| Judges | A new chat per judge per run. Same judge prompt and scoring sheet. |

**You have to hold these constant yourself, and note them under *My notes* in the report:**

- agent settings, including auto-approval and how many tool calls it may make, plus temperature and output
  limit unless you pin them with `request_overrides`
- power mode and what else is running (on laptops, plugged in or on battery changes speed)
- server version

The report records hardware, the agent's version in unattended runs, and quantisation where LM Studio
reports it. Keep those the same too, and add any it lists as `not reported` to your notes.
- your own interventions: approvals, nudges, manual commits

If one model waits for your approval and another doesn't, their wall times can't be compared. Use the
same auto-approve settings for every stage.

**Unattended runs remove some of these and add others.** With `run`, nobody approves, nudges or commits,
and nobody fixes the plan, so every model gets the same treatment. That also means a bad plan goes
straight to every builder. The agent is now a bigger part of what you measure: a command-line agent can
behave differently from an editor one, even from the same project, so don't compare scores across agents
or modes. There are no retries, because a retry would give one model a chance the others didn't get.
A restart after an interruption continues from the files already there, and the report's *Attempts*
column shows it. Treat such a stage's numbers with care.

## Known limitations and biases

**Sample size and chance.** Models are nondeterministic, and one run is one sample. A difference of
a few tests or points in a single run may be chance. Repeat runs before you believe a small gap, and
don't treat a report as a ranking. `run --repeat` and `compare` show the mean and spread across runs,
and keep runs with different tasks, prompts, scoring or agents apart. They don't test for significance.

**Stopping a model by hand is a human decision.** `skip` ends a stage early, and a person chooses which
stage and when. A stopped build is scored on what it had at that moment, the same as a build that reached the
stage limit, and the report says it was stopped by hand. Its score is a snapshot, not its best work, so the
report and `compare` mark every stopped build. Judges aren't told, so they score the code as it stands.

**The task set is small and Python-centred.** Three tasks can't describe a model. All are small,
self-contained Python packages with a clear spec, which favours models good at exactly that. Long
refactors, large unfamiliar codebases and vague requirements are not tested. `toy-bepay` also has
country-specific rules that a model may or may not have seen in training.

**Task authoring bias.** The hidden tests were written alongside reference solutions by the same
author (AI agents directed by a person). They may embed that author's reading of the task. A test
that is too strict on an ambiguous point penalises valid solutions.

**Test contamination.** These tasks are public. A model trained on data that includes this repo, or
an agent with web access, could have seen them. Write private tasks if that matters.

**Hidden tests aren't sealed.** Agents have a shell, so a model can reach anything your user can,
including `tasks/*/hidden_tests`, `blind-key.json`, sibling builds and `tests/fixtures`. The prompts
tell it to stay in its folder, but nothing enforces that. As a best-effort check, the proxy searches
every request for strings like `hidden_tests`, `blind-key` and other builders' folders and names, and
flags hits in the report. It can miss things (a model that reads a file and never repeats it in a
form the agent sends back) and can false-alarm (a task or a project that uses those words). For
stronger isolation, set `runs_dir` outside this repo and run agents in a container or VM.

**Blinding is imperfect.**

- Models can sign their work in comments, or have a recognisable style. The label search only finds
  exact labels.
- The operator isn't blind in the strict sense. You watched which model built what, and you can open
  `tests/` or the key. A `human` judge should ideally be someone other than the operator.
- Reviewers and judges can infer things from quality. A judge might also assume the best build came
  from the biggest model.
- Numbered build folders follow config order. This matters only to someone who can also see the config.

**Judges have biases and drift.**

- Cloud judges favour certain styles, and may reward length despite instructions. Using several
  judges helps but doesn't remove this. The report keeps each judge's scores visible next to the average.
- Model updates change a judge between runs, so scores from different months may not compare.
  Re-judge an old packet to check.
- Judges also see the hidden test results first, which likely anchors their other scores. That is
  deliberate for correctness, and a real bias for code quality.
- Reviews can colour a judge's view of a build. Builds and reviews are scored separately, reviews come
  after all the builds in the packet, and judges are told to score the builds first. That reduces the
  effect but can't remove it, because a judge reads the whole packet. Runs without reviewers avoid it.
- Judges see each build's own test results, run by the harness. Passing tests can still be trivial, so
  this is evidence about the tests, not a score.
- The scoring scales are subjective, and the average of a few judges isn't a precise instrument.
- Very large packets may not fit a judge's context, and it may not say so.

**Speed numbers depend on setup.**

- Numbers from different machines, servers or quantisations can't be compared directly.
- Agents send different amounts of context, so results are for *this model in this agent*.
- On servers that load models on demand, the first call's load time lands in that stage's TTFT. The
  median over several calls reduces this but does not remove it.
- Prompt caching makes later calls faster, in ways that vary by server and by how the agent uses it.
- `openai-compatible` mode doesn't control context length. A small server default can silently
  truncate long agent prompts and hurt a model unfairly. Set it on the server, to the same value for
  every model.
- Thermal throttling can penalise whichever stage runs later. The harness doesn't randomise stage order.

**Plans are shared, but agents aren't neutral.** A model's results depend on how well it fits the
agent's tool format and prompt style. A poor score may be a poor fit with that agent.

**Prompts favour certain models.** The prompts were written in English, in one style, and tuned on a
limited set of models and tasks.

**What isn't verified.** The test suite runs the whole pipeline against a mock server with a scripted
agent, which shows the harness works, not that any particular model or agent does. See
[platform status](README.md#requirements-and-platform-status).

## Security

relaybench is a measurement tool, not a sandbox.

- **Model-written code runs on your machine with your privileges**, through the agent's commands, when
  hidden tests import a build, and when the harness runs a build's own tests. The temporary copy protects the results, not your system.
- Prefer an agent that asks before running commands, and consider a container or a VM, especially for
  models from sources you don't fully trust.
- **`run` removes the person from the loop.** The agent has to auto-approve, so anything a model decides to
  run, runs, for hours, with nobody watching. A container or VM is the real protection here. It is also
  the only thing that keeps hidden tests out of reach: mount just the stage folder, and keep the repo and
  the runs folder outside.
- The proxy listens on `127.0.0.1` only, and has no authentication.
- **What leaves your machine.** With a local server, stage traffic stays local. Judge packets
  (the task, plan, all code and reviews) go wherever you send them: `paste` by your own hand, `api`
  when you run `judge`. A remote `backend.url` sends all stage traffic there too, and your agent may
  have telemetry of its own.
- API keys are only read from environment variables. The proxy log doesn't record keys or message
  content, only metadata.
