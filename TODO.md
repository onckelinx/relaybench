# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

The first five came from a nine-model unattended run, where a model got stuck in a loop on the toy task
and used up its full hour. None of them changes the task, prompts or scoring hashes, so results from
before and after them still compare.

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
- `compare` counts failed builds, but a stopped build looks like a normal one. Add a *Stopped* column that
  counts stops by hand and stops at a limit.
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

Today's way, for a finished run:

1. Add `{"label": "gemini", "type": "paste"}` to `judges` in `runs/<id>/run.json`.
2. Paste the answer into `4-judging/scores-gemini.md`.
3. Run `python pipeline.py report`.

Wait until `run` has ended, because a JSON typo would crash a live run.

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

## Show how to watch a run

In step 9 of `GUIDE-UNATTENDED.md`, add:

```bash
python pipeline.py status
tail -n 30 "$(ls -t runs/$(cat runs/CURRENT)/agent-logs/*.log | head -1)"
```

Add a caveat: agent logs show which model wrote what, so a `human` judge shouldn't read them before scoring.

## More tmux help

Extend the table under "Connected over SSH?" in `GUIDE-UNATTENDED.md`:

- `tmux ls` lists sessions.
- `tmux switch-client -t <name>`, or `Ctrl+B` then `S`, changes session from inside tmux.
- Explain that the "sessions should be nested with care" warning means you're already inside tmux.
- Add the `Address already in use` row to the troubleshooting table. Today only the manual guide has it.

## Say when judge files appear

`cmd_packet` already creates `scores-<label>.md` for `paste` and `human` judges, and `judge` writes the `api`
ones. They only appear at the packet step, after all reviews, so `4-judging/` doesn't exist during a long
run. Say so in step 10 of `GUIDE-UNATTENDED.md`. `add-judge` above covers judges added later.

## Smaller ideas and open offers

- Write release notes to publish on GitHub.
- Decide whether to force-push to remove the hardware line that commit `b009b6f` left in the history. The
  recommendation so far is to leave it.
- Validate review fields and `preferred_build`.
- Tidy the packet and report for runs with no reviewers.
- Add a total-time row and cached tokens to the report.
- Add a packet hash.
- Flag uncommitted harness changes.
- Have `check` and `run` warn about a proxy started before the latest pull.
- Tag a new version after a real run has exercised the latest changes.
