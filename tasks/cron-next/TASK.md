# Task: `cronnext` — cron schedule parser and next-run calculator

Build a Python package called `cronnext` that parses standard 5-field cron expressions
and calculates when a schedule fires next. It also needs a small command-line interface.

Use **Python 3.9+ and the standard library only** (no third-party packages).

## Required layout

```
cronnext/
  __init__.py      # re-exports CronError, parse, next_after and next_n
  __main__.py      # the command-line interface
```

You may add more modules, and your own tests (for example a `tests/` folder using `unittest`).

## 1. Expressions

An expression has exactly five fields separated by whitespace:

| Field | Allowed numbers | Names (any letter case) |
|---|---|---|
| minute | 0–59 | |
| hour | 0–23 | |
| day of month | 1–31 | |
| month | 1–12 | `JAN` … `DEC` |
| day of week | 0–7, where both 0 and 7 mean Sunday | `SUN` … `SAT` |

Numbers are written with the ASCII digits `0`–`9`.

A field is a comma-separated list of one or more items. Each item is one of:

- `*` – every value
- `*/n` – every n-th value, starting from the field's minimum
- `a` – one value
- `a-b` – every value from a to b inclusive
- `a-b/n` – every n-th value from a to b, starting at a
- `a/n` – every n-th value from a up to the field's maximum

Rules:

- `a`, `b` may be names where the field has names, including in ranges (`MON-FRI`).
- `n` must be a whole number of at least 1.
- `a` may not be greater than `b`; ranges do not wrap around (`FRI-MON` is invalid).
- In the day-of-week field, `7` is Sunday also inside ranges: `5-7` means Friday, Saturday, Sunday.
- Spaces inside a field are not possible, and empty list items (`1,,2`) are invalid.

These macros are also accepted, in any letter case:

| Macro | Means |
|---|---|
| `@yearly`, `@annually` | `0 0 1 1 *` |
| `@monthly` | `0 0 1 * *` |
| `@weekly` | `0 0 * * 0` |
| `@daily`, `@midnight` | `0 0 * * *` |
| `@hourly` | `0 * * * *` |

Leading and trailing whitespace around the expression is ignored.

## 2. `CronError`

An exception class, a subclass of `ValueError`. Every invalid input described below raises it.

## 3. `parse(expr) -> schedule`

- Returns a schedule object with these attributes, each a `set` of `int`:
  `minutes`, `hours`, `days_of_month`, `months`, `days_of_week`.
- `days_of_week` uses 0–6 with Sunday as 0, so a `7` in the expression becomes `0`.
- Raises `CronError` for anything invalid: the wrong number of fields, values out of range,
  unknown names or macros, a step of `0`, a non-string `expr`, and so on.

## 4. `next_after(schedule, when) -> datetime | None`

- `schedule` is an expression string or a schedule returned by `parse`.
- `when` must be a naive `datetime` (no `tzinfo`). Anything else raises `CronError`.
  A plain `date` is not a `datetime`.
- Returns the first matching minute **strictly after** `when`, as a `datetime` with
  seconds and microseconds set to 0. A `when` of 10:00:30 is followed by 10:01:00 for
  `* * * * *`, and a `when` of exactly 10:00:00 is also followed by 10:01:00.
- Returns `None` if the schedule never fires again, for example `0 0 31 2 *`. This includes
  reaching the largest date `datetime` can represent. It must not loop forever, and it must
  finish within a few seconds.
- It must find a match however far ahead it is. `0 0 29 2 *` from 2096-03-01 fires on
  2104-02-29, because 2100 is not a leap year.

**Day matching.** A field is *restricted* unless its text begins with `*`. So `*/2` counts
as unrestricted, like `*`.

- If both day of month and day of week are restricted, a day matches when **either** matches.
- Otherwise a day matches when **both** match (an unrestricted field holds its values as normal).

For example `0 0 13 * FRI` fires on every 13th and on every Friday, while
`0 0 */2 * MON` fires only on Mondays that fall on an odd day of the month.

A macro counts as the expression it stands for.

## 5. `next_n(schedule, when, n) -> list`

- The next `n` firing times in order, each strictly after the one before, the first strictly
  after `when`.
- Shorter than `n` if the schedule runs out; empty if it never fires.
- `n` must be an `int` of 0 or more. Anything else (`bool` included) raises `CronError`.

## 6. Command line: `python -m cronnext <expr> [--from ISO] [-n N]`

- `--from` is the start time in the form `YYYY-MM-DDTHH:MM:SS` or `YYYY-MM-DDTHH:MM`.
  The default is the current local time.
- `-n` is how many firing times to print, default 1.
- Prints each firing time on its own line as `YYYY-MM-DDTHH:MM:SS` and exits with code `0`.
- If the schedule never fires again, prints exactly `no match` and exits with code `1`.
- An invalid expression, `--from` value or `-n` value, or missing or extra arguments: prints a
  short message to **stderr** and exits with code `2`.

## Done when

- The package works as described above.
- Your own tests pass.
- Your work is committed with Git.
