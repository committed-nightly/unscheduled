"""The reachability answer, checked against brute force.

unreachable() is a handful of set operations. The thing it is standing in for
is "walk every date and see if any of them match", which is obviously right
and far too slow to run on every job. So the test is the slow version: for a
few thousand field combinations, walk four centuries of dates and check the
fast answer against what the walk found.

The window is 28 years, which is how long the calendar takes to repeat while
no century boundary intervenes: every (month, day) pair reaches all seven
weekdays inside it, February 29 included. A shorter window makes the brute
force wrong rather than slow -- at four years it reported `* * */33 5 2,7` as
unreachable, when all that schedule needs is a May 1 that falls on a Tuesday,
and the next one is in 2028.
"""

import datetime
import random

import pytest

from unscheduled.fields import parse_field, parse_time_fields
from unscheduled.schedule import fires_at, unreachable

ALWAYS = "* * * * *"


def fields(text):
    return parse_time_fields(text.split())


def brute_force_fires(text, years=28):
    """Does this schedule fire on any day in `years` years from 2024?

    Days, not minutes: the minute and hour fields are independent of the
    date, so a non-empty minute set and a non-empty hour set is all they
    need, and checking 525,600 minutes per year would make this unusable.
    """
    minute, hour, dom, month, dow = fields(text)
    if not minute.values or not hour.values:
        return False
    one_minute, one_hour = min(minute.values), min(hour.values)
    day = datetime.date(2024, 1, 1)
    end = datetime.date(2024 + years, 1, 1)
    while day < end:
        when = datetime.datetime(day.year, day.month, day.day, one_hour, one_minute)
        if fires_at(minute, hour, dom, month, dow, when):
            return True
        day += datetime.timedelta(days=1)
    return False


def test_the_obvious_ones():
    assert unreachable(*fields(ALWAYS)) is None
    assert unreachable(*fields("0 0 29 2 *")) is None       # a leap year will come
    assert unreachable(*fields("0 0 30 2 *")) is not None
    assert unreachable(*fields("0 0 31 4 *")) is not None
    assert unreachable(*fields("0 0 31 2,4,6,9,11 *")) is not None
    assert unreachable(*fields("0 0 31 2,4,7 *")) is None   # July has a 31st


def test_a_reversed_range_is_unreachable_and_says_which_field():
    reason = unreachable(*fields("0 0 * * SAT-SUN"))
    assert reason is not None
    assert "day-of-week" in reason and "SAT-SUN" in reason

    reason = unreachable(*fields("58-2 * * * *"))
    assert "minute" in reason and "58-2" in reason


def test_the_day_fields_are_ored_unless_one_is_starred():
    # Friday the 13th, written the way people write it, is not Friday the
    # 13th: an impossible day-of-month still fires on the weekday.
    assert unreachable(*fields("0 0 30 2 5")) is None
    # Star the day-of-week and the OR becomes an AND, so now it cannot fire.
    assert unreachable(*fields("0 0 30 2 *")) is not None
    # `*/1` matches every weekday, but it starts with a star, so it ANDs too.
    assert unreachable(*fields("0 0 30 2 */1")) is not None


def test_an_impossible_date_names_the_dates_it_checked():
    reason = unreachable(*fields("0 0 31 2 *"))
    assert "Feb 31" in reason


def test_a_step_wider_than_the_field_keeps_only_the_first_value():
    field = parse_field("*/61", "minute", 0, 59, None)
    assert field.values == {0}
    assert field.star is True


# --------------------------------------------------------------------------
# The cross-check.
# --------------------------------------------------------------------------

def _random_field(low, high, rng, allow_reversed=True):
    kind = rng.choice(["star", "star-step", "single", "range", "range-step", "list"])
    if kind == "star":
        return "*"
    if kind == "star-step":
        return f"*/{rng.randint(1, high + 2)}"
    if kind == "single":
        return str(rng.randint(low, high))
    if kind in ("range", "range-step"):
        first, last = rng.randint(low, high), rng.randint(low, high)
        if not allow_reversed and first > last:
            first, last = last, first
        body = f"{first}-{last}"
        return body if kind == "range" else f"{body}/{rng.randint(1, 5)}"
    return ",".join(str(rng.randint(low, high)) for _ in range(rng.randint(2, 4)))


def _random_schedule(rng):
    return " ".join([
        _random_field(0, 59, rng),
        _random_field(0, 23, rng),
        _random_field(1, 31, rng),
        _random_field(1, 12, rng),
        _random_field(0, 7, rng),
    ])


@pytest.mark.parametrize("seed", range(12))
def test_unreachable_agrees_with_walking_the_calendar(seed):
    """Twenty-five schedules per seed, three hundred in total."""
    rng = random.Random(seed)
    for _ in range(25):
        text = _random_schedule(rng)
        fast = unreachable(*fields(text)) is None
        slow = brute_force_fires(text)
        assert fast == slow, f"{text}: unreachable said {not fast}, walk said {slow}"
