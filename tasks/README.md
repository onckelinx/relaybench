# Tasks

A task is what the models are asked to build. Each one is a folder here:

```
tasks/my-task/
  TASK.md             what to build (required)
  starter/            files every build starts with (optional)
  hidden_tests/       acceptance tests the models never see (required)
    test_*.py
```

| Task | Difficulty | What it asks for | Hidden tests |
|---|---|---|---|
| [`toy-bepay`](toy-bepay/TASK.md) | Toy | Validators for Belgian payment references, and a small CLI. Pure functions, country-specific rules. | 41 |
| [`cron-next`](cron-next/TASK.md) | Medium | A cron parser and next-run calculator. Easy to start, with famous traps: the day-of-month/day-of-week OR rule, leap years, schedules that never fire. | 100 |
| [`wal-kv`](wal-kv/TASK.md) | Hard | Extending an existing in-memory store with a write-ahead log, nested transactions, expiry, crash recovery and compaction, and fixing a reported bug. | 57 |

Choose one with `python pipeline.py new-run --task <name>`, or set `"task"` in `pipeline.json`.

Each test count is the number of tests pytest collects, after parametrisation. `tests/test_e2e.py`
checks the counts against the reference solutions.

## `TASK.md`

Every builder, the planner, the reviewers and the judges read this file.

- **Be exact about behaviour that is tested.** Name the functions, signatures, return values, error
  types, exit codes and output formats. A hidden test should never fail a reasonable reading of the
  task. If a test needs a file format, specify the format.
- **Number the requirements.** Reviewers fill in a checklist against them.
- **State the allowed language and dependencies.** The prompts defer to `TASK.md` for this.
- **Don't mention the tests' location**, or use the words `hidden_tests` or `blind-key`. The proxy
  flags those strings in requests, as a sign that a model read something it should not have.
- **End with a "Done when" section** that includes committing the work.

## `starter/`

Optional. It is copied into every build folder before the builder starts, so it can be a real
project to extend. The planner gets a read-only copy in `1-plan/starter/`. Judges see the starter
code once in the packet, and each build then lists which starter files it left unchanged.

## `hidden_tests/`

pytest test files, run once per build:

```
python -m pytest . -p no:cacheprovider --rootdir . --junitxml ...
```

The contract:

- **The build's path is in the `BUILD_DIR` environment variable.** It is a temporary copy of the
  build, without its `.git`. The tests themselves also run from a temporary copy, so they can write
  files without affecting the task or the next build.
- **Guard your imports.** `import thepackage` at the top of a test file would turn a broken build
  into one collection error, which loses the test count. The harness also collects the tests with an
  empty build to learn how many there are, and that only works with guarded imports. Import inside
  a helper and fail from there. See `load_build_module` in
  [`cron-next/hidden_tests/_support.py`](cron-next/hidden_tests/_support.py).
- **Guard calls that can loop.** A build that hangs in one call should fail one test, not the whole
  suite. `limited(...)` in `_support.py` does this without third-party plugins, on every OS.
  `test_timeout_s` in `pipeline.json` (default 600) is the backstop for the whole suite. If it fires,
  that build scores 0 and is marked `timeout`.
- **Don't let a crash pass.** A test that only checks "exit code 1 and empty output" also passes for
  a build that doesn't start. Make the test prove the program works first.
- **Try awkward input.** In a real run, a build passed every test while `"\u00b2".isdigit()` followed by
  `int()` could crash it; only the judges noticed. Non-ASCII digits, trailing newlines and empty
  strings are cheap to test. Say in `TASK.md` what is valid, so that the test is fair.
- **Use only pytest and the standard library**, so that `requirements.txt` stays the only install.
- **Plain `unittest.TestCase` classes also work.** pytest runs them, and `toy-bepay` is written that way.

## Checking a new task

Tests nobody has run against a wrong solution prove little. Before trusting a task:

1. Write a reference solution in `tests/fixtures/<task>/good/`, and add its folder to `TASK_FOLDERS`
   in `tests/mock_server.py`. The mock finds a task by the name in its `# Task: ...` title.
2. Add wrong variants in `tests/fixtures/<task>/variants.json`. Each is a set of find-and-replace
   edits to the good solution, plus the tests it must fail.
3. Add the task to `HIDDEN_COUNTS` and to the `test_full_run` parameters in `tests/test_e2e.py`, and run:

   ```
   python -m pytest tests -k full_run
   ```

   This asserts that the good build passes everything, each wrong one fails its named tests, and a
   build that doesn't even import scores exactly zero.

Reference solutions live outside `tasks/` so they are not copied into any stage folder.
