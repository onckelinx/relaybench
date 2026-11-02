"""Hidden acceptance tests for the cron-next task.

The builder models never see this file. pipeline.py runs it against a copy of each
build, whose path is in the BUILD_DIR environment variable.
"""
from datetime import date, datetime, timezone

import pytest

from _support import limited, load_build_module, run_cli

cronnext, IMPORT_ERROR = load_build_module("cronnext")


def api(name):
    if cronnext is None:
        pytest.fail("could not import cronnext: %r" % (IMPORT_ERROR,))
    fn = getattr(cronnext, name, None)
    if fn is None:
        pytest.fail("cronnext does not export %s" % name)
    return fn


def field(expr, name):
    return getattr(limited(api("parse"), expr), name)


def nxt(expr, when):
    return limited(api("next_after"), expr, when)


def test_exports_and_error_type():
    for name in ("parse", "next_after", "next_n"):
        api(name)
    assert issubclass(api("CronError"), ValueError)


@pytest.mark.parametrize("text,expected", [
    ("*", set(range(60))),
    ("*/15", {0, 15, 30, 45}),
    ("5", {5}),
    ("10-12", {10, 11, 12}),
    ("10-20/5", {10, 15, 20}),
    ("50/3", {50, 53, 56, 59}),
    ("1,2,10-11", {1, 2, 10, 11}),
])
def test_minute_field_forms(text, expected):
    assert field(text + " * * * *", "minutes") == expected


def test_step_starts_from_field_minimum():
    assert field("0 0 */10 * *", "days_of_month") == {1, 11, 21, 31}
    assert field("0 0 1 */5 *", "months") == {1, 6, 11}


def test_month_names_any_case():
    assert field("0 0 1 jan,Mar,DEC *", "months") == {1, 3, 12}
    assert field("0 0 1 FEB-apr *", "months") == {2, 3, 4}


def test_day_names_and_seven_is_sunday():
    assert field("0 0 * * MON-FRI", "days_of_week") == {1, 2, 3, 4, 5}
    assert field("0 0 * * sun", "days_of_week") == {0}
    assert field("0 0 * * 7", "days_of_week") == {0}
    assert field("0 0 * * 5-7", "days_of_week") == {5, 6, 0}
    assert field("0 0 * * 0,7", "days_of_week") == {0}


def test_surrounding_and_repeated_whitespace():
    assert field("  5\t*  * * *\n", "minutes") == {5}


@pytest.mark.parametrize("macro,equivalent", [
    ("@yearly", "0 0 1 1 *"), ("@annually", "0 0 1 1 *"), ("@monthly", "0 0 1 * *"),
    ("@weekly", "0 0 * * 0"), ("@daily", "0 0 * * *"), ("@midnight", "0 0 * * *"),
    ("@hourly", "0 * * * *"), ("@DAILY", "0 0 * * *"),
])
def test_macros(macro, equivalent):
    start = datetime(2024, 5, 15, 10, 30)
    assert nxt(macro, start) == nxt(equivalent, start)


@pytest.mark.parametrize("bad", [
    "", "* * * *", "* * * * * *", "60 * * * *", "-1 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *",
    "* * * 0 *", "* * * 13 *", "* * * * 8", "*/0 * * * *", "1-2/0 * * * *", "5-1 * * * *", "1,,2 * * * *",
    ",1 * * * *", "1, * * * *", "a * * * *", "* * * FOO *", "* * * * FUNDAY", "* * * * FRI-MON", "1.5 * * * *",
    "*/ * * * *", "*/-1 * * * *", "@never", "1-2-3 * * * *", "MON * * * *", "* * * MON *", "* * * * JAN",
    "\u00b2 * * * *", "\u0663 * * * *", "*/\u00b2 * * * *", "*/\u0663 * * * *",
])
def test_invalid_expressions(bad):
    with pytest.raises(api("CronError")):
        limited(api("parse"), bad)


@pytest.mark.parametrize("bad", [None, 5, ["* * * * *"]])
def test_parse_non_string(bad):
    with pytest.raises(api("CronError")):
        limited(api("parse"), bad)


def test_strictly_after_on_exact_match():
    assert nxt("* * * * *", datetime(2024, 1, 1, 10, 0, 0)) == datetime(2024, 1, 1, 10, 1)


def test_seconds_and_microseconds_are_cleared():
    assert nxt("* * * * *", datetime(2024, 1, 1, 10, 0, 30, 500)) == datetime(2024, 1, 1, 10, 1)


def test_accepts_parsed_schedule():
    schedule = limited(api("parse"), "30 6 * * *")
    assert nxt(schedule, datetime(2024, 1, 1, 7, 0)) == datetime(2024, 1, 2, 6, 30)


def test_rolls_over_year_end():
    assert nxt("0 0 1 1 *", datetime(2024, 12, 31, 23, 59)) == datetime(2025, 1, 1, 0, 0)


def test_skips_short_months():
    assert nxt("0 0 31 * *", datetime(2024, 1, 31, 0, 0)) == datetime(2024, 3, 31, 0, 0)


def test_leap_day_next_leap_year():
    assert nxt("0 0 29 2 *", datetime(2024, 2, 29, 0, 0)) == datetime(2028, 2, 29, 0, 0)


def test_leap_day_across_non_leap_century():
    assert nxt("0 0 29 2 *", datetime(2096, 3, 1)) == datetime(2104, 2, 29, 0, 0)


@pytest.mark.parametrize("expr", ["0 0 31 2 *", "0 0 30 2 *", "0 0 31 4,6,9,11 *"])
def test_impossible_date_returns_none(expr):
    assert nxt(expr, datetime(2024, 1, 1)) is None


def test_end_of_time_returns_none():
    assert nxt("* * * * *", datetime(9999, 12, 31, 23, 59)) is None


def test_last_representable_minute_is_found():
    assert nxt("59 23 31 12 *", datetime(9999, 1, 1)) == datetime(9999, 12, 31, 23, 59)


def test_dom_and_dow_both_restricted_is_or():
    # 2024-09-06 is a Friday, and 2024-10-11 is a Friday whose next match is the 13th, a Sunday.
    expr = "0 0 13 * FRI"
    assert nxt(expr, datetime(2024, 9, 1)) == datetime(2024, 9, 6)
    assert nxt(expr, datetime(2024, 10, 11)) == datetime(2024, 10, 13)


def test_star_step_day_of_month_is_unrestricted_so_and():
    # */2 starts with *, so both must match: Mondays on an odd day of the month.
    assert nxt("0 0 */2 * MON", datetime(2024, 9, 2)) == datetime(2024, 9, 9)


def test_star_step_day_of_week_is_unrestricted_so_and():
    # */2 in day of week is 0,2,4,6. Combined with the 10th: the first 10th on Sun, Tue, Thu or Sat.
    assert nxt("0 0 10 * */2", datetime(2024, 1, 1)) == datetime(2024, 2, 10)


def test_only_day_of_week_restricted():
    assert nxt("0 9 * * MON", datetime(2024, 9, 3)) == datetime(2024, 9, 9, 9, 0)


def test_only_day_of_month_restricted():
    assert nxt("0 9 15 * *", datetime(2024, 9, 3)) == datetime(2024, 9, 15, 9, 0)


def test_or_rule_respects_month():
    assert nxt("0 0 13 2 FRI", datetime(2024, 3, 1)) == datetime(2025, 2, 7)


@pytest.mark.parametrize("bad", [
    date(2024, 1, 1), "2024-01-01", None, 1700000000, datetime(2024, 1, 1, tzinfo=timezone.utc),
])
def test_next_after_rejects_bad_when(bad):
    with pytest.raises(api("CronError")):
        limited(api("next_after"), "* * * * *", bad)


def test_next_after_bad_expression_raises():
    with pytest.raises(api("CronError")):
        limited(api("next_after"), "not cron", datetime(2024, 1, 1))


def test_next_n_sequence():
    assert limited(api("next_n"), "0 */6 * * *", datetime(2024, 1, 1, 1), 3) == [
        datetime(2024, 1, 1, 6), datetime(2024, 1, 1, 12), datetime(2024, 1, 1, 18)]


def test_next_n_zero():
    assert limited(api("next_n"), "* * * * *", datetime(2024, 1, 1), 0) == []


def test_next_n_never_fires():
    assert limited(api("next_n"), "0 0 31 2 *", datetime(2024, 1, 1), 3) == []


def test_next_n_runs_out():
    result = limited(api("next_n"), "58-59 23 31 12 *", datetime(9999, 12, 31, 23, 57), 5)
    assert result == [datetime(9999, 12, 31, 23, 58), datetime(9999, 12, 31, 23, 59)]


@pytest.mark.parametrize("bad", [-1, 1.5, "3", True, None])
def test_next_n_bad_count(bad):
    with pytest.raises(api("CronError")):
        limited(api("next_n"), "* * * * *", datetime(2024, 1, 1), bad)


def test_cli_prints_iso():
    r = run_cli("cronnext", "30 6 * * *", "--from", "2024-01-01T07:00:00")
    assert (r.stdout.strip(), r.returncode) == ("2024-01-02T06:30:00", 0)


def test_cli_from_without_seconds_and_count():
    r = run_cli("cronnext", "0 0 1 * *", "--from", "2024-01-15T00:00", "-n", "2")
    assert r.returncode == 0
    assert r.stdout.split() == ["2024-02-01T00:00:00", "2024-03-01T00:00:00"]


def test_cli_default_from_is_now():
    r = run_cli("cronnext", "* * * * *")
    assert r.returncode == 0
    assert datetime.strptime(r.stdout.strip(), "%Y-%m-%dT%H:%M:%S") > datetime(2020, 1, 1)


def test_cli_no_match():
    r = run_cli("cronnext", "0 0 31 2 *", "--from", "2024-01-01T00:00:00")
    assert (r.stdout.strip(), r.returncode) == ("no match", 1)


@pytest.mark.parametrize("args", [
    ("not cron",), ("* * * * *", "--from", "yesterday"), ("* * * * *", "-n", "x"), ("* * * * *", "-n", "-1"),
    (), ("* * * * *", "extra"), ("* * * * *", "-n", "\u00b2"),
])
def test_cli_errors_go_to_stderr_with_code_2(args):
    r = run_cli("cronnext", *args)
    assert r.returncode == 2
    assert r.stderr.strip()
    assert not r.stdout.strip()
