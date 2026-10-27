"""Expanding the five time fields the way Debian's cron actually expands them.

Every rule in here was measured against cron 3.0pl1 rather than read off a
manual page, because two of them are not in one. The measurements are in
tests/test_against_cron.py and the harness that took them is in probe/.

The one that matters: a range whose first value is above its last -- `58-2`,
`SAT-SUN` -- is accepted, parses to completion, and expands to nothing. cron
does not wrap a range round the end of the field. Nothing is logged, the file
is kept, and the job can never fire.
"""

MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec"]
DAY_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

# name, low, high, names-and-the-value-the-first-name-means
SPECS = [
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day-of-month", 1, 31, None),
    ("month", 1, 12, (MONTH_NAMES, 1)),
    ("day-of-week", 0, 7, (DAY_NAMES, 0)),
]

# @reboot is left out on purpose: it has no time fields to expand. The parser
# handles it separately, because "fires once at boot" is not a schedule and
# pretending it expands to one would make every reachability answer wrong.
SPECIALS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


class FieldError(ValueError):
    """cron would refuse this field -- and with it the whole file."""


class Field:
    """One expanded field: the set of values, and whether it was written `*`.

    `star` is not "matches everything". cron sets its star flag from the
    field's first character, so `*/2` is starred too, and the flag is what
    decides whether day-of-month and day-of-week are ANDed or ORed. A field
    can therefore be starred and still match only half the days.
    """

    __slots__ = ("name", "low", "high", "values", "star", "text")

    def __init__(self, name, low, high, values, star, text):
        self.name = name
        self.low = low
        self.high = high
        self.values = values
        self.star = star
        self.text = text

    def __repr__(self):
        return f"Field({self.name}={self.text!r} -> {sorted(self.values)}, star={self.star})"


def _number(token, spec_names, low, high, field):
    """A field value: digits, or a three-letter name in the name fields."""
    if token.isdigit():
        value = int(token)
    elif spec_names is not None:
        names, base = spec_names
        # Exactly three letters. cron compares three characters and then
        # requires the token to end, so MONDAY and MONxyz are both refused.
        if len(token) != 3 or token.lower() not in names:
            raise FieldError(f"{field}: {token!r} is not a number or a three-letter name")
        value = names.index(token.lower()) + base
    else:
        raise FieldError(f"{field}: {token!r} is not a number")
    if not low <= value <= high:
        raise FieldError(f"{field}: {value} is outside {low}-{high}")
    return value


def parse_field(text, name, low, high, spec_names):
    """Expand one field, or raise FieldError if cron would refuse it.

    Returns a Field. An empty value set is not an error here: cron accepts a
    reversed range and expands it to nothing, which is the whole point of this
    tool, so that case has to come back as a successfully parsed empty field.
    """
    if text == "":
        raise FieldError(f"{name}: empty field")
    star = text[0] == "*"
    values = set()
    # Elements are comma separated with nothing between them. cron refuses a
    # trailing or leading comma and refuses a space inside the list (the space
    # ends the field, so the next element is read as the next field).
    for element in text.split(","):
        if element == "":
            raise FieldError(f"{name}: empty element in {text!r}")
        values |= _parse_element(element, name, low, high, spec_names)
    return Field(name, low, high, values, star, text)


def _split_step(body, name):
    """Split `range/step` -- and swallow whatever follows a second slash.

    cron stops reading after the step and does not complain about the rest, so
    `0-10/2/3` is every second minute to ten and `*/2/3` is every second
    minute. A step of zero is refused.
    """
    if "/" not in body:
        return body, 1
    head, _, tail = body.partition("/")
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        else:
            break  # junk after the step: cron ignores it
    if digits == "":
        raise FieldError(f"{name}: no number after / in {body!r}")
    step = int(digits)
    if step == 0:
        raise FieldError(f"{name}: a step of 0 is refused")
    return head, step


def _parse_element(element, name, low, high, spec_names):
    body, step = _split_step(element, name)

    if body.startswith("*"):
        # Anything after the star and before the step is ignored: `*-5` is
        # every value, not five of them.
        return set(range(low, high + 1, step))

    # A plain value. cron reads digits-or-name, then looks for `-`; anything
    # else ends the element. So `5/10` is refused (a step needs a range) but
    # `1-5-7` is 1 to 5 with the `-7` ignored.
    first_token, dash, rest = body.partition("-")
    if dash == "":
        if step != 1:
            raise FieldError(f"{name}: a step needs a range, not {element!r}")
        return {_number(first_token, spec_names, low, high, name)}

    first = _number(first_token, spec_names, low, high, name)
    # The second value runs to the next non-value character; the rest is junk
    # cron does not read.
    second_token = ""
    for char in rest:
        if char.isdigit() or char.isalpha():
            second_token += char
        else:
            break
    last = _number(second_token, spec_names, low, high, name)

    # The measured one. No wrap-around, no error, no values.
    if first > last:
        return set()
    return set(range(first, last + 1, step))


def parse_time_fields(fields):
    """Expand the five time fields of a job line. Raises FieldError."""
    if len(fields) != 5:
        raise FieldError(f"{len(fields)} time fields, not 5")
    out = []
    for text, (name, low, high, spec_names) in zip(fields, SPECS):
        out.append(parse_field(text, name, low, high, spec_names))
    return out
