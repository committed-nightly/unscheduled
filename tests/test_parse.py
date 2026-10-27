"""Parsing, on cases that do not need cron to be installed to be worth having."""

import pytest

from unscheduled.fields import FieldError, parse_field
from unscheduled.parse import parse, split_percent

USERS = {"root", "www-data", "backup"}


def tab(text):
    return parse(text, system=True, user_exists=USERS.__contains__)


def user(text):
    return parse(text, system=False, user_exists=USERS.__contains__)


def test_a_system_crontab_wants_a_username():
    table = tab("0 3 * * * root /usr/bin/backup\n")
    assert table.rejection is None
    assert table.jobs[0].user == "root"
    assert table.jobs[0].command == "/usr/bin/backup"


def test_tabs_separate_fields_as_well_as_spaces():
    table = tab("17 *\t* * *\troot\tcd / && run-parts --report /etc/cron.hourly\n")
    assert table.rejection is None
    assert table.jobs[0].user == "root"
    assert table.jobs[0].command.startswith("cd / &&")


def test_a_user_crontab_has_no_username():
    table = user("0 3 * * * /usr/bin/backup\n")
    assert table.rejection is None
    assert table.jobs[0].user is None
    assert table.jobs[0].command == "/usr/bin/backup"


def test_a_user_crontab_line_in_a_system_crontab_is_refused():
    table = tab("0 3 * * * /usr/bin/backup\n")
    assert "'/usr/bin/backup' is not a user" in table.rejection.reason


def test_environment_assignments_reach_forwards_only():
    table = tab("MAILTO=ops\n0 1 * * * root a\nPATH=/opt/bin\n0 2 * * * root b\n")
    assert table.jobs[0].env == {"MAILTO": "ops"}
    assert table.jobs[1].env == {"MAILTO": "ops", "PATH": "/opt/bin"}


def test_one_layer_of_quotes_comes_off_an_assignment():
    table = tab('MAILTO=""\nSHELL="/bin/bash"\n0 1 * * * root a\n')
    assert table.jobs[0].env == {"MAILTO": "", "SHELL": "/bin/bash"}


def test_the_rejected_line_is_reported_with_its_number_and_what_was_lost():
    table = tab("# head\n0 1 * * * root a\n\n0 99 * * * root b\n0 2 * * * root c\n")
    assert table.rejection.lineno == 4
    assert table.jobs_before_rejection == 1
    assert table.jobs == []


def test_specials_expand_to_the_schedule_they_name():
    assert tab("@daily root a\n").jobs[0].schedule == "0 0 * * *"
    assert tab("@weekly root a\n").jobs[0].schedule == "0 0 * * 0"
    assert tab("@reboot root a\n").jobs[0].reboot is True
    assert tab("@MIDNIGHT root a\n").jobs[0].schedule == "0 0 * * *"


def test_comments_and_blank_lines_are_not_jobs():
    table = tab("\n# a comment\n   \n\t# indented\n0 1 * * * root a\n")
    assert len(table.jobs) == 1


@pytest.mark.parametrize("command,runs,stdin", [
    ("date +%Y", "date +", "Y"),
    (r"date +\%Y", "date +%Y", None),
    ("mail -s x me%body line%second", "mail -s x me", "body line%second"),
    ("echo hello", "echo hello", None),
    ("%", "", ""),
    (r"\%%", "%", ""),
])
def test_split_percent(command, runs, stdin):
    assert split_percent(command) == (runs, stdin)


@pytest.mark.parametrize("text,expected", [
    ("*", set(range(0, 60))),
    ("*/15", {0, 15, 30, 45}),
    ("5", {5}),
    ("5-8", {5, 6, 7, 8}),
    ("5-8/2", {5, 7}),
    ("1,2,3", {1, 2, 3}),
    ("8-5", set()),
    ("*/61", {0}),
    ("*-5", set(range(0, 60))),
    ("1-5-7", {1, 2, 3, 4, 5}),
    ("*/2/3", set(range(0, 60, 2))),
])
def test_minute_field_expansion(text, expected):
    assert parse_field(text, "minute", 0, 59, None).values == expected


@pytest.mark.parametrize("text", ["60", "-1", "+5", "*/0", "5/10", "0,", ",0", "", "x"])
def test_minute_field_refusals(text):
    with pytest.raises(FieldError):
        parse_field(text, "minute", 0, 59, None)


def test_star_is_about_the_first_character_not_the_values():
    assert parse_field("*/1", "day-of-month", 1, 31, None).star is True
    assert parse_field("1-31", "day-of-month", 1, 31, None).star is False
    assert parse_field("1-31", "day-of-month", 1, 31, None).values == \
        parse_field("*", "day-of-month", 1, 31, None).values
