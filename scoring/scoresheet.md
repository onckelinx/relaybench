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

## Reviews (each criterion 1–5, plus counts)

For each review, compare its findings with your own reading of the code:

- `real_issues` – how many of its reported issues are genuine
- `false_alarms` – how many reported issues are wrong or irrelevant
- `missed_important` – important problems it did not mention
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
