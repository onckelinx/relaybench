import unittest

from walkv import KVStore


class TestStore(unittest.TestCase):
    def test_set_and_get(self):
        store = KVStore()
        store.set("a", "one")
        self.assertEqual(store.get("a"), "one")

    def test_missing_key_gives_default(self):
        store = KVStore()
        self.assertIsNone(store.get("nope"))
        self.assertEqual(store.get("nope", "fallback"), "fallback")

    def test_delete(self):
        store = KVStore()
        store.set("a", "one")
        self.assertTrue(store.delete("a"))
        self.assertFalse(store.delete("a"))
        self.assertNotIn("a", store)

    def test_keys_with_prefix(self):
        store = KVStore()
        for key in ("b:1", "a:2", "a:1"):
            store.set(key, "x")
        self.assertEqual(store.keys("a:"), ["a:1", "a:2"])
        self.assertEqual(len(store), 3)

    def test_bad_key(self):
        store = KVStore()
        with self.assertRaises(ValueError):
            store.set("", "x")


if __name__ == "__main__":
    unittest.main()
