"""Can this schedule ever come round?

cron's matcher, from cron.c:

    minute && hour && month &&
        ( (DOM_STAR || DOW_STAR) ? (dom && dow) : (dom || dow) )

So day-of-month and day-of-week are ORed -- unless one of them was written
starting with a star, in which case they are ANDed. `0 0 13 * 5` is every
Friday and every 13th. `0 0 13 * */1` is only Friday the 13th, because the
star in `*/1` flips the whole rule. Both were measured; see
tests/test_against_cron.py.

The answer here is a straight yes or no, not a next-run time. Everything this
module is asked is of the form "is there any instant at all", and once there
is one there is one every year.
"""

import calendar

# The longest each month can be in some year. February is 29: a leap year is
# only four years away, which is not "never".
LONGEST = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
           7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def _day_of_month_possible(dom_values, month_values):
    """Is there a (month, day) pair in these sets that exists in some year?"""
    return any(day <= LONGEST[month] for month in month_values for day in dom_values)


def unreachable(minute, hour, dom, month, dow):
    """Why this schedule can never fire, or None if it can.

    Takes the five Fields. The reason is a short phrase for the report, in
    the voice of the field that is wrong.
    """
    for field in (minute, hour, month):
        if not field.values:
            return f"the {field.name} field {field.text!r} expands to nothing"

    # A (month, day) pair that exists in some year is reachable on every
    # weekday if you wait long enough -- every date falls on all seven
    # eventually, Feb 29 included -- so the day question reduces to the sets
    # being non-empty and the date being a real one.
    date_possible = _day_of_month_possible(dom.values, month.values)

    if dom.star or dow.star:
        # AND. A starred field still matches only the values it expands to,
        # so both sides have to be satisfiable at once.
        if not dom.values:
            return f"the day-of-month field {dom.text!r} expands to nothing"
        if not dow.values:
            return f"the day-of-week field {dow.text!r} expands to nothing"
        if not date_possible:
            return _impossible_date(dom, month)
        return None

    # OR. Either side firing is enough, so this is only unreachable when
    # neither side can ever match.
    if dow.values or date_possible:
        return None
    if not dom.values and not dow.values:
        return (f"both day fields expand to nothing: day-of-month {dom.text!r} "
                f"and day-of-week {dow.text!r}")
    if not dow.values:
        return (_impossible_date(dom, month) + " -- and the day-of-week field "
                f"{dow.text!r} expands to nothing, so the OR has no other side")
    return _impossible_date(dom, month)


def _impossible_date(dom, month):
    pairs = sorted((m, d) for m in month.values for d in dom.values)
    shown = ", ".join(f"{calendar.month_abbr[m]} {d}" for m, d in pairs[:4])
    if len(pairs) > 4:
        shown += f", and {len(pairs) - 4} more"
    return f"no such date: {shown}"


def fires_at(minute, hour, dom, month, dow, when):
    """Does this schedule fire in the minute `when` (a datetime)?

    This exists for the differential test: it is what lets a prediction be
    checked against what cron did in the same minute. The weekday mapping is
    cron's -- Sunday is both 0 and 7, and a crontab may have written either.
    """
    if when.minute not in minute.values or when.hour not in hour.values:
        return False
    if when.month not in month.values:
        return False
    weekday = (when.weekday() + 1) % 7  # Python Monday=0 -> cron Sunday=0
    dow_hit = weekday in dow.values or (weekday == 0 and 7 in dow.values)
    dom_hit = when.day in dom.values
    if dom.star or dow.star:
        return dom_hit and dow_hit
    return dom_hit or dow_hit
