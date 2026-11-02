# Task: `bepay` — Belgian payment reference validators

Build a small Python package called `bepay` that validates and formats two Belgian
payment identifiers: the **structured communication** (OGM/VCS, "gestructureerde
mededeling") and the **Belgian IBAN**. It also needs a tiny command-line interface.

Use **Python 3.9+ and the standard library only** (no third-party packages).

Wherever this task says *digits*, it means the ASCII digits `0`–`9`.

## Required layout

```
bepay/
  __init__.py      # re-exports the four functions below
  validators.py    # the four functions
  __main__.py      # the command-line interface
```

You may add your own tests (for example a `tests/` folder using `unittest`).

## 1. `is_valid_ogm(text) -> bool`

A structured communication is 12 digits: a 10-digit base number followed by a
2-digit check number. The check number is `base % 97`, except that a result of `0`
becomes `97`.

Accepted input formats (leading and trailing whitespace is ignored):

- `+++123/4567/89002+++`
- `***123/4567/89002***`
- `123456789002` (12 digits, no separators)

Rules:

- With delimiters, the digits must be grouped exactly 3/4/5 with `/` between groups.
- The opening and closing delimiter must be the same (`+++ … +++` or `*** … ***`).
- Anything else is invalid.
- Returns `True` only if the format is accepted **and** the check number is correct.
- If `text` is not a string, return `False` (never raise).

## 2. `make_ogm(base) -> str`

- `base` is an `int` from `0` to `9999999999` inclusive.
- Returns the formatted communication, zero-padded: `make_ogm(123456789)` →
  `"+++012/3456/78939+++"`.
- Raises `ValueError` if `base` is out of range or is not an `int`
  (`bool`, `float` and `str` count as not an `int`).

## 3. `is_valid_be_iban(text) -> bool`

A Belgian IBAN is `BE` + 2 IBAN check digits + a 12-digit account number
(16 characters in total), for example `BE68 5390 0754 7034`.

Rules:

- Ignore all spaces; accept upper- and lowercase.
- Must start with `BE` and be followed by exactly 14 digits.
- The standard IBAN mod-97 check must pass (move the first 4 characters to the end,
  replace letters with numbers A=10 … Z=35, the result mod 97 must equal 1).
- The Belgian national check must **also** pass: the last 2 digits of the 12-digit
  account number must equal the first 10 digits `% 97`, where `0` becomes `97`.
- If `text` is not a string, return `False` (never raise).

## 4. `format_be_iban(text) -> str`

- Returns the IBAN in uppercase, in groups of 4 separated by single spaces:
  `format_be_iban("be68539007547034")` → `"BE68 5390 0754 7034"`.
- Raises `ValueError` if the IBAN is not valid according to `is_valid_be_iban`.

## 5. Command line: `python -m bepay <value>`

- Prints exactly `valid OGM` and exits with code `0` if the value is a valid
  structured communication.
- Otherwise prints exactly `valid IBAN` and exits with code `0` if it is a valid
  Belgian IBAN.
- Otherwise prints exactly `invalid` and exits with code `1`.
- With no argument (or more than one), print a short usage message to **stderr**
  and exit with code `2`.

## Done when

- The package works as described above.
- Your own tests pass.
- Your work is committed with Git.
