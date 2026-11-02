# Task: `walkv` — add durability, transactions and expiry to a key-value store

The project in this folder is a small in-memory key-value store, `walkv.KVStore`, with its own
tests. Extend it into a durable store with a write-ahead log. Also fix the reported bug below.

Use **Python 3.9+ and the standard library only** (no third-party packages). Keep the package
name and keep the existing tests passing; you may add tests. Everything must also work on Windows,
so close a file before you replace or remove it.

## 1. Bug report

> `store.get("count", 10)` returns `10` when the stored value is `0`. The same happens for `""`,
> `[]` and `False`.

A stored value must always be returned, whatever it is. The default is only for missing keys.
A stored `None` is returned as `None`, even when a default is given.

## 2. The API

`walkv` must export `KVStore`, `TransactionError` and `CorruptLogError`. The two errors are
subclasses of `Exception`.

```python
KVStore(path=None, clock=time.time)
```

- `path`: the log file. It is created if missing, and replayed if it exists. With `None` the
  store lives in memory only, and everything below still works except durability.
- `clock`: a function returning the current time in seconds. Expiry always uses it.

The existing methods stay: `get(key, default=None)`, `delete(key)` (returns `True` if the key
existed and has not expired), `keys(prefix="")` (sorted), `key in store`, `len(store)`.

New or changed:

- `set(key, value, ttl=None)`
  - Keys: a non-empty `str`, otherwise `ValueError`, in every method that takes a key.
    `key in store` is the exception: it just gives `False`.
  - `value` must be JSON-serialisable. Otherwise raises `TypeError` or `ValueError`, and neither
    the store nor the log changes.
- `close()`: closes the file. Calling it twice is fine. An open transaction is rolled back. After
  `close()`, every other method raises `ValueError`.
- `compact()`, `begin()`, `commit()`, `rollback()`, `transaction()`: see below.
- `KVStore` works as a context manager (`with KVStore(path) as store:`), closing on exit.

**Values are isolated.** `set` and `get` work with copies. Changing a list after `set`, or
changing the list `get` returned, does not change what is stored. Values behave as if they went
through JSON, in memory as well as after a reopen: a tuple comes back as a list.

## 3. Expiry

- `ttl` is a positive number of seconds; anything else (`0`, negative, `bool`, a string) raises
  `ValueError`. It sets the expiry time to `clock() + ttl`, computed when `set` is called.
- A key is expired once `clock() >= expiry time`. An expired key is treated as missing by every
  method, and doesn't count in `len`.
- `set` without `ttl` removes any earlier expiry of that key.
- Expiry times are absolute and stored in the log, so they survive a reopen.

## 4. Transactions

- `begin()` opens a transaction. Transactions can be nested to any depth.
- Inside a transaction, reads see the transaction's own changes.
- `commit()` closes the innermost transaction. Its changes join the enclosing transaction, or
  become permanent if it was the outermost.
- `rollback()` undoes only the innermost transaction.
- `commit()` or `rollback()` with no open transaction raises `TransactionError`.
- `transaction()` returns a context manager: `begin` on entry, `commit` on a normal exit,
  `rollback` on an exception, which then propagates.
- Nothing from a transaction reaches the log before the outermost `commit`, and then it is written
  as **one record**, so it is all-or-nothing after a crash. An outermost commit with no changes
  writes nothing.

## 5. The log

The log is a UTF-8 text file with one record per line:

```
<crc> <json>\n
```

- `<json>` is a JSON object on one line: `{"ops": [...]}`.
- `<crc>` is the CRC-32 (`zlib.crc32`) of the UTF-8 bytes of `<json>`, as 8 lowercase hex digits.
- Each op is `["set", key, value, expires_at]`, where `expires_at` is a number or `null`,
  or `["del", key]`.

Every change outside a transaction is written as its own record, before the method returns.
Flush and `os.fsync` after every record. A `delete` of a missing key writes nothing.

## 6. Recovery

When a store opens an existing log:

- A bad **last** record (no final newline, wrong CRC, or unreadable) is what a crash during a write
  leaves behind. Drop it, truncate the file to the end of the last good record so later writes are
  clean, and carry on.
- A bad record with another record after it means real damage: raise `CorruptLogError` and leave
  the file untouched.
- An empty file is a valid empty log.

## 7. `compact()`

- Rewrites the log so it holds just the current state, without expired keys, and is replayed to
  exactly the same state. The log must not grow unboundedly with overwrites: after many overwrites
  of one key, the file must be much smaller after compacting.
- Write a new file in the same folder, then swap it in with `os.replace`. No extra files stay behind.
- In a transaction it raises `TransactionError`. For an in-memory store it does nothing.
- The store stays usable afterwards, and later writes go to the new log.

## 8. Command line: `python -m walkv <path> <command>`

| Command | Prints | Exit code |
|---|---|---|
| `set <key> <value>` (value stored as a string) | nothing | `0` |
| `get <key>` | the value, or nothing if missing | `0`, or `1` if missing |
| `del <key>` | nothing | `0`, or `1` if missing |
| `keys [prefix]` | one key per line | `0` |
| `compact` | nothing | `0` |

Wrong usage prints a message to **stderr** and exits with code `2`. A corrupt log prints a message
to **stderr** and exits with code `3`. For `get`, print a non-string value as JSON.

## Done when

- The package works as described above.
- The existing tests and your own tests pass.
- Your work is committed with Git.
