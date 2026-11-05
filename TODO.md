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

In that run, a builder created `/tmp/cronnext-venv`, even though `build.md` says to stay in its folder. The
integrity check doesn't look at `/tmp`, so the report didn't flag it. If that model's `pip install` worked, a
later model could find a copy of its build there.

- Compare `/tmp` before and after each stage, and flag new entries in the report.
- Think hard before deleting anything automatically, because `/tmp` also holds other programs' files.
- Setting `TMPDIR` for each stage isn't enough, because this model typed `/tmp` itself.

Only a container really prevents this. See [Using a container](GUIDE-UNATTENDED.md#using-a-container).

## Use a neutral run path

Agents can see where they work: `.../relaybench/runs/20261105-0001-cron-next/2-build-8`. That shows the project's
name, the task and that other builds exist. In that run, a model reasoned "if a grader tries `pip install .`", and
that decided what it added to its build.

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

## Fix two `cron-next` test problems

- **A weak test:** `test_star_step_day_of_month_is_unrestricted_so_and` passes by luck with the wrong rule, because
  from 2 September the next Monday is the 9th, which is odd anyway. Start from 9 September, where the right answer
  is the 23rd and the wrong rule gives the 16th. Add a failing variant to `tests/fixtures/cron-next/variants.json`.
  This changes the task hash, so wait until no `cron-next` run is going.
- **Grouping:** one slow search shows as three *Failing areas*. In `tasks/cron-next/test_groups.json`, move
  `test_next_n_never_fires` and `test_cli_no_match` to *schedules that end*. This doesn't change the task hash.

## Keep a changelog

Add a `CHANGELOG.md`, starting with `v0.1.0` and what has changed since. For each version, say which changes affect
results, meaning anything that changes the task, prompts or scoring hash, so readers can tell which results still
compare. Update it with each change from now on.

## Smaller ideas and open offers

- Write release notes to publish on GitHub.
- Check on real hardware whether LM Studio stops generating when `skip` or a limit kills the agent mid-call.
  That decides how much electricity stopping a model really saves.
- Decide whether to force-push to remove the hardware line that commit `b009b6f` left in the history. The
  recommendation so far is to leave it.
- Add cached tokens to the report, once real server data shows what servers send.
- Tag a new version after a real run has exercised the latest changes.
