# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

Most of these came from a big unattended `cron-next` run. "That run" means this one. Entries that change a task,
prompts or scoring hash wait until the planned mlx-serve runs are done, so those runs compare with that run.

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
  before results exist. A rule like "✅ = not stopped and at least 85% of tests" would have rated that run's winner
  ⚠️, because it was stopped one command short of committing, so stopped builds need thought.
- **Ask for numbers too.** Most models got mostly ✅ or mostly ⚠️. Ask for a test count, a rank and the chance of
  finishing without being stopped.
- **Use a fixed answer format.** One judge merged the two GLM quantisations into one row. A JSON block with one entry
  per model prevents that.
- **Split the question about judges.** Asked how well judges would "predict", some answered about predicting before
  the run and others about auditing code afterwards, so those scores don't compare.
- **Track conflicts of interest.** Models are named here, and judges rated their own makers' models. For example,
  the only ✅ for Gemma came from Gemini, and was right. Report those rows separately.
- **Keep it apart from judging.** Use new chats with memory off. Judges with web search could also find this public
  repo's earlier reports.

## Tell builders that `TASK.md` wins over the plan

The plan in that run contradicted `TASK.md` in one rule, and suggested a minute-by-minute search that can't meet the
task's time limit. A reference solution changed to use that search fails 5 of the 100 hidden tests, and using the
wrong rule instead fails 1. All five builds that kept the minute search scored 94 or less, and the four that
replaced it scored 96 or more.

`prompts/build.md` says "Implement exactly what they describe" and has no rule for a disagreement. Adding one changes
the prompts hash, so later runs get their own table in `compare`. The report already names missing plan sections.

## Fix `cron-next` test problems

- **A weak test:** `test_star_step_day_of_month_is_unrestricted_so_and` passes by luck with the wrong rule, because
  from 2 September the next Monday is the 9th, which is odd anyway. Start from 9 September, where the right answer
  is the 23rd and the wrong rule gives the 16th. Add a failing variant to `tests/fixtures/cron-next/variants.json`.
- **Missing tests:** judges reported five bugs that the hidden tests can't see. With each one added to a copy of the
  reference solution, it still passed 100 of 100. Add a test and a failing variant for each:
  - `5/1 * * * *` must give the minutes 5 to 59.
  - `50-70 * * * *` must raise `CronError`.
  - `0 0 31 2 FRI` from 2024-01-01 must give 2024-02-02.
  - `parse()` must raise `CronError` when given a schedule object.
  - `--from 2024-01-15` and `--from "2024-01-15 07:00:00"` must exit with code 2.

  Checks on that run's builds found all five, and only the top build has none of them.

Both change the task hash and the test count, so do them together, and after the mlx-serve runs.

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

## Find out who cuts long prompts, and what's lost

The report now warns when a prompt is cut to a fixed size. In that run, three builds reached about 65,000 tokens of
input against a limit of 65,536, then stayed at about 31,300 for every later call, each waiting about 35 s for a first
token. It isn't known yet whether LM Studio or the agent does the cutting, or what the model loses.

- The proxy now logs each request's size. On the next run that reaches the limit, set it against the server's input
  tokens: a request that keeps growing while the input stays fixed means the server cuts.
- If the cut loses the task or the plan, a model may redo work it already did. Look for that in the agent logs.
- Decide on a context length for later runs. A larger one avoids the cut, but changes what's measured, and
  `compare` keeps runs with a different context length apart.

## Decide whether to stop a looping model automatically

The report now flags a model whose answer sizes repeat in a cycle. In that run, `glm-4.7-flash` looped from minute
10.7 of 31.6 until it was stopped by hand, and in the toy run `devstral-small-2507-mlx` looped from minute 12.7 of 60.
Stopping automatically could have saved about 21 and 47 minutes. But it's a decision for the harness to make instead
of a person, so it needs a threshold that can't stop a model doing real work. Two real loops are not much to set one.

## Smaller ideas and open offers

- Write release notes to publish on GitHub.
- Check whether LM Studio stops generating when `skip` or a limit kills the agent mid-call. `skip` has now stopped a
  real model, but that doesn't show whether generation stopped too. This decides how much electricity stopping a model
  really saves.
- Decide whether to force-push to remove the hardware line that commit `b009b6f` left in the history. The
  recommendation so far is to leave it.
- Add cached tokens to the report. The proxy now logs them where the server reports them, so first see what real
  servers send.
- Tag a new version after a real run has exercised the latest changes.
