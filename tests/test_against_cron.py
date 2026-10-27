"""Every rule unscheduled encodes, asked of the cron on this machine.

These are the tests that can tell the difference between unscheduled being
right and unscheduled agreeing with its author. Each one puts the case to
cron and compares the answer with what unscheduled says about the same text.

They need sudo, unshare and cron, and skip themselves without them -- so CI
asserts separately that they did not skip.

What is NOT measured here, stated plainly because the README claims it: the
tool's "no such date" findings (Feb 30, Apr 31) are arithmetic. There is no
way to ask cron about a date that is not today, short of moving the clock,
and a probe that waits for February is not a test. What these tests do cover
is every rule that arithmetic rests on: which field forms cron accepts, what
they expand to, and whether the two day fields are ANDed or ORed.
"""

import datetime

import pytest

from unscheduled.analyse import analyse_crontab
from unscheduled.fields import FieldError, parse_time_fields
from unscheduled.parse import parse
from unscheduled.schedule import fires_at

from .cron_oracle import file_is_read, job_runs, line_is_kept, needs_cron

pytestmark = [needs_cron, pytest.mark.cron]


def unscheduled_keeps(line):
    """Does unscheduled think cron keeps a file containing this line?"""
    table = parse(line + "\n", system=True)
    return table.rejection is None


# --------------------------------------------------------------------------
# Which field forms cron accepts. A rejected line costs the whole file, so
# getting this wrong in either direction is a wrong answer about every other
# job in the file too.
# --------------------------------------------------------------------------

FIELD_FORMS = [
    "0 0 * * 7 root true",            # Sunday is 0 and 7
    "0 0 30 2 * root true",           # February 30: accepted, never fires
    "0 0 * * MON root true",
    "0 0 * * mon root true",
    "0 0 * JAN-DEC * root true",
    "0 0 * JAN-3 * root true",        # a name and a number in one range
    "0 0 * * SAT-SUN root true",      # accepted, and expands to nothing
    "5-1 * * * * root true",          # ditto
    "*/61 * * * * root true",         # step wider than the field
    "0-30/5 * * * * root true",
    "0 0 * * 1-5/2 root true",
    "*-5 * * * * root true",          # junk after a star, ignored
    "1-5-7 * * * * root true",        # junk after a range, ignored
    "*/2/3 * * * * root true",        # junk after a step, ignored
    "0-10/2/3 * * * * root true",
    "00 0 * * * root true",           # zero padded
    "0\t0\t*\t*\t*\troot\ttrue",      # tabs, as in every /etc/crontab
    "@daily root true",
    "@reboot root true",
    "@yearly root true",
    "0 0 * * 8 root true",            # out of range
    "0 0 0 * * root true",
    "0 0 32 * * root true",
    "60 0 * * * root true",
    "0 24 * * * root true",
    "0 0 * 0 * root true",
    "0 0 * 13 * root true",
    "JAN 0 * * * root true",          # a name in a numeric field
    "-1 0 * * * root true",
    "+5 0 * * * root true",
    "*/0 * * * * root true",          # a step of zero
    "0-30/0 * * * * root true",
    "5/10 * * * * root true",         # a step needs a range
    "0, 0 * * * root true",           # trailing comma
    ",0 0 * * * root true",
    "0, 30 * * * * root true",        # a space inside a list
    "0 0 * * MONDAY root true",       # names are exactly three letters
    "0 0 * JANUARY * root true",
    "0 0 * * MONxyz root true",
    "@bogus root true",
    "0 0 * * * root",                 # no command
    "0 0 * * root true",              # four time fields
    "0 0 * * * * root true",          # five fields and `*` for a username
    "0 0 L * * root true",            # not vixie syntax
    "0 0 ? * * root true",
]


@pytest.mark.parametrize("line", FIELD_FORMS)
def test_acceptance_matches_cron(tmp_path, line):
    assert unscheduled_keeps(line) == line_is_kept(tmp_path, line), line


# --------------------------------------------------------------------------
# What the accepted forms expand to. Asked by picking a schedule that either
# does or does not include this very minute and seeing whether cron runs it.
# --------------------------------------------------------------------------

def _now_fields(text):
    return parse_time_fields(text.split())


