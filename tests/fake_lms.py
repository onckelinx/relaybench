#!/usr/bin/env python3
"""A stand-in for LM Studio's `lms` command. It records its arguments and tells the mock server what is loaded."""
import json
import os
import sys

state_dir = os.environ["FAKE_LMS_STATE"]
loaded_path = os.path.join(state_dir, "loaded.json")
args = sys.argv[1:]
with open(os.path.join(state_dir, "lms-calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")


def write_loaded(data):
    with open(loaded_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


if args[:2] == ["unload", "--all"]:
    write_loaded({})
elif args[:1] == ["load"]:
    write_loaded({args[args.index("--identifier") + 1]: args[1]})
elif args[:1] == ["ls"]:
    print(json.dumps([{"modelKey": m, "type": "llm"} for m in os.environ.get("FAKE_LMS_MODELS", "").split(",") if m]))
else:
    sys.exit("fake lms: unknown command %s" % args)
