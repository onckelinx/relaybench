# Your role: code reviewer

This folder contains:

- `TASK.md` – the requirements
- `plan.md` – the plan the developer was asked to follow
- the developer's implementation (and their own tests, if any)

Review the implementation. You may read every file in this folder and you may run
the code and the tests (for example `python -m unittest discover -s tests -v`, using
whichever of `python` or `python3` works on this machine).
**Do not change any existing file, and do not read anything outside this folder.**

Write your review to a new file called `REVIEW.md` with these sections:

1. **Summary** – 2–3 sentences on overall quality.
2. **Issues** – a table with columns:
   `#` | `Severity` (critical / major / minor) | `Where` (file:line) | `Problem` | `Suggested fix`
3. **Requirement checklist** – every numbered requirement in `TASK.md`,
   each marked *met*, *partly met* or *not met*, with one line why.
4. **Plan adherence** – where the code follows or departs from `plan.md`.
5. **Verdict** – one of: *ship as is*, *fix first*, *redo*.

Only report issues you are confident about. If you are unsure, say so instead of
guessing. A short, accurate review is better than a long one.

When `REVIEW.md` is saved, reply with: `REVIEW DONE`.
