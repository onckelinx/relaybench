"""Hidden acceptance tests for the wal-kv task.

The builder models never see this file. pipeline.py runs it against a copy of each
build, whose path is in the BUILD_DIR environment variable.
"""
import json
import os
import zlib

import pytest

from _support import limited, load_build_module, run_cli

walkv, IMPORT_ERROR = load_build_module("walkv")


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def api(name):
    if walkv is None:
        pytest.fail("could not import walkv: %r" % (IMPORT_ERROR,))
    item = getattr(walkv, name, None)
    if item is None:
        pytest.fail("walkv does not export %s" % name)
    return item


def KVStore(*args, **kwargs):
    return limited(api("KVStore"), *args, **kwargs)


@pytest.fixture
def log(tmp_path):
    return str(tmp_path / "data.wal")


def records(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line.split(" ", 1)[1]) for line in fh.read().splitlines()]


def record_line(ops):
    payload = json.dumps({"ops": ops})
    return "%08x %s\n" % (zlib.crc32(payload.encode("utf-8")), payload)


# ---- the reported bug and the existing behaviour

@pytest.mark.parametrize("value", [0, "", [], False, {}])
def test_falsy_values_are_returned(value):
    store = KVStore()
    store.set("k", value)
    assert store.get("k", "default") == value


def test_stored_none_beats_default():
    store = KVStore()
    store.set("k", None)
    assert store.get("k", "default") is None
    assert "k" in store


def test_existing_api_still_works():
    store = KVStore()
    store.set("b", 1)
    store.set("a", 2)
    assert store.keys() == ["a", "b"]
    assert len(store) == 2
    assert store.delete("a") is True
    assert store.delete("a") is False


@pytest.mark.parametrize("bad", ["", None, 5])
def test_bad_keys_raise(bad):
    store = KVStore()
    for call in (lambda: store.set(bad, 1), lambda: store.get(bad), lambda: store.delete(bad)):
        with pytest.raises(ValueError):
            call()
    assert (bad in store) is False


def test_error_types():
    assert issubclass(api("TransactionError"), Exception)
    assert issubclass(api("CorruptLogError"), Exception)


# ---- isolation

def test_values_are_copied_on_set_and_get():
    store = KVStore()
    value = [1, 2]
    store.set("k", value)
    value.append(3)
    assert store.get("k") == [1, 2]
    store.get("k").append(4)
    assert store.get("k") == [1, 2]


def test_values_behave_like_json_in_memory():
    store = KVStore()
    store.set("k", (1, 2))
    assert store.get("k") == [1, 2]


def test_unserialisable_value_changes_nothing(log):
    with KVStore(log) as store:
        store.set("k", 1)
        size = os.path.getsize(log)
        with pytest.raises((TypeError, ValueError)):
            store.set("k", {1, 2})
        assert store.get("k") == 1
        assert os.path.getsize(log) == size


# ---- durability and the log format

def test_data_survives_reopen(log):
    with KVStore(log) as store:
        store.set("a", {"nested": [1, "two", None]})
        store.set("b", 2)
        store.delete("b")
    with KVStore(log) as store:
        assert store.get("a") == {"nested": [1, "two", None]}
        assert "b" not in store


def test_creates_missing_file(log):
    KVStore(log).close()
    assert os.path.exists(log)


def test_empty_file_is_valid(log):
    open(log, "w").close()
    with KVStore(log) as store:
        assert len(store) == 0


def test_writes_are_on_disk_before_close(log):
    store = KVStore(log)
    store.set("a", 1)
    with KVStore(log) as second:  # another reader, as after a crash
        assert second.get("a") == 1
    store.close()


