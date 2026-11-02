import sys
from datetime import datetime

from .core import CronError, next_n, parse


def _usage(message):
    print("error: %s\nusage: python -m cronnext <expr> [--from ISO] [-n N]" % message, file=sys.stderr)
    return 2


def main(argv):
    positional, start, count = [], None, 1
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--from", "-n"):
            if i + 1 >= len(argv):
                return _usage("%s needs a value" % arg)
            value = argv[i + 1]
            if arg == "--from":
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
                    try:
                        start = datetime.strptime(value, fmt)
                        break
                    except ValueError:
                        pass
                if start is None:
                    return _usage("bad --from value")
            else:
                if not (value.isascii() and value.isdigit()):
                    return _usage("bad -n value")
                count = int(value)
            i += 2
        else:
            positional.append(arg)
            i += 1
    if len(positional) != 1:
        return _usage("expected one expression")
    try:
        schedule = parse(positional[0])
    except CronError as exc:
        return _usage(str(exc))
    times = next_n(schedule, start or datetime.now(), count)
    if not times and count:
        print("no match")
        return 1
    for moment in times:
        print(moment.strftime("%Y-%m-%dT%H:%M:%S"))
    return 0


sys.exit(main(sys.argv[1:]))