def _schedules_for_now():
    """Schedules built from the current minute, half of which should fire."""
    now = datetime.datetime.now()
    cron_dow = (now.weekday() + 1) % 7
    other_dow = (cron_dow + 3) % 7
    other_dom = 1 if now.day != 1 else 2
    return [
        "* * * * *",
        f"* * * * {cron_dow}",
        f"* * * * {other_dow}",
        # The reversed range, pinned to a day that matches: if cron wrapped
        # `2-1` round the week this would fire now. It does not.
        f"* * * * {cron_dow}-{(cron_dow - 1) % 7}",
        f"* * {now.day} * {other_dow}",      # OR: day-of-month side matches
        f"* * {other_dom} * {cron_dow}",     # OR: day-of-week side matches
        f"* * {other_dom} * {other_dow}",    # OR: neither side matches
        f"* * {other_dom} * *",              # AND, because day-of-week is `*`
        f"* * {now.day} * *",
        # `*/1` is starred too, so these two flip the rule to AND even though
        # the field they are in matches everything.
        f"* * */1 * {other_dow}",
        f"* * {other_dom} * */1",
        f"* * 1-31 * {other_dow}",           # not starred: back to OR
        f"{now.minute} * * * *",
        f"{(now.minute + 7) % 60} * * * *",
        f"* {now.hour} * * *",
        f"* {(now.hour + 5) % 24} * * *",
        f"* * * {now.month} *",
        f"* * * {(now.month % 12) + 1} *",
    ]


@pytest.mark.parametrize("schedule", _schedules_for_now())
def test_firing_this_minute_matches_cron(tmp_path, schedule):
    fields = _now_fields(schedule)
    before = datetime.datetime.now()
    ran, _ = job_runs(tmp_path, f"{schedule} root touch {{out}}/MARKER")
    after = datetime.datetime.now()
    predicted = fires_at(*fields, when=before)
    if predicted != fires_at(*fields, when=after):
        # The minute turned over while cron was being asked, so there is no
        # single right answer for this run. Skipping beats a flake.
        pytest.skip(f"{schedule} straddles a minute boundary")
    assert predicted == ran, f"{schedule}: unscheduled says {predicted}, cron did {ran}"


# --------------------------------------------------------------------------
# One bad line costs the file. This is the single most consequential claim
# unscheduled makes, so it is checked in both directions.
# --------------------------------------------------------------------------

