# Changelog

What changed in relaybench, newest first.

**Affects results** means a change to a task, the prompts or the scoring. Each of those has a hash in every run's
report, and `compare` never puts runs with different hashes in one table. So only these changes stop new results
from comparing with older ones. Everything else changes how runs are measured, handled or reported.

## Unreleased

### Affects results

Nothing since the changes listed under 2026-11-04 and 2026-11-03.

### Measurement and reports

- **Cut prompts:** the report warns when a stage's input falls sharply and then stays at one exact size, as it did
  near LM Studio's context limit, and says how long those calls waited for a first token.
- **Loops:** the report warns when a model's answer sizes repeat in a cycle, and says from which call and minute.
- **Last attempt only:** a stage that ran more than once shows speed and times from its last attempt only, in the
  report and in `compare`. *Attempts* still shows how many there were.
- **`compare`:** runs with a different server type or context length get their own table.
- **Proxy:**
  - It logs each request's size (bytes and number of messages), so it can be compared with the server's
    token count.
  - It logs cached prompt tokens when the server reports them.
- **`cron-next` failing areas:** two tests that fail with one slow search now count under *schedules that end*,
  rather than as two more areas. This file isn't part of the task hash.
- **Judges:**
  - *Verdicts* say `(scored k of n)` for a judge who scored only some builds.
  - A note says when builds' averages come from different judges.
- **Packet:**
  - It opens by saying how many builds it holds, and asks judges to name any they couldn't read.
  - Creating it prints its size, with a warning for a long one.
- **Plans:** the report names any section the planner prompt asks for that the plan leaves out.
- **`retry <stage>`:** runs a failed stage again, even after the report. It keeps *My notes*.
- **`/tmp`:** during each unattended stage, new entries at the top level of `/tmp` are listed in the report,
  including ones removed again. Nothing is deleted.

## 2026-11-04

### Affects results

- **`toy-bepay`:** two hidden tests, for formats `TASK.md` forbids and for a newline inside delimiters, taking
  it from 41 to 43 tests. Task hash `13c7271457b98e20` to `4084af52ab60cafa`.
- **Scoring:** a build that crashes where `TASK.md` forbids it scores at most 3 on edge cases, and a note says
  that own tests run under pytest. Scoring hash `bc02bec3ab460ebc` to `662279e4dc5e2f93`.

### Measurement and reports

- **`skip`:** stops the active model during `run` and moves on.
- **`add-judge`:** adds a judge to a run that has already started.
- **`--run <id>`:** lets `status`, `report`, `judge`, `packet`, `add-judge` and `cross-check` work on an
  earlier run.
- **`cross-check`:** an optional packet for judges to check each other's reports, as notes only.
- **Hidden tests table:**
  - It marks builds that were stopped.
  - It groups failing tests into areas, using an optional `test_groups.json` per task.
- **Report:**
  - The speed table has a total row.
  - The header shows the packet's hash.
  - More of the judges' answers are checked.
- **`compare`:**
  - It shows stopped builds.
  - It keeps runs with different pinned settings apart.
  - It notes when runs had different judges.
- **Run safety:**
  - `check` and `run` catch a proxy started before an update.
  - `init` keeps `request_overrides`.
  - The harness commit says when there were uncommitted changes.

## 2026-11-03

### Affects results

- **Prompts:**
  - Judges see numbered code lines, and score the builds before the reviews.
  - Reviewers must give evidence for each issue: an input, what should happen and what does.

  Prompts hash `c4090ea2685263cd` to `3bcf1bd23cdcfbc5`.
- **Scoring:**
  - How real issues, false alarms and missed issues are counted is now defined.
  - A build's own test results count as evidence.

  Scoring hash `860d3f5d1031ad83` to `bc02bec3ab460ebc`.
- **`toy-bepay`:** tighter tests for non-ASCII digits in the delimited format, and for a second IBAN that passes
  mod-97 but fails the national check. Still 41 tests. Task hash `80d915bb52492e23` to `13c7271457b98e20`.

### Measurement and reports

- **Pinned settings:** optional `request_overrides` pin `temperature` and `max_tokens`.
- **Report:** it records hardware, agent version, quantisation and limits.
- **Repeat runs:** `run --repeat n` does several runs in a row, and `compare` sets them side by side.

## v0.1.0 (2026-11-03)

The first tagged version.

| Hash | Value |
|---|---|
| `toy-bepay` task | `80d915bb52492e23` |
| `cron-next` task | `1cdee2ca7823a7f9` |
| `wal-kv` task | `ad181c10fc2f5ea1` |
| prompts | `c4090ea2685263cd` |
| scoring | `860d3f5d1031ad83` |

`cron-next` and `wal-kv` have not changed since.
