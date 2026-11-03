<!--
  SYNTHETIC EXAMPLE. Every model here is a mock, and every number is meaningless.
  This shows the format of a report only. It says nothing about any real model.
  Regenerate: RELAYBENCH_WRITE_EXAMPLE=examples/mock-run-report.md python -m pytest tests -k full_run
-->

# Run report: 20261103-2345-toy-bepay

- Task: `toy-bepay`
- Started: 2026-11-03T23:45:27
- Platform: Linux x86_64, Python 3.14.8
- Mode: manual
- Hardware: (left out of this example)
- Backend: openai-compatible
- Quantisation: not reported
- Context length: not controlled by the harness (server setting)
- Harness commit: b009b6f · Task hash: 13c7271457b98e20 · Prompts hash: 3bcf1bd23cdcfbc5 · Scoring hash: bc02bec3ab460ebc
- Planner: mock-planner · Builders: mock-good, mock-buggy, mock-syntax · Reviewers: mock-rev-a, mock-rev-b
- Limits: hidden tests 600 s

## Speed per stage (from the proxy log)

| Stage | Attempts | Model calls | Errors | Input tokens | Output tokens | Median time to first token | Generation speed | Model time | Wall time |
|---|---|---|---|---|---|---|---|---|---|
| Plan (mock-planner) | 1 | 2 | 0 | 2,534 | 127 | 0.0 s | 36863.4 tok/s | 0.0 min | 0.0 min |
| Build A (mock-good) | 1 | 2 | 0 | 3,083 | 510 | 0.0 s | 64403.4 tok/s | 0.0 min | 0.0 min |
| Build B (mock-buggy) | 1 | 2 | 0 | 3,082 | 509 | 0.0 s | 65213.7 tok/s | 0.0 min | 0.0 min |
| Build C (mock-syntax) | 1 | 2 | 0 | 3,043 | 472 | 0.0 s | 69725.3 tok/s | 0.0 min | 0.0 min |
| Review of A (mock-good) by mock-rev-a | 1 | 2 | 0 | 2,929 | 79 | 0.0 s | 33856.1 tok/s | 0.0 min | 0.0 min |
| Review of B (mock-buggy) by mock-rev-a | 1 | 2 | 0 | 2,929 | 79 | 0.0 s | 33228.0 tok/s | 0.0 min | 0.0 min |
| Review of C (mock-syntax) by mock-rev-a | 1 | 2 | 0 | 2,929 | 79 | 0.0 s | 32888.3 tok/s | 0.0 min | 0.0 min |
| Review of A (mock-good) by mock-rev-b | 1 | 2 | 0 | 2,929 | 79 | 0.0 s | 32989.8 tok/s | 0.0 min | 0.0 min |
| Review of B (mock-buggy) by mock-rev-b | 1 | 2 | 0 | 2,929 | 79 | 0.0 s | 32793.9 tok/s | 0.0 min | 0.0 min |
| Review of C (mock-syntax) by mock-rev-b | 1 | 2 | 0 | 2,929 | 79 | 0.0 s | 33666.9 tok/s | 0.0 min | 0.0 min |

*Model time* = time spent waiting for the model. *Wall time* = first to last model call in that stage (includes tool use, your approvals and any gap between attempts).

## Settings sent to the model

| Stage | Temperature | Max tokens |
|---|---|---|
| Plan (mock-planner) | 0.2 | 16384 |
| Build A (mock-good) | 0.2 | 16384 |
| Build B (mock-buggy) | 0.2 | 16384 |
| Build C (mock-syntax) | 0.2 | 16384 |
| Review of A (mock-good) by mock-rev-a | 0.2 | 16384 |
| Review of B (mock-buggy) by mock-rev-a | 0.2 | 16384 |
| Review of C (mock-syntax) by mock-rev-a | 0.2 | 16384 |
| Review of A (mock-good) by mock-rev-b | 0.2 | 16384 |
| Review of B (mock-buggy) by mock-rev-b | 0.2 | 16384 |
| Review of C (mock-syntax) by mock-rev-b | 0.2 | 16384 |

`–` = not sent, so the server's or the model's default applied. *pinned* = set by `request_overrides` in the config, replacing what the agent sent.

## Hidden tests

