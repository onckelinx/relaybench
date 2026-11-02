import json
import sys

from .store import CorruptLogError, KVStore


def _usage():
    print("usage: python -m walkv <path> set <key> <value> | get <key> | del <key> | keys [prefix] | compact",
          file=sys.stderr)
    return 2


def main(argv):
    if len(argv) < 2:
        return _usage()
    path, command, rest = argv[0], argv[1], argv[2:]
    expected = {"set": (2, 2), "get": (1, 1), "del": (1, 1), "keys": (0, 1), "compact": (0, 0)}
    if command not in expected or not expected[command][0] <= len(rest) <= expected[command][1]:
        return _usage()
    try:
        store = KVStore(path)
    except CorruptLogError as exc:
        print("corrupt log: %s" % exc, file=sys.stderr)
        return 3
    with store:
        if command == "set":
            store.set(rest[0], rest[1])
        elif command == "get":
            if rest[0] not in store:
                return 1
            value = store.get(rest[0])
            print(value if isinstance(value, str) else json.dumps(value))
        elif command == "del":
            return 0 if store.delete(rest[0]) else 1
        elif command == "keys":
            for key in store.keys(rest[0] if rest else ""):
                print(key)
        else:
            store.compact()
    return 0


sys.exit(main(sys.argv[1:]))
