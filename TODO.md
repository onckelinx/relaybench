# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

Most of these came from a big unattended `cron-next` run. "That run" means this one.

## Rerun a failed build in a finished run

`python pipeline.py retry <stage>`

In that run, a model wrote 12 tokens on its first call, and nothing more came through. Cline gave up after about
6 minutes of silence, so the build failed before any work was done. `run` can't redo it, because it skips failed
stages, and it starts a new run once a report exists.

Today this takes a hand-written script, which worked on a mock run. The command should do the same:

- Refuse if a `run` is going, or if the stage isn't marked as failed.
- Set aside `tests`, `blind-key.json`, `4-judging`, `REPORT.md` and any `3-review-*` folders.
- Remove the stage's failed entry from `stage-results.json`, so that `run` resumes this run and builds it.
- Keep what's under *My notes* in the report.
- Warn that build letters get reshuffled, so any judges' answers need redoing.

**Tests:** with and without reviewers, a second failure, and both refusals.

## Count only the last attempt in a stage's speed

`cmd_report` uses all of a stage's calls for generation speed, model time and wall time, including those from a
failed attempt. A rerun stage would then look slower than it was: a stalled call adds minutes of generating time
with almost no tokens, and wall time includes the gap until the rerun. `compare` has the same problem with speed.

`stage-history.jsonl` records when each attempt started, so calls can be limited to the last one. Keep showing
the number of attempts.

## Spot leftovers outside the build folder

In that run, one builder created `/tmp/cronnext-venv` and another wrote `/tmp/patch.py`, even though `build.md` says
to stay in the build's folder. The integrity check doesn't look at `/tmp`, so the report flagged neither. The first
builder removed its folder before it ended, but a leftover like this could show a later model part of a build.

- Compare `/tmp` before and after each stage, and flag new entries in the report.
- Think hard before deleting anything automatically, because `/tmp` also holds other programs' files.
- Setting `TMPDIR` for each stage isn't enough, because this model typed `/tmp` itself.