| Build | Model | Passed | Own tests | Failing tests |
|---|---|---|---|---|
| A | mock-good | 41 of 41 | none found | – |
| B | mock-buggy | 37 of 41 | none found | TestOgmValidation.test_check_00_is_never_valid, TestOgmValidation.test_check_97_when_remainder_zero, TestMakeOgm.test_lower_bound, TestIbanValidation.test_national_check_97 |
| C | mock-syntax | 0 of 41 | none found | TestImport.test_exports_all_functions, TestImport.test_package_imports, TestOgmValidation.test_check_00_is_never_valid, TestOgmValidation.test_check_97_when_remainder_zero, TestOgmValidation.test_mixed_delimiters, TestOgmValidation.test_non_ascii_digits_are_invalid, TestOgmValidation.test_non_string_returns_false, TestOgmValidation.test_plain_digits, TestOgmValidation.test_plus_format, TestOgmValidation.test_star_format, TestOgmValidation.test_surrounding_whitespace, TestOgmValidation.test_wrong_check_number, TestOgmValidation.test_wrong_grouping, TestOgmValidation.test_wrong_length, TestMakeOgm.test_lower_bound, TestMakeOgm.test_out_of_range, TestMakeOgm.test_round_trip, TestMakeOgm.test_upper_bound, TestMakeOgm.test_wrong_types, TestMakeOgm.test_zero_padding, TestIbanValidation.test_another_valid, TestIbanValidation.test_compact, TestIbanValidation.test_iban_ok_but_national_check_wrong, TestIbanValidation.test_letters_in_account, TestIbanValidation.test_national_check_97, TestIbanValidation.test_non_ascii_digits_are_invalid, TestIbanValidation.test_non_string_and_empty, TestIbanValidation.test_other_country, TestIbanValidation.test_spaces_and_lowercase, TestIbanValidation.test_trailing_newline_never_raises, TestIbanValidation.test_wrong_iban_check_digits, TestIbanValidation.test_wrong_length, TestFormatIban.test_format, TestFormatIban.test_format_already_spaced, TestFormatIban.test_format_invalid_raises, TestFormatIban.test_format_non_ascii_digits_raises, TestCli.test_invalid, TestCli.test_no_argument, TestCli.test_non_ascii_digits_print_invalid, TestCli.test_valid_iban_with_spaces, TestCli.test_valid_ogm |

## Judge scores


### Builds (1–5 per criterion, total out of 25)

| Build | Model | Judge | correctness | plan adherence | code quality | edge cases | own tests | Total |
|---|---|---|---|---|---|---|---|---|
| A | mock-good | judge-api | 5 | 3 | 3 | 5 | 1 | 17 |
| A | mock-good | judge-human | 4 | 4 | 4 | 4 | 4 | 20 |
| A | mock-good | judge-paste | 5 | 3 | 3 | 5 | 1 | 17 |
| A | mock-good | **average** | | | | | | **18.0** |
| B | mock-buggy | judge-api | 5 | 3 | 3 | 5 | 1 | 17 |
| B | mock-buggy | judge-human | 4 | 4 | 4 | 4 | 4 | 20 |
| B | mock-buggy | judge-paste | 5 | 3 | 3 | 5 | 1 | 17 |
| B | mock-buggy | **average** | | | | | | **18.0** |
| C | mock-syntax | judge-api | 1 | 3 | 3 | 1 | 1 | 9 |
| C | mock-syntax | judge-human | 4 | 4 | 4 | 4 | 4 | 20 |
| C | mock-syntax | judge-paste | 1 | 3 | 3 | 1 | 1 | 9 |
| C | mock-syntax | **average** | | | | | | **12.7** |

### Reviews

| Review | Reviewer | Judge | Real issues | False alarms | Missed important | Accuracy | Usefulness |
|---|---|---|---|---|---|---|---|
| A (mock-good) | mock-rev-a | judge-api | 0 | 0 | 1 | 3 | 2 |
| A (mock-good) | mock-rev-b | judge-api | 0 | 0 | 1 | 3 | 2 |
| B (mock-buggy) | mock-rev-a | judge-api | 0 | 0 | 1 | 3 | 2 |
| B (mock-buggy) | mock-rev-b | judge-api | 0 | 0 | 1 | 3 | 2 |
| C (mock-syntax) | mock-rev-a | judge-api | 0 | 0 | 1 | 3 | 2 |
| C (mock-syntax) | mock-rev-b | judge-api | 0 | 0 | 1 | 3 | 2 |
| A (mock-good) | mock-rev-a | judge-human | 2 | 2 | 2 | 2 | 2 |
| A (mock-good) | mock-rev-b | judge-human | 2 | 2 | 2 | 2 | 2 |
| B (mock-buggy) | mock-rev-a | judge-human | 2 | 2 | 2 | 2 | 2 |
| B (mock-buggy) | mock-rev-b | judge-human | 2 | 2 | 2 | 2 | 2 |
| C (mock-syntax) | mock-rev-a | judge-human | 2 | 2 | 2 | 2 | 2 |
| C (mock-syntax) | mock-rev-b | judge-human | 2 | 2 | 2 | 2 | 2 |
| A (mock-good) | mock-rev-a | judge-paste | 0 | 0 | 1 | 3 | 2 |
| A (mock-good) | mock-rev-b | judge-paste | 0 | 0 | 1 | 3 | 2 |
| B (mock-buggy) | mock-rev-a | judge-paste | 0 | 0 | 1 | 3 | 2 |
| B (mock-buggy) | mock-rev-b | judge-paste | 0 | 0 | 1 | 3 | 2 |
| C (mock-syntax) | mock-rev-a | judge-paste | 0 | 0 | 1 | 3 | 2 |
| C (mock-syntax) | mock-rev-b | judge-paste | 0 | 0 | 1 | 3 | 2 |

### Verdicts

- **judge-api** prefers Build A (mock-good): Canned verdict from the mock server.
- **judge-human** prefers Build A (mock-good): Filled in by a test.
- **judge-paste** prefers Build A (mock-good): Canned verdict from the mock server.

## Integrity flags

None detected. Detection is best-effort: see METHODOLOGY.md.

## Blind key

- Build A = mock-good
- Build B = mock-buggy
- Build C = mock-syntax
- reviewer-1 = mock-rev-a
- reviewer-2 = mock-rev-b

## My notes

- Hardware and power settings:
- Agent, version and its settings:
- What broke or was confusing:
- What surprised me: nothing
- What to change for the next run:
