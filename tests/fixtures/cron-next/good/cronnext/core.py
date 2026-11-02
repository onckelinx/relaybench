import calendar
import re
from datetime import date, datetime, timedelta

MONTHS = {n.upper(): i for i, n in enumerate(calendar.month_abbr) if n}
DAYS = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
MACROS = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *",
}
ITEM = re.compile(r"^(?:(\*)|([0-9A-Za-z]+)(?:-([0-9A-Za-z]+))?)(?:/([0-9]+))?$")


class CronError(ValueError):
    pass


class Schedule:
    def __init__(self, minutes, hours, days_of_month, months, days_of_week, dom_restricted, dow_restricted):
        self.minutes = minutes
        self.hours = hours
        self.days_of_month = days_of_month
        self.months = months
        self.days_of_week = days_of_week
        self.dom_restricted = dom_restricted
        self.dow_restricted = dow_restricted

    def day_matches(self, day):
        if day.month not in self.months:
            return False
        dom = day.day in self.days_of_month
        dow = (day.weekday() + 1) % 7 in self.days_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        return dom and dow


def _value(text, low, high, names):
    if text.isdigit():
        number = int(text)
    elif names and text.upper() in names:
        number = names[text.upper()]
    else:
        raise CronError("bad value: %r" % text)
    if not low <= number <= high:
        raise CronError("value out of range: %r" % text)
    return number


def _field(text, low, high, names=None):
    values = set()
    for item in text.split(","):
        match = ITEM.match(item)
        if not match:
            raise CronError("bad item: %r" % item)
        star, first, last, step = match.groups()
        step_size = int(step) if step is not None else 1
        if step_size < 1:
            raise CronError("step must be at least 1")
        if star:
            start, end = low, high
        else:
            start = _value(first, low, high, names)
            if last is not None:
                end = _value(last, low, high, names)
            else:
                end = high if step is not None else start
        if start > end:
            raise CronError("range runs backwards: %r" % item)
        values.update(range(start, end + 1, step_size))
    return values


def parse(expr):
    if not isinstance(expr, str):
        raise CronError("expression must be a string")
    text = expr.strip()
    if text.startswith("@"):
        if text.lower() not in MACROS:
            raise CronError("unknown macro: %r" % text)
        text = MACROS[text.lower()]
    fields = text.split()
    if len(fields) != 5:
        raise CronError("expected 5 fields, got %d" % len(fields))
    dow = {d % 7 for d in _field(fields[4], 0, 7, DAYS)}
    return Schedule(_field(fields[0], 0, 59), _field(fields[1], 0, 23), _field(fields[2], 1, 31),
                    _field(fields[3], 1, 12, MONTHS), dow,
                    not fields[2].startswith("*"), not fields[4].startswith("*"))


def _coerce(schedule):
    return schedule if isinstance(schedule, Schedule) else parse(schedule)


def _next_from(schedule, when):
    """The first match strictly after `when`, walking days and then times within a day."""
    minutes, hours = sorted(schedule.minutes), sorted(schedule.hours)
    day = when.date()
    after = (when.hour, when.minute)
    # 400 years is one whole cycle of the Gregorian calendar, weekdays included.
    last = min(day.toordinal() + 366 * 400, date.max.toordinal())
    for ordinal in range(day.toordinal(), last + 1):
        current = date.fromordinal(ordinal)
        if schedule.day_matches(current):
            for hour in hours:
                for minute in minutes:
                    if ordinal > day.toordinal() or (hour, minute) > after:
                        return datetime(current.year, current.month, current.day, hour, minute)
    return None


def next_after(schedule, when):
    schedule = _coerce(schedule)
    if not isinstance(when, datetime) or when.tzinfo is not None:
        raise CronError("when must be a naive datetime")
    return _next_from(schedule, when)


def next_n(schedule, when, n):
    schedule = _coerce(schedule)
    if type(n) is not int or n < 0:
        raise CronError("n must be an int of 0 or more")
    if not isinstance(when, datetime) or when.tzinfo is not None:
        raise CronError("when must be a naive datetime")
    result = []
    current = when
    while len(result) < n:
        current = _next_from(schedule, current)
        if current is None:
            break
        result.append(current)
    return result
