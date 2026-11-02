"""A key-value store with a write-ahead log, nested transactions and expiry."""
import contextlib
import json
import os
import time
import zlib

_DELETED = object()


class TransactionError(Exception):
    pass


class CorruptLogError(Exception):
    pass


def _encode(ops):
    payload = json.dumps({"ops": ops}, ensure_ascii=False, separators=(",", ":"))
    return ("%08x %s\n" % (zlib.crc32(payload.encode("utf-8")), payload)).encode("utf-8")


def _decode(line):
    """The ops of one complete line, or None if it is not a good record."""
    try:
        if not line.endswith(b"\n"):
            return None
        text = line[:-1].decode("utf-8")
        crc, payload = text.split(" ", 1)
        if len(crc) != 8 or int(crc, 16) != zlib.crc32(payload.encode("utf-8")):
            return None
        return json.loads(payload)["ops"]
    except (ValueError, KeyError, TypeError):
        return None


class KVStore:
    def __init__(self, path=None, clock=time.time):
        self._path = path
        self._clock = clock
        self._data = {}  # key -> (json text, expires_at)
        self._layers = []  # one dict per open transaction: key -> entry or _DELETED
        self._file = None
        self._closed = False
        if path is not None:
            self._replay()
            self._file = open(path, "ab")

    def _replay(self):
        if not os.path.exists(self._path):
            return
        with open(self._path, "rb") as fh:
            lines = fh.read().splitlines(keepends=True)
        good_size = 0
        for index, line in enumerate(lines):
            ops = _decode(line)
            if ops is None:
                if index != len(lines) - 1:
                    raise CorruptLogError("bad record %d in %s" % (index + 1, self._path))
                with open(self._path, "r+b") as fh:
                    fh.truncate(good_size)
                return
            for op in ops:
                if op[0] == "set":
                    self._data[op[1]] = (json.dumps(op[2]), op[3])
                else:
                    self._data.pop(op[1], None)
            good_size += len(line)

    def _check_open(self):
        if self._closed:
            raise ValueError("store is closed")

    @staticmethod
    def _check_key(key):
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")

    def _append(self, ops):
        if self._file is not None:
            self._file.write(_encode(ops))
            self._file.flush()
            os.fsync(self._file.fileno())

    def _lookup(self, key):
        for layer in reversed(self._layers):
            if key in layer:
                entry = layer[key]
                break
        else:
            entry = self._data.get(key, _DELETED)
        if entry is _DELETED:
            return _DELETED
        if entry[1] is not None and self._clock() >= entry[1]:
            return _DELETED
        return entry

    def _apply(self, key, entry):
        if self._layers:
            self._layers[-1][key] = entry
            return
        if entry is _DELETED:
            self._append([["del", key]])
            self._data.pop(key, None)
        else:
            self._append([["set", key, json.loads(entry[0]), entry[1]]])
            self._data[key] = entry

    def set(self, key, value, ttl=None):
        self._check_open()
        self._check_key(key)
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0):
            raise ValueError("ttl must be a positive number")
        text = json.dumps(value)
        self._apply(key, (text, None if ttl is None else self._clock() + ttl))

    def get(self, key, default=None):
        self._check_open()
        self._check_key(key)
        entry = self._lookup(key)
        return default if entry is _DELETED else json.loads(entry[0])

    def delete(self, key):
        self._check_open()
        self._check_key(key)
        if self._lookup(key) is _DELETED:
            return False
        self._apply(key, _DELETED)
        return True

    def _visible(self):
        keys = set(self._data)
        for layer in self._layers:
            keys.update(layer)
        return [k for k in keys if self._lookup(k) is not _DELETED]

    def keys(self, prefix=""):
        self._check_open()
        return sorted(k for k in self._visible() if k.startswith(prefix))

    def __contains__(self, key):
        self._check_open()
        return isinstance(key, str) and bool(key) and self._lookup(key) is not _DELETED

    def __len__(self):
        self._check_open()
        return len(self._visible())

    def begin(self):
        self._check_open()
        self._layers.append({})

    def commit(self):
        self._check_open()
        if not self._layers:
            raise TransactionError("no open transaction")
        layer = self._layers.pop()
        if self._layers:
            self._layers[-1].update(layer)
            return
        ops = []
        for key, entry in layer.items():
            if entry is _DELETED:
                if key in self._data:
                    ops.append(["del", key])
            else:
                ops.append(["set", key, json.loads(entry[0]), entry[1]])
        if ops:
            self._append(ops)
        for key, entry in layer.items():
            if entry is _DELETED:
                self._data.pop(key, None)
            else:
                self._data[key] = entry

    def rollback(self):
        self._check_open()
        if not self._layers:
            raise TransactionError("no open transaction")
        self._layers.pop()

    @contextlib.contextmanager
    def transaction(self):
        self.begin()
        try:
            yield self
        except BaseException:
            self.rollback()
            raise
        self.commit()

    def compact(self):
        self._check_open()
        if self._layers:
            raise TransactionError("can't compact inside a transaction")
        now = self._clock()
        self._data = {k: e for k, e in self._data.items() if e[1] is None or now < e[1]}
        if self._path is None:
            return
        tmp = self._path + ".compact"
        with open(tmp, "wb") as fh:
            if self._data:
                fh.write(_encode([["set", k, json.loads(e[0]), e[1]] for k, e in sorted(self._data.items())]))
            fh.flush()
            os.fsync(fh.fileno())
        self._file.close()
        os.replace(tmp, self._path)
        self._file = open(self._path, "ab")

    def close(self):
        if self._closed:
            return
        self._layers = []
        self._closed = True
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        self._check_open()
        return self

    def __exit__(self, *_exc):
        self.close()
