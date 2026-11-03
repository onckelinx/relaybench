"""Hidden acceptance tests for the bepay toy task.

The builder models never see this file. pipeline.py runs it against each build
folder by setting the BUILD_DIR environment variable.
"""
import os
import subprocess
import sys
import unittest

BUILD_DIR = os.environ.get("BUILD_DIR", "")
if BUILD_DIR:
    sys.path.insert(0, BUILD_DIR)

try:
    import bepay  # noqa: E402
    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - reported as a failing test
    bepay = None
    IMPORT_ERROR = exc


def _fn(name):
    if bepay is None:
        raise AssertionError("could not import bepay: %r" % (IMPORT_ERROR,))
    fn = getattr(bepay, name, None)
    if fn is None:
        raise AssertionError("bepay does not export %s" % name)
    return fn


class TestImport(unittest.TestCase):
    def test_package_imports(self):
        self.assertIsNone(IMPORT_ERROR, "bepay failed to import: %r" % (IMPORT_ERROR,))

    def test_exports_all_functions(self):
        for name in ("is_valid_ogm", "make_ogm", "is_valid_be_iban", "format_be_iban"):
            _fn(name)


class TestOgmValidation(unittest.TestCase):
    def test_plus_format(self):
        self.assertTrue(_fn("is_valid_ogm")("+++123/4567/89002+++"))

    def test_star_format(self):
        self.assertTrue(_fn("is_valid_ogm")("***123/4567/89002***"))

    def test_plain_digits(self):
        self.assertTrue(_fn("is_valid_ogm")("123456789002"))

    def test_surrounding_whitespace(self):
        self.assertTrue(_fn("is_valid_ogm")("  +++123/4567/89002+++\n"))

    def test_check_97_when_remainder_zero(self):
        f = _fn("is_valid_ogm")
        self.assertTrue(f("+++000/0000/09797+++"))
        self.assertTrue(f("+++000/0000/00097+++"))

    def test_check_00_is_never_valid(self):
        self.assertFalse(_fn("is_valid_ogm")("+++000/0000/00000+++"))

    def test_wrong_check_number(self):
        self.assertFalse(_fn("is_valid_ogm")("+++123/4567/89003+++"))

    def test_mixed_delimiters(self):
        self.assertFalse(_fn("is_valid_ogm")("+++123/4567/89002***"))

    def test_wrong_grouping(self):
        self.assertFalse(_fn("is_valid_ogm")("+++1234/567/89002+++"))

    def test_wrong_length(self):
        f = _fn("is_valid_ogm")
        self.assertFalse(f("12345678900"))
        self.assertFalse(f("1234567890022"))

    def test_non_string_returns_false(self):
        f = _fn("is_valid_ogm")
        self.assertFalse(f(123456789002))
        self.assertFalse(f(None))

    def test_non_ascii_digits_are_invalid(self):
        # str.isdigit() accepts these, and int() then raises on the superscripts.
        f = _fn("is_valid_ogm")
        for bad in ("\u00b2" * 12, "+++\u00b2\u00b2\u00b2/\u00b2\u00b2\u00b2\u00b2/\u00b2\u00b2\u00b2\u00b2\u00b2+++",
                    "\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u0660\u0660\u0662",
                    # int() reads these digits, so only a format check that means 0-9 can refuse them.
                    "+++\u0661\u0662\u0663/\u0664\u0665\u0666\u0667/\u0668\u0669\u0660\u0660\u0662+++",
                    "\u0661\u0662\u0663456789002", "+++\u0661\u0662\u0663/4567/89002+++"):
            self.assertFalse(f(bad), ascii(bad))


