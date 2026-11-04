# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

Most of these came from a nine-model unattended run, where a model got stuck in a loop on the toy task and
used up its full hour. "That run" further down always means this one.

## Stop a stuck model, with a note

`python pipeline.py skip "stuck in a loop"`

Today, `kill -TERM -$(cat runs/agent.pid)` already stops the agent and moves the run on, but the report
only says `agent exited with code -15`. Don't use `Ctrl+C` for this, because it stops the whole run.

- `skip` writes a request naming the active stage and the note. `_run_agent` checks for it on each poll,
  once a second, and stops the agent the way it already does with `_kill_tree`.
- Naming the stage means a late request can't stop the next model.
- It gets the same treatment as hitting `stage_timeout_s`: whatever the model built is committed, tested,
  reviewed and judged, so a stop by hand can't favour a model.
- The report's *Notes* column shows `stopped by hand: <note>`. The note can't reach judges, because
  `cmd_packet` doesn't read stage results.
- It counts as a stopped build: see [Mark stopped builds](#mark-stopped-builds).
- `skip` warns before stopping a planner, because without a plan the run ends. It says so clearly if no
  agent is running.
- It needs someone awake. Overnight, `stage_timeout_s` remains the only limit. Automatic loop detection is
  left out on purpose: a wrong guess would stop a working model and change the results.

**Check on real hardware:** does LM Studio stop generating when the agent is killed mid-call? That decides
how much electricity this really saves.

**Tests:** use `--misbehave 2-build-1:hang` from `tests/scripted_agent.py` in `tests/test_e2e.py`. Check that
the next builder runs, the note reaches the report but not the packet, and a stale request stops nothing.
Cover the plan and review stages, and the case with no agent running.

## Add a judge after the run has started

`python pipeline.py add-judge`

Judges are copied into `run.json` when a run starts, and `report` only reads that list. A score file added
by hand for another judge is **silently ignored**.

Until it exists, use the steps under
[Explain how to add a forgotten judge](#explain-how-to-add-a-forgotten-judge).

The command:

- Asks the same questions as `init`, by moving one judge's questions out of `ask_judges` so both share them.
- Adds the judge to the run's `run.json`, written atomically. `_run_steps` re-reads that file each loop, so
  this also works during a `run`.
- Creates the score file if the packet already exists, reusing the code in `cmd_packet`.
- Asks whether to also add the judge to `pipeline.json` for future runs (default no).

Related changes:

- `report` warns about a `scores-*.md` file that belongs to no judge in the run.
- Add `--run <id>` in `current_run`, so an older run can be judged without touching `runs/CURRENT`. Commands
  only act on the current run today, so the unattended guide's "any you judge later show up in `compare`"
  only works by hand-editing that file.
- `compare` says when runs in a table had different judges, because an extra strict judge shifts that run's
  average.

**Tests:** cover before and after the packet, after `REPORT.md` exists, duplicate labels, and `--run`.

## Explain how to add a forgotten judge

Add a row to the troubleshooting tables in `GUIDE-UNATTENDED.md` and `GUIDE-MANUAL.md`, for when a judge was
left out at `init` and the run has already started. It links to a short section with the steps below, which
add a `paste` judge. Run them from the `relaybench` folder after `run` has ended, changing `claude` to the
judge's label:

```bash
cp "runs/$(cat runs/CURRENT)/run.json" "runs/$(cat runs/CURRENT)/run.json.bak"
python - <<'EOF'
import json
path = "runs/" + open("runs/CURRENT").read().strip() + "/run.json"
with open(path) as fh:
    run = json.load(fh)
if not any(j["label"] == "claude" for j in run["judges"]):
    run["judges"].append({"label": "claude", "type": "paste"})
    with open(path, "w") as fh:
        json.dump(run, fh, indent=2)
print([j["label"] for j in run["judges"]])
EOF
touch "runs/$(cat runs/CURRENT)/4-judging/scores-claude.md"
```

Then paste the judge's complete answer into that file, and run `python pipeline.py report`.

The section should also say:

- Don't use `run`, which starts a new run once this one has a report, or `init`, which doesn't change this
  run. `packet` isn't needed, because every judge gets the same packet.
- `pipeline.json` is unchanged, so add the judge there too, or run `init` again, for future runs.

These steps were rehearsed on a mock run, for a `paste` judge only. Check `api` and `human` judges before
documenting them, and add the rehearsal to `tests/test_e2e.py` so the guides stay true. Replace the section
with `add-judge` once that exists.

## Group failed tests by root cause

Non-ASCII digits are 4 of the 41 toy tests, so one mistake can cost a build four tests. In that run, the top five
builds each failed for a single reason, so the raw counts overstate the differences between them.

- Keep the raw count as the main result, and add the failing categories next to it in the report.
- One way is a small file in the task folder that maps tests to categories, which models never see.
- Don't let this change any score.

## Mark stopped builds

The report's *Unattended stages* table shows when a build was stopped, but the *Hidden tests* table doesn't,
and in `compare` a stopped build looks like a normal one.

- Mark stopped builds in the *Hidden tests* table. Their scores only show the code at the moment they were
  stopped. In that run, one build's score came from about 75 seconds of work, followed by one runaway answer.
- Add a *Stopped* column to `compare`, counting stops by hand and stops at a limit.
- Keep this out of the judge packet, so judges score the code as it stands.

## Let judges cross-check each other (optional)

Giving each judge the other's report found real errors in that run, including a mix-up between two builds and
bugs that one judge had missed. It also produced false claims about missing tests, because judges can't see
the hidden tests.

- A command could build a packet with all the judges' reports and the hidden test source. Scoring is over by
  then, so showing the tests does no harm.
- Keep the answers as notes, not scores, because second-round scores would be anchored on the other judge.

## Smaller ideas and open offers

- Write release notes to publish on GitHub.
- Decide whether to force-push to remove the hardware line that commit `b009b6f` left in the history. The
  recommendation so far is to leave it.
- Validate review fields and `preferred_build`.
- Tidy the packet and report for runs with no reviewers.
- Add a total-time row and cached tokens to the report.
- Add a packet hash.
- Tag a new version after a real run has exercised the latest changes.