Only a container really prevents this. See [Using a container](GUIDE-UNATTENDED.md#using-a-container).

## Use a neutral run path

Agents can see where they work: `.../relaybench/runs/20261105-0001-cron-next/2-build-8`. That shows the project's
name, the task and that other builds exist. In that run, a model reasoned "if a grader tries `pip install .`", and
that decided what it added to its build. Nothing it was given mentions a grader or hidden tests.

- Setting `runs_dir` outside the repo already removes `relaybench` from the path.
- Hiding the rest needs more thought, because the proxy's integrity check and resuming a run both use these
  folder names.
- This can't be shown to change results without trying it, so compare runs with and without it.

## Measure planning, building and reviewing separately

A plan shapes every build made from it. It's fair between the builders in one run, but a poor plan can hide
differences between them. Across runs it adds noise: `compare` averages runs with different plans in one table,
so plan luck and model luck can't be told apart.

**First, measure how big the effect is.** This needs no code. After `new-run`, put a plan in `1-plan/plan.md`, then
use `run`. On a mock run, the planner was skipped and the builders got that plan. Build with about 3 plans and 3
builders, twice each, and read the reports separately. The report still names the configured planner, and `compare`
would mix the plans. Include a plan with no detail as a baseline, because it shows how much plans matter at all.

**If the effect is real, add one mechanism instead of five modes:** any stage's input can be a saved result in place
of a fresh model's output.

- **Builder only:** start from a saved plan. Use 2 or 3 plans of different quality, not one "gold" plan, which would
  swap one bias for another. They shouldn't contain code, which would do the thinking for weak builders.
- **Planner only:** stop after the plan, with several plans per model.
- **Reviewer only:** start from saved builds. `tests/fixtures/*/variants.json` already has builds with known bugs,
  so score the bugs a reviewer finds, plus its false alarms on the reference solution. Those bugs are small, so mix
  in real builds. `wal-kv` has only one such variant.
- **`compare`:** group by a hash of the plan, or of the builds, and name the source in the report's header.

**Score plans in two ways, and check that they agree:**

- Judges audit them against the task, using a per-task list of traps that models never see.
- Build each plan with the same 2 or 3 builders, and use the hidden tests. If judges' scores don't predict these
  results, drop the audit.
- Put all of a task's plans in one packet, shuffled per judge. Otherwise 8 models, 3 plans and 9 judges means 216
  chats, instead of 9. Check too whether 3 judges give the same result as 9.

Keep the full relay as the final check, because the best planner and the best builder may not be the best pair.

## Have judges predict results before they exist, and score the predictions

During that run, nine cloud judges got the task, the prompt and the plan, and predicted how well each local model
would build, plus how good each judge would be. Nobody had seen results yet, so these can be checked afterwards.

A command could write the prediction packet, keep the answers from before the results, and score them from what the
run already measures, with no further chats. That shows which judges know these models, and whether anyone beats
the group's average or the published benchmarks they cite.

What that first round showed:

- **Ratings need a meaning set in advance.** ✅, ⚠️ and ❌ weren't defined, so they can't be scored fairly. Fix them
  before results exist, for example ✅ = not stopped and at least 85% of tests.
- **Ask for numbers too.** Most models got mostly ✅ or mostly ⚠️. Ask for a test count, a rank and the chance of
  finishing without being stopped.
- **Use a fixed answer format.** One judge merged the two GLM quantisations into one row. A JSON block with one entry
  per model prevents that.
- **Split the question about judges.** Asked how well judges would "predict", some answered about predicting before
  the run and others about auditing code afterwards, so those scores don't compare.
- **Track conflicts of interest.** Models are named here, and judges rated their own makers' models. For example,
  the only ✅ for Gemma came from Gemini. Report those rows separately.
- **Keep it apart from judging.** Use new chats with memory off. Judges with web search could also find this public
  repo's earlier reports.

## Handle plans that skip or contradict the task

The plan in that run left out two of the six sections `prompts/plan.md` requires, and contradicted `TASK.md` in one
rule. It also suggested a minute-by-minute search that can't meet the task's time limit. A reference solution
changed to use that search fails 5 of the 100 hidden tests, and using the wrong rule instead fails 1.

- Flag missing plan sections in the report. `_settle_stage` only checks that `plan.md` exists and isn't empty.
- Tell builders that `TASK.md` wins when the plan disagrees. `prompts/build.md` says "Implement exactly what they
  describe" and has no rule for this. This changes the prompts hash, so later runs get their own table in `compare`.

## Fix `cron-next` test problems

- **A weak test:** `test_star_step_day_of_month_is_unrestricted_so_and` passes by luck with the wrong rule, because
  from 2 September the next Monday is the 9th, which is odd anyway. Start from 9 September, where the right answer
  is the 23rd and the wrong rule gives the 16th. Add a failing variant to `tests/fixtures/cron-next/variants.json`.
  This changes the task hash, so wait until no `cron-next` run is going.
- **Grouping:** one slow search shows as three *Failing areas*. In `tasks/cron-next/test_groups.json`, move
  `test_next_n_never_fires` and `test_cli_no_match` to *schedules that end*. This doesn't change the task hash.
- **Missing tests:** judges reported five bugs that the hidden tests can't see. With each one added to a copy of the
  reference solution, it still passed 100 of 100. Add a test and a failing variant for each:
  - `5/1 * * * *` must give the minutes 5 to 59.
  - `50-70 * * * *` must raise `CronError`.
  - `0 0 31 2 FRI` from 2024-01-01 must give 2024-02-02.
  - `parse()` must raise `CronError` when given a schedule object.
  - `--from 2024-01-15` and `--from "2024-01-15 07:00:00"` must exit with code 2.

  Checks on that run's builds found all five, and only the top build has none of them. This changes the task hash and
  the test count, so do it together with the weak test. Later runs then get their own table in `compare`.

## Flag judges who read only part of the packet

The packet in that run was about 292,000 characters, and some chats cut it. Grok scored only the first three and the
last two of nine builds, and its answer says about 118,000 characters were cut, which fits the gap. Perplexity scored
only the first three. The harness doesn't shorten anything, so this happened in the chats. Leaving them out of those averages didn't change the order, but the report still lists Perplexity's
"prefers Build B" with no sign that it saw 3 of 9.

- In *Verdicts*, show how many builds each judge scored, for example `(scored 3 of 9)`.
- Say in the report when the builds' averages use different judges.
- Print the packet's size when it's made, and tell people in the guides to check that an answer covers every build.
- Ask judges to name any build they couldn't read, and try smaller packets. Put new instructions in `cmd_packet`,
  because changing `scoring/` changes the scoring hash.

## Tell judges when a build was stopped or didn't commit

The packet doesn't say that a build was stopped, or that the harness committed its work. In that run, the top build
hit the stage limit one command short of committing: its final tests had passed and `git add -A` was done. The context
limit had added 17.6 minutes to that build, and its last call was 9.4 s into a 35 s wait when the stage limit ended it.
All eight judges who scored it gave it 25 of 25, and one wrote that it "meets every requirement". Two other builds
didn't commit either.

- Add one factual line to the packet for each such build: stopped at the limit, stopped by hand, or committed by the
  harness.
- Keep `skip` notes out, because a note could identify the model.
- Decide first how judges should weigh this, because it could make them harsher on code that works. A scoring rule
  for it would change the scoring hash.

## Flag builds that hit the context limit

The proxy log shows this, but the report doesn't. Three builds reached about 65,000 tokens of input, against a limit
of 65,536. Input then dropped to about 31,300 and stayed at one exact count for every later call. Each of those calls
took about 35 s to its first token, instead of a few seconds. That cost `qwen3.8-27b-uncensored-mlx` 17.6 minutes and
`glm-4.7-flash` about 12 in that run, and `devstral-small-2507-mlx` 17.3 in the toy run. One long answer can use much of
the limit: a 22,183-token planning answer stayed in the history and took about a third of it.

- **Report:** add a note to the stage, with the call number and the minutes lost.
- **`compare`:** add context length to the grouping in `cmd_compare`. It currently mixes runs with different context
  lengths in one table.
- **Proxy:** log each request's size, so it can be compared with the server's count. Use that to find out whether
  LM Studio or the agent cuts the prompt, and what gets cut.

## Flag or stop a model that repeats itself

In that run, `glm-4.7-flash` alternated between answers of 147 and 561 tokens from call 46, at minute 10.7 of 31.6,
until it was stopped by hand. In the toy run, `devstral-small-2507-mlx` repeated a five-call cycle from call 38, at
minute 12.7 of 60. Both loops started before those builds hit the context limit. No other stage in the three runs in
the log matched, so exact repeats of answer sizes look like a usable signal.

- Flag it in the report first, with the call and minute where it started.
- Decide later whether it should also stop the model automatically. Here that could have saved about 21 and 47 minutes.

## Keep a changelog

Add a `CHANGELOG.md`, starting with `v0.1.0` and what has changed since. For each version, say which changes affect
results, meaning anything that changes the task, prompts or scoring hash, so readers can tell which results still
compare. Update it with each change from now on.

## Smaller ideas and open offers

- Write release notes to publish on GitHub.
- Check whether LM Studio stops generating when `skip` or a limit kills the agent mid-call. `skip` has now stopped a
  real model, but that doesn't show whether generation stopped too. This decides how much electricity stopping a model
  really saves.
- Decide whether to force-push to remove the hardware line that commit `b009b6f` left in the history. The
  recommendation so far is to leave it.
- Add cached tokens to the report, once real server data shows what servers send.
- Tag a new version after a real run has exercised the latest changes.