class TestMakeOgm(unittest.TestCase):
    def test_zero_padding(self):
        self.assertEqual(_fn("make_ogm")(123456789), "+++012/3456/78939+++")

    def test_lower_bound(self):
        self.assertEqual(_fn("make_ogm")(0), "+++000/0000/00097+++")

    def test_upper_bound(self):
        self.assertEqual(_fn("make_ogm")(9999999999), "+++999/9999/99948+++")

    def test_round_trip(self):
        self.assertTrue(_fn("is_valid_ogm")(_fn("make_ogm")(1234567890)))

    def test_out_of_range(self):
        f = _fn("make_ogm")
        for bad in (-1, 10000000000):
            with self.assertRaises(ValueError):
                f(bad)

    def test_wrong_types(self):
        f = _fn("make_ogm")
        for bad in (True, 1.0, "123"):
            with self.assertRaises(ValueError):
                f(bad)


class TestIbanValidation(unittest.TestCase):
    def test_compact(self):
        self.assertTrue(_fn("is_valid_be_iban")("BE68539007547034"))

    def test_spaces_and_lowercase(self):
        self.assertTrue(_fn("is_valid_be_iban")("be68 5390 0754 7034"))

    def test_national_check_97(self):
        self.assertTrue(_fn("is_valid_be_iban")("BE54119753076697"))

    def test_another_valid(self):
        self.assertTrue(_fn("is_valid_be_iban")("BE55123000456744"))

    def test_wrong_iban_check_digits(self):
        self.assertFalse(_fn("is_valid_be_iban")("BE69539007547034"))

    def test_iban_ok_but_national_check_wrong(self):
        for iban in ("BE41539007547035", "BE58 8288 3607 5983"):
            self.assertFalse(_fn("is_valid_be_iban")(iban), iban)

    def test_other_country(self):
        self.assertFalse(_fn("is_valid_be_iban")("NL91ABNA0417164300"))

    def test_wrong_length(self):
        self.assertFalse(_fn("is_valid_be_iban")("BE6853900754703"))

    def test_letters_in_account(self):
        self.assertFalse(_fn("is_valid_be_iban")("BE68 5390 0754 703A"))

    def test_non_string_and_empty(self):
        f = _fn("is_valid_be_iban")
        self.assertFalse(f(""))
        self.assertFalse(f(None))

    def test_non_ascii_digits_are_invalid(self):
        f = _fn("is_valid_be_iban")
        for bad in ("BE" + "\u00b2" * 14, "BE\u0666\u0668" + "539007547034"):
            self.assertFalse(f(bad), ascii(bad))

    def test_trailing_newline_never_raises(self):
        # Whether a newline counts as a space is open to reading. Raising is not.
        self.assertIn(_fn("is_valid_be_iban")("BE68539007547034\n"), (True, False))


class TestFormatIban(unittest.TestCase):
    def test_format(self):
        self.assertEqual(_fn("format_be_iban")("be68539007547034"), "BE68 5390 0754 7034")

    def test_format_already_spaced(self):
        self.assertEqual(_fn("format_be_iban")("BE68  5390 0754 7034"), "BE68 5390 0754 7034")

    def test_format_invalid_raises(self):
        with self.assertRaises(ValueError):
            _fn("format_be_iban")("BE69539007547034")

    def test_format_non_ascii_digits_raises(self):
        with self.assertRaises(ValueError):
            _fn("format_be_iban")("BE\u0666\u0668" + "539007547034")


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "bepay"] + list(args),
            cwd=BUILD_DIR or None,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_valid_ogm(self):
        r = self._run("+++123/4567/89002+++")
        self.assertEqual((r.stdout.strip(), r.returncode), ("valid OGM", 0))

    def test_valid_iban_with_spaces(self):
        r = self._run("BE68 5390 0754 7034")
        self.assertEqual((r.stdout.strip(), r.returncode), ("valid IBAN", 0))

    def test_invalid(self):
        r = self._run("hello")
        self.assertEqual((r.stdout.strip(), r.returncode), ("invalid", 1))

    def test_non_ascii_digits_print_invalid(self):
        r = self._run("\u00b2" * 12)
        self.assertEqual((r.stdout.strip(), r.returncode), ("invalid", 1))

    def test_no_argument(self):
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.strip(), "usage message should go to stderr")


if __name__ == "__main__":
    unittest.main()
