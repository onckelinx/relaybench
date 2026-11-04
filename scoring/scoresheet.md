# Scoring sheet

Score every build and every review with the scales below. Use whole numbers.
Judge only what is in this packet. Do not guess which model wrote what.

## Builds (each criterion 1–5)

| Criterion | 1 | 3 | 5 |
|---|---|---|---|
| **Correctness** | Broken or most requirements fail | Works for normal input, some edge cases wrong | Every requirement met, hidden tests pass |
| **Plan adherence** | Ignores the plan | Follows it loosely, unexplained deviations | Follows it, deviations explained and sensible |
| **Code quality** | Hard to read, duplicated, fragile | Readable, some clumsy parts | Clean, idiomatic, easy to maintain |
| **Edge cases & errors** | Crashes or wrong on edge input | Handles some edge cases | Handles all edge cases and errors as specified |
| **Own tests** | None or meaningless | Some useful tests, gaps | Thorough tests that match the test plan |

Use the hidden test results as strong evidence for **Correctness**, but read the
code too: a test can pass by accident.

For **Edge cases & errors**, a build that crashes where `TASK.md` forbids it scores at most 3,
however rare the input.

For **Own tests**, each build shows what happened when the harness ran its tests with pytest, which
also collects plain `test_` functions. That is evidence, not proof of quality: passing tests can
still be trivial, so read what they cover.

Score each build on its own code and tests. Reviews are scored separately below.

## Reviews (each criterion 1–5, plus counts)

For each review, compare its findings with your own reading of the code:

- `real_issues` – how many of its reported issues are genuine
- `false_alarms` – how many reported issues are wrong, or ask for something `TASK.md` doesn't require
- `missed_important` – problems it did not mention that break a requirement in `TASK.md` or a hidden test

Count them the same way every time:

- Several reports of one root cause count as one issue.
- Every issue counts as exactly one of `real_issues` or `false_alarms`.
- A sensible minor suggestion is real. Documentation counts only if `TASK.md` asks for it.
- A missed style or minor problem does not count towards `missed_important`.
- `accuracy` (1–5) – are its claims correct?
- `usefulness` (1–5) – would a developer fix the right things after reading it?

## Required output

Write your whole answer as **one Markdown document**, directly as your reply (not as a
canvas, file, download or attachment), with exactly these headings:

```
# Judge scores
## Builds
### Build A
### Build B
## Reviews
### A/reviewer-1
### B/reviewer-1
## Verdict
## Scores (JSON)
```

(Add a `### Build …` / `### …/reviewer-…` heading for every build and review in the packet.)
Under each build and review heading, write your reasoning in short bullet points.
Under **Verdict**, write which build you prefer and why, in two or three sentences.
Under **Scores (JSON)**, put **one** fenced code block that starts with ` ```json ` and
ends with ` ``` `, containing exactly this shape. Write nothing after it.
(fill in every number; keep the keys exactly as shown; one entry per build
letter and per review key that appears in the packet):

```json
{
  "builds": {
    "A": {"correctness": 0, "plan_adherence": 0, "code_quality": 0, "edge_cases": 0, "own_tests": 0},
    "B": {"correctness": 0, "plan_adherence": 0, "code_quality": 0, "edge_cases": 0, "own_tests": 0}
  },
  "reviews": {
    "A/reviewer-1": {"real_issues": 0, "false_alarms": 0, "missed_important": 0, "accuracy": 0, "usefulness": 0},
    "B/reviewer-1": {"real_issues": 0, "false_alarms": 0, "missed_important": 0, "accuracy": 0, "usefulness": 0}
  },
  "preferred_build": "A",
  "one_line_verdict": "..."
}
```
