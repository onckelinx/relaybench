import re

_OGM = re.compile(r"\+\+\+([0-9]{3})/([0-9]{4})/([0-9]{5})\+\+\+|\*\*\*([0-9]{3})/([0-9]{4})/([0-9]{5})\*\*\*|([0-9]{12})")


def _check(base):
    return base % 97 or 97


def is_valid_ogm(text):
    if not isinstance(text, str):
        return False
    match = _OGM.fullmatch(text.strip())
    if not match:
        return False
    digits = "".join(g for g in match.groups() if g)
    return _check(int(digits[:10])) == int(digits[10:])


def make_ogm(base):
    if type(base) is not int or not 0 <= base <= 9999999999:
        raise ValueError("base must be an int from 0 to 9999999999")
    digits = "%010d%02d" % (base, _check(base))
    return "+++%s/%s/%s+++" % (digits[:3], digits[3:7], digits[7:])


def is_valid_be_iban(text):
    if not isinstance(text, str):
        return False
    iban = text.replace(" ", "").upper()
    if not re.fullmatch(r"BE[0-9]{14}", iban):
        return False
    numeric = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    if int(numeric) % 97 != 1:
        return False
    account = iban[4:]
    return _check(int(account[:10])) == int(account[10:])


def format_be_iban(text):
    if not is_valid_be_iban(text):
        raise ValueError("not a valid Belgian IBAN")
    iban = text.replace(" ", "").upper()
    return " ".join(iban[i:i + 4] for i in range(0, 16, 4))
