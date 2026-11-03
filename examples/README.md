# Example reports

## A real run: [`REPORT.md`](REPORT.md)

A real unattended run (`python pipeline.py run`) of the `toy-bepay` task on a MacBook Pro with an M5 Max and
128 GB of unified memory: one planner, two builders and one reviewer, all running locally through Cline's
command-line agent (version 3.0.74), with two cloud judges used through web chats. It took about 31 minutes
with no failed stages. Build B passed 38 of 41 hidden tests and Build A passed 33 of 41, and both judges
preferred Build B.

The report is left exactly as it came out of that run, and its **My notes** section was left blank. It is a
real-world example of how someone might use relaybench, not a formal study.

It shows how the tests and the judges work together. The tests count the failures, and the judges read the
code and explain them: one traces Build A's failures to its national IBAN check reading the wrong digits.
Both builds also fail tests for non-ASCII digits, which exist because judges in
[an earlier run](https://github.com/onckelinx/relaybench/blob/86a38bb/examples/REPORT.md) spotted a bug of that kind that the tests missed.

Read it as an example, not as a ranking of these models:

- **It is one run of one small task.** Models are nondeterministic, and a toy task says little about
  harder work. See [limitations](../METHODOLOGY.md#known-limitations-and-biases).
- **Compare it with care.** Cline's CLI sent no temperature or output limit, so each model used its defaults.
  Scores from another agent, or from a manual run, aren't directly comparable.
- **It comes from commit `86a38bb`, and the harness has changed since.** Tests for non-ASCII digits are now
  stricter, so `38 of 41` doesn't compare directly with newer results. Judges now get numbered code, each
  build's own test results and tighter counting rules, reviews must give evidence, and the report's header
  and tables have more in them.
- **Speeds depend on hardware** and quantisation, so compare them only within one machine. The
  quantisations used aren't recorded in this report.

## The current format: [`mock-run-report.md`](mock-run-report.md)

**This one is synthetic.** It was produced by the test suite: a mock model server, a scripted agent
and canned judges. The models are named after what the mock does (`mock-good`, `mock-buggy`,
`mock-syntax`), and every speed, token count and score is meaningless. It exists to show the current
format of a manual run, and says nothing about any real model. A report from `pipeline.py run` has one
more section, *Unattended stages*, listing each stage's outcome and anything that went wrong.

Regenerate it with:

```bash
RELAYBENCH_WRITE_EXAMPLE=examples/mock-run-report.md python -m pytest tests -k "full_run and toy"
```

## Adding your own

Copy a run's `REPORT.md` here, and read it first: reports name your models, and the **My notes**
section holds whatever you wrote there.
