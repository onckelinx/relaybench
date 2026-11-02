"""Helpers for hidden tests: guarded imports and calls that can't hang the whole suite."""
import importlib
import os
import subprocess
import sys
import threading

import pytest

BUILD_DIR = os.environ.get("BUILD_DIR", "")
if BUILD_DIR and BUILD_DIR not in sys.path:
    sys.path.insert(0, BUILD_DIR)


def load_build_module(name):
    """Import a module from the build. Returns (module, error) so that collection never fails."""
    try:
        return importlib.import_module(name), None
    except BaseException as exc:  # noqa: B902 - a bad build must only fail tests
        if isinstance(exc, KeyboardInterrupt):
            raise
        return None, exc


def limited(fn, *args, seconds=20, **kwargs):
    """Call fn in a daemon thread. A build that loops forever fails this test, not the whole run."""
    box = {}

    def target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: B902 - re-raised in the test below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail("no result after %s seconds" % seconds)
    if "error" in box:
        raise box["error"]
    return box["value"]


def run_cli(module, *args, seconds=30):
    try:
        return subprocess.run([sys.executable, "-m", module] + list(args), cwd=BUILD_DIR or None,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        pytest.fail("the command did not finish within %s seconds" % seconds)