def test_log_format(log):
    with KVStore(log) as store:
        store.set("a", "é")
        store.delete("a")
    with open(log, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 2
    for line in lines:
        crc, payload = line.split(" ", 1)
        assert crc == "%08x" % zlib.crc32(payload.encode("utf-8"))
    assert json.loads(lines[0].split(" ", 1)[1])["ops"] == [["set", "a", "é", None]]
    assert json.loads(lines[1].split(" ", 1)[1])["ops"] == [["del", "a"]]


def test_delete_missing_writes_nothing(log):
    with KVStore(log) as store:
        store.delete("nope")
    assert os.path.getsize(log) == 0


def test_replays_a_log_written_elsewhere(log):
    with open(log, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(record_line([["set", "a", 1, None], ["set", "b", 2, None]]))
        fh.write(record_line([["del", "b"]]))
    with KVStore(log) as store:
        assert store.get("a") == 1
        assert "b" not in store


def test_closed_store_raises(log):
    store = KVStore(log)
    store.close()
    store.close()
    for call in (lambda: store.get("a"), lambda: store.set("a", 1), lambda: store.keys(), store.begin):
        with pytest.raises(ValueError):
            call()


# ---- expiry

def test_ttl_expires_at_exact_time():
    clock = Clock()
    store = KVStore(clock=clock)
    store.set("k", 1, ttl=10)
    clock.now = 1009.9
    assert store.get("k") == 1
    clock.now = 1010.0
    assert store.get("k", "gone") == "gone"
    assert "k" not in store
    assert len(store) == 0
    assert store.keys() == []
    assert store.delete("k") is False


def test_expiry_survives_reopen(log):
    clock = Clock()
    with KVStore(log, clock=clock) as store:
        store.set("k", 1, ttl=10)
    assert records(log)[0]["ops"] == [["set", "k", 1, 1010.0]]
    with KVStore(log, clock=clock) as store:
        assert store.get("k") == 1
    clock.now = 1011
    with KVStore(log, clock=clock) as store:
        assert "k" not in store


def test_set_without_ttl_clears_expiry():
    clock = Clock()
    store = KVStore(clock=clock)
    store.set("k", 1, ttl=10)
    store.set("k", 2)
    clock.now = 5000
    assert store.get("k") == 2


@pytest.mark.parametrize("bad", [0, -1, True, "10"])
def test_bad_ttl_raises(bad):
    store = KVStore(clock=Clock())
    with pytest.raises(ValueError):
        store.set("k", 1, ttl=bad)
    assert "k" not in store


def test_set_over_expired_key_counts_once():
    clock = Clock()
    store = KVStore(clock=clock)
    store.set("k", 1, ttl=1)
    clock.now = 2000
    store.set("k", 2)
    assert len(store) == 1


# ---- transactions

def test_commit_and_rollback_without_transaction():
    store = KVStore()
    with pytest.raises(api("TransactionError")):
        store.commit()
    with pytest.raises(api("TransactionError")):
        store.rollback()


def test_rollback_undoes_changes():
    store = KVStore()
    store.set("keep", 1)
    store.set("gone", 2)
    store.begin()
    store.set("keep", 100)
    store.set("new", 3)
    assert store.delete("gone") is True
    assert (store.get("keep"), store.get("new"), "gone" in store) == (100, 3, False)
    store.rollback()
    assert (store.get("keep"), store.get("new"), store.get("gone")) == (1, None, 2)


def test_nested_rollback_only_undoes_inner():
    store = KVStore()
    store.begin()
    store.set("outer", 1)
    store.begin()
    store.set("inner", 2)
    store.rollback()
    assert store.get("outer") == 1
    assert "inner" not in store
    store.commit()
    assert store.get("outer") == 1


def test_inner_commit_is_undone_by_outer_rollback():
    store = KVStore()
    store.begin()
    store.begin()
    store.set("k", 1)
    store.commit()
    assert store.get("k") == 1
    store.rollback()
    assert "k" not in store


def test_three_levels_of_shadowing():
    store = KVStore()
    store.set("k", 0)
    store.begin()
    store.set("k", 1)
    store.begin()
    store.delete("k")
    store.begin()
    store.set("k", 3)
    store.rollback()
    assert "k" not in store
    assert store.keys() == []
    store.rollback()
    assert store.get("k") == 1
    store.commit()
    assert store.get("k") == 1


def test_transaction_writes_one_record_at_outermost_commit(log):
    with KVStore(log) as store:
        store.begin()
        store.set("a", 1)
        store.begin()
        store.set("b", 2)
        store.commit()
        assert os.path.getsize(log) == 0
        store.delete("a")
        store.commit()
        assert len(records(log)) == 1
    with KVStore(log) as store:
        assert store.keys() == ["b"]


def test_empty_commit_writes_nothing(log):
    with KVStore(log) as store:
        store.begin()
        store.commit()
        store.begin()
        store.set("a", 1)
        store.rollback()
        assert os.path.getsize(log) == 0


def test_uncommitted_is_lost_on_crash(log):
    store = KVStore(log)
    store.set("a", 1)
    store.begin()
    store.set("b", 2)
    with KVStore(log) as second:
        assert second.keys() == ["a"]
    store.close()
    with KVStore(log) as third:
        assert third.keys() == ["a"]


def test_transaction_context_manager(log):
    with KVStore(log) as store:
        with store.transaction():
            store.set("a", 1)
        assert store.get("a") == 1
        with pytest.raises(KeyError):
            with store.transaction():
                store.set("b", 2)
                raise KeyError("boom")
        assert "b" not in store
    with KVStore(log) as store:
        assert store.keys() == ["a"]


def test_ttl_inside_transaction_uses_set_time(log):
    clock = Clock()
    with KVStore(log, clock=clock) as store:
        store.begin()
        store.set("k", 1, ttl=10)
        clock.now += 100
        store.commit()
        assert "k" not in store
    assert records(log)[0]["ops"][0][3] == 1010.0


# ---- recovery

def three_records(log):
    with KVStore(log) as store:
        store.set("a", 1)
        store.set("b", 2)
        store.set("c", 3)


def test_torn_tail_is_dropped_and_file_truncated(log):
    three_records(log)
    size = os.path.getsize(log)
    with open(log, "r+b") as fh:
        fh.truncate(size - 4)
    with KVStore(log) as store:
        assert store.keys() == ["a", "b"]
        store.set("d", 4)
    with KVStore(log) as store:
        assert store.keys() == ["a", "b", "d"]
    assert len(records(log)) == 3


def test_bad_crc_on_last_record_is_dropped(log):
    three_records(log)
    with open(log, "rb") as fh:
        data = fh.read()
    lines = data.splitlines(keepends=True)
    lines[-1] = b"00000000" + lines[-1][8:]
    with open(log, "wb") as fh:
        fh.write(b"".join(lines))
    with KVStore(log) as store:
        assert store.keys() == ["a", "b"]


def test_garbage_tail_is_dropped(log):
    three_records(log)
    with open(log, "ab") as fh:
        fh.write(b"\xff\xfe not a record")
    with KVStore(log) as store:
        assert store.keys() == ["a", "b", "c"]


def test_damage_in_the_middle_raises_and_keeps_file(log):
    three_records(log)
    with open(log, "rb") as fh:
        data = fh.read()
    lines = data.splitlines(keepends=True)
    lines[1] = lines[1].replace(b'"b"', b'"x"')
    damaged = b"".join(lines)
    with open(log, "wb") as fh:
        fh.write(damaged)
    with pytest.raises(api("CorruptLogError")):
        KVStore(log)
    with open(log, "rb") as fh:
        assert fh.read() == damaged


def test_torn_transaction_is_all_or_nothing(log):
    with KVStore(log) as store:
        store.set("before", 1)
        with store.transaction():
            store.set("a", 1)
            store.set("b", 2)
    with open(log, "r+b") as fh:
        fh.truncate(os.path.getsize(log) - 1)
    with KVStore(log) as store:
        assert store.keys() == ["before"]


# ---- compaction

def test_compact_shrinks_and_keeps_state(log):
    clock = Clock()
    with KVStore(log, clock=clock) as store:
        for i in range(200):
            store.set("k", "value-%d" % i)
        store.set("other", [1, 2])
        store.set("short", 1, ttl=5)
        store.set("long", 2, ttl=500)
        store.set("deleted", 1)
        store.delete("deleted")
        clock.now += 10
        before = os.path.getsize(log)
        store.compact()
        assert os.path.getsize(log) < before / 5
        assert store.keys() == ["k", "long", "other"]
        store.set("after", True)
    assert "short" not in json.dumps(records(log))
    with KVStore(log, clock=clock) as store:
        assert store.keys() == ["after", "k", "long", "other"]
        assert store.get("k") == "value-199"
        clock.now += 1000
        assert "long" not in store


def test_compact_leaves_no_other_files(tmp_path):
    path = str(tmp_path / "data.wal")
    with KVStore(path) as store:
        store.set("a", 1)
        store.compact()
    assert os.listdir(str(tmp_path)) == ["data.wal"]


def test_compact_in_transaction_raises(log):
    with KVStore(log) as store:
        store.begin()
        with pytest.raises(api("TransactionError")):
            store.compact()
        store.rollback()


def test_compact_in_memory_is_harmless():
    store = KVStore()
    store.set("a", 1)
    store.compact()
    assert store.get("a") == 1


# ---- command line

def test_cli_round_trip_across_processes(log):
    assert run_cli("walkv", log, "set", "a:1", "hello").returncode == 0
    assert run_cli("walkv", log, "set", "a:2", "world").returncode == 0
    assert run_cli("walkv", log, "set", "b", "x").returncode == 0
    r = run_cli("walkv", log, "get", "a:1")
    assert (r.stdout.strip(), r.returncode) == ("hello", 0)
    r = run_cli("walkv", log, "keys", "a:")
    assert (r.stdout.split(), r.returncode) == (["a:1", "a:2"], 0)
    assert run_cli("walkv", log, "compact").returncode == 0
    assert run_cli("walkv", log, "del", "b").returncode == 0
    assert run_cli("walkv", log, "del", "b").returncode == 1


def test_cli_get_missing(log):
    assert run_cli("walkv", log, "set", "there", "x").returncode == 0  # a crashing build also exits with 1
    r = run_cli("walkv", log, "get", "nope")
    assert (r.stdout.strip(), r.returncode) == ("", 1)
    assert not r.stderr.strip()


def test_cli_prints_non_string_as_json(log):
    with KVStore(log) as store:
        store.set("n", {"a": 1})
    r = run_cli("walkv", log, "get", "n")
    assert (json.loads(r.stdout), r.returncode) == ({"a": 1}, 0)


@pytest.mark.parametrize("args", [(), ("only-a-path",), ("p.wal", "dance"), ("p.wal", "set", "k")])
def test_cli_wrong_usage(args, tmp_path):
    fixed = [str(tmp_path / a) if a.endswith(".wal") else a for a in args]
    r = run_cli("walkv", *fixed)
    assert r.returncode == 2
    assert r.stderr.strip()


def test_cli_corrupt_log(log):
    three_records(log)
    with open(log, "rb") as fh:
        lines = fh.read().splitlines(keepends=True)
    lines[0] = b"00000000" + lines[0][8:]
    with open(log, "wb") as fh:
        fh.write(b"".join(lines))
    r = run_cli("walkv", log, "keys")
    assert r.returncode == 3
    assert r.stderr.strip()
