# Todo

Ideas for later, in no particular order. What to work on first gets decided when we get to it.

The first five came from a nine-model unattended run, where a model got stuck in a loop on the toy task
and used up its full hour. None of them changes the task, prompts or scoring hashes, so results from
before and after them still compare. "That run" further down always means this one.

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

## Copy score files to and from the remote machine

Step 10 of `GUIDE-UNATTENDED.md` shows how to fetch the packet over SSH, but not the score files. You may
want to edit those on the computer with the browser, so add both directions, run from that computer:

```bash
# Fetch a score file to edit here
scp <you>@<remote-machine>:<path-to-relaybench>/runs/<run-id>/4-judging/scores-<label>.md .

# Put it back when it's done
scp scores-<label>.md <you>@<remote-machine>:<path-to-relaybench>/runs/<run-id>/4-judging/
```

Then run `python pipeline.py report` on the remote machine.

Add a caveat: use the same `<run-id>` both ways, which `cat runs/CURRENT` shows on the remote machine. Build
letters are shuffled for each run, so a score file put in the wrong run replaces that run's file and its
scores count for the wrong models.

## Add the missing OGM format tests

`TASK.md` forbids these inputs, but `tasks/toy-bepay/hidden_tests/test_hidden.py` doesn't test them:

- `1234 5678 9002`
- `+123/4567/89002+`
- `+++123456789002+++`
- `123/4567/89002`
- `+++123/4567/89002\n+++`

The judges found builds that accept them. One build with a very permissive parser still passed 36 of 41, next
to builds on 37, while the judges gave it 11 of 25 against their 17. The last input should also catch a build
that passed 40 of 41.

The reference solution in `tests/fixtures/toy-bepay/good` rejects all five. Superscript digits and
`BE58 8288 3607 5983` are already tested, so they don't need adding.

- Decide between adding them to an existing test, which keeps the count at 41, and a new test, which changes
  the count in `tasks/README.md` and the check in `tests/test_e2e.py`.
- Add failing variants to `tests/fixtures/toy-bepay/variants.json`, as for earlier tests.
- This changes the task hash, so later runs get their own table in `compare`. Do it together with the
  edge-case cap below.

## Cap edge-case scores when a build crashes

In `scoring/scoresheet.md`: a build that crashes where `TASK.md` forbids it scores at most 3 on edge cases.

This was the judges' biggest disagreement in that run. Three builds got 4 from one judge and 2 from the other,
and the stricter judge still gave a 4 to a build that crashes on a trailing newline.

This changes the scoring hash, so do it together with the OGM tests above.

## Tell judges how own tests are run

The packet shows each build's own test results, but not that the harness runs them with pytest, which also
collects plain `test_` functions. In that run, both judges called a plain `test_cli()` a discovery problem,
though the harness had counted it. Say so next to the own test results, or in the scoresheet.

## Keep pinned and unpinned runs apart in `compare`

`cmd_compare` groups runs by task, the three hashes and the agent command. `request_overrides` isn't part of
that, so runs with and without a `max_tokens` cap would be averaged together. Add it to the grouping, and treat
old runs as having none.

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
- Flag uncommitted harness changes.
- Have `check` and `run` warn about a proxy started before the latest pull.
- Tag a new version after a real run has exercised the latest changes.
