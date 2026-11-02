# Example reports

## A real run: [`REPORT.md`](REPORT.md)

A real run of the `toy-bepay` task on a MacBook Pro with an M5 Max and 128 GB of unified memory: one
planner, two builders and one reviewer, all running locally, with two cloud judges (GPT-6 and Claude
Opus 5.5, both on high settings). Both builds passed all 36 hidden tests of the time, and both judges
preferred Build B.

The report is left exactly as it came out of that run, including the **My notes** section: those are
the author's personal comments, written for their own use. It is a real-world example of how someone
might use relaybench, not a formal study.

It also shows why judges read the code. Both flagged a bug in Build A that the tests missed: its
`isdigit()`/`int()` check could raise on input like `²`. The task now has tests for this and says that
digits means ASCII `0`–`9`, so it has 41 tests, and new results aren't directly comparable with this run.

Read it as an example, not as a ranking of these models:

- **It is one run of one small task.** Models are nondeterministic, and a toy task says little about
  harder work. See [limitations](../METHODOLOGY.md#known-limitations-and-biases).
- **It predates the public release.** A report from the current version also has attempts per stage,
  the settings the agent sent, the platform, content hashes and integrity flags.
- **Conditions were not fully controlled.** As the author's notes in the report say, the agent's
  command approval changed between the two builds, so their wall times don't compare directly. Build
  A's wall time includes waiting for approvals; compare *model time* and speed instead.
- **Speeds depend on hardware** and quantisation, so compare them only within one machine. The
  quantisations used aren't recorded in this report.

## The current format: [`mock-run-report.md`](mock-run-report.md)

**This one is synthetic.** It was produced by the test suite: a mock model server, a scripted agent
and canned judges. The models are named after what the mock does (`mock-good`, `mock-buggy`,
`mock-syntax`), and every speed, token count and score is meaningless. It exists to show every
section of the current format, and says nothing about any real model.

Regenerate it with:

```bash
RELAYBENCH_WRITE_EXAMPLE=examples/mock-run-report.md python -m pytest tests -k "full_run and toy"
```

## Adding your own

Copy a run's `REPORT.md` here, and read it first: reports name your models, and the **My notes**
section holds whatever you wrote there.
