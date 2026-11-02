"""An in-memory key-value store."""


class KVStore:
    def __init__(self):
        self._data = {}

    @staticmethod
    def _check_key(key):
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")

    def set(self, key, value):
        self._check_key(key)
        self._data[key] = value

    def get(self, key, default=None):
        self._check_key(key)
        return self._data.get(key) or default

    def delete(self, key):
        """Remove a key. Returns True if it existed."""
        self._check_key(key)
        if key in self._data:
            del self._data[key]
            return True
        return False

    def keys(self, prefix=""):
        """All keys that start with prefix, sorted."""
        return sorted(k for k in self._data if k.startswith(prefix))

    def __contains__(self, key):
        return key in self._data

    def __len__(self):
        return len(self._data)
