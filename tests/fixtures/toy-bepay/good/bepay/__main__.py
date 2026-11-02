import sys

from .validators import is_valid_be_iban, is_valid_ogm


def main():
    if len(sys.argv) != 2:
        print("usage: python -m bepay <value>", file=sys.stderr)
        return 2
    value = sys.argv[1]
    if is_valid_ogm(value):
        print("valid OGM")
        return 0
    if is_valid_be_iban(value):
        print("valid IBAN")
        return 0
    print("invalid")
    return 1


sys.exit(main())