def test_a_bad_line_below_a_good_one_takes_it_down(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    text = f"* * * * * root touch {out}/GOOD\n0 0 * 13 * root true\n"
    ran, made = job_runs(tmp_path, "", prelude=text)
    assert made == [], f"expected the file to be refused, cron ran {made}"

    findings, _ = analyse_crontab("probe", text)
    assert [f.kind for f in findings] == ["file-rejected"]
    assert findings[0].extra["jobs_lost"] == 1


def test_a_bad_line_above_a_good_one_takes_it_down(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    text = f"0 0 * 13 * root true\n* * * * * root touch {out}/GOOD\n"
    _, made = job_runs(tmp_path, "", prelude=text)
    assert made == [], f"expected the file to be refused, cron ran {made}"

    findings, _ = analyse_crontab("probe", text)
    assert findings[0].kind == "file-rejected"
    assert findings[0].lineno == 1


def test_a_file_that_does_not_end_in_a_newline_is_refused(tmp_path):
    assert line_is_kept(tmp_path, "0 0 * * * root true", terminate=False) is False
    table = parse("* * * * * root true\n0 0 * * * root true", system=True)
    assert table.rejection is not None
    assert "newline" in table.rejection.reason
    assert table.jobs_before_rejection == 1


def test_a_final_comment_without_a_newline_is_harmless(tmp_path):
    assert line_is_kept(tmp_path, "# trailing comment", terminate=False) is True
    table = parse("* * * * * root true\n# trailing comment", system=True)
    assert table.rejection is None
    assert len(table.jobs) == 1


# --------------------------------------------------------------------------
# Which files anything opens.
# --------------------------------------------------------------------------

NAMES = ["plain", "Job9_x-Y", "backup.sh", "jobs.conf", "jobs~", "jo+b"]


@pytest.mark.parametrize("name", NAMES)
def test_cron_d_naming_matches_cron(tmp_path, name):
    from unscheduled.files import name_rejected
    assert (name_rejected(name) is None) == file_is_read(tmp_path, name), name


MODES = ["644", "755", "664", "666", "600", "640"]


@pytest.mark.parametrize("mode", MODES)
def test_cron_d_modes_match_cron(tmp_path, mode):
    """The same file, judged by cron and by unscheduled's rule."""
    import subprocess

    from unscheduled.files import ownership_rejected

    read = file_is_read(tmp_path, "plain", mode=mode)

    sample = tmp_path / f"sample-{mode}"
    sample.write_text("# a crontab\n")
    subprocess.run(["sudo", "chown", "root:root", str(sample)], check=True)
    subprocess.run(["sudo", "chmod", mode, str(sample)], check=True)
    assert (ownership_rejected(str(sample)) is None) == read, \
        f"mode {mode}: cron read it {read}, unscheduled said " \
        f"{ownership_rejected(str(sample))!r}"


def test_cron_will_not_read_a_file_it_does_not_own(tmp_path):
    assert file_is_read(tmp_path, "plain", mode="644", owner="nobody:nogroup") is False


# --------------------------------------------------------------------------
# The command, after cron has had its way with it.
# --------------------------------------------------------------------------

def test_an_unescaped_percent_cuts_the_command(tmp_path):
    ran, made = job_runs(
        tmp_path, '* * * * * root sh -c "touch {out}/MARKER-%Y"')
    assert made == [], f"expected nothing to be created, got {made}"

    findings, _ = analyse_crontab(
        "probe", '* * * * * root sh -c "touch /tmp/x-%Y"\n')
    assert [f.kind for f in findings] == ["command-truncated"]


def test_an_escaped_percent_survives(tmp_path):
    ran, made = job_runs(
        tmp_path, '* * * * * root sh -c "date +\\%Y > {out}/YEAR"')
    assert made == ["YEAR"], made

    findings, _ = analyse_crontab(
        "probe", '* * * * * root sh -c "date +\\%Y > /tmp/x"\n')
    assert findings == []


def test_crons_default_path_is_what_unscheduled_assumes(tmp_path):
    from unscheduled.analyse import CRON_DEFAULT_PATH

    _, made = job_runs(
        tmp_path, '* * * * * root sh -c \'echo "$PATH" > {out}/PATH\'')
    assert made == ["PATH"]
    out = (tmp_path / "out" / "PATH").read_text().strip()
    assert out == CRON_DEFAULT_PATH, f"cron gave {out!r}"


def test_a_path_assignment_below_a_job_does_not_reach_it(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    below = (f"* * * * * root sh -c 'command -v probe-tool > {out}/FOUND'\n"
             "PATH=/usr/local/bin:/usr/bin:/bin\n")
    # Whatever is on this machine, the point is the assignment's reach: the
    # job sees the default PATH, so unscheduled must read it the same way.
    table = parse(below, system=True)
    assert table.jobs[0].env.get("PATH") is None

    above = ("PATH=/usr/local/bin:/usr/bin:/bin\n"
             f"* * * * * root sh -c 'command -v probe-tool > {out}/FOUND'\n")
    table = parse(above, system=True)
    assert table.jobs[0].env["PATH"] == "/usr/local/bin:/usr/bin:/bin"


def test_an_unknown_user_costs_the_file(tmp_path):
    assert line_is_kept(tmp_path, "* * * * * nosuchuser-xyzzy true") is False
    table = parse("* * * * * nosuchuser-xyzzy true\n", system=True)
    assert "not a user on this machine" in table.rejection.reason


def test_a_missing_user_field_costs_the_file(tmp_path):
    # The shape that bites: a user crontab line pasted into /etc/cron.d. The
    # command's first word is read as a username.
    assert line_is_kept(tmp_path, "* * * * * touch /tmp/whatever") is False
    table = parse("* * * * * touch /tmp/whatever\n", system=True)
    assert "'touch'" in table.rejection.reason


def test_reboot_does_not_fire_now(tmp_path):
    ran, made = job_runs(tmp_path, "@reboot root touch {out}/MARKER")
    assert made == [], "@reboot ran outside of boot"
    findings, _ = analyse_crontab("probe", "@reboot root true\n")
    assert [f.kind for f in findings] == ["needs-boot"]


def test_field_errors_are_raised_not_swallowed():
    with pytest.raises(FieldError):
        parse_time_fields("60 0 * * *".split())
