"""The command line, the exit codes, and the shapes of finding nobody should
have to run cron to check.
"""

import json
import os

import pytest

from unscheduled.analyse import analyse_crontab, audit_paths, resolvable
from unscheduled.cli import main
from unscheduled.report import counts, summary

EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "examples")


def kinds(text, **kwargs):
    findings, _ = analyse_crontab("probe", text, **kwargs)
    return [f.kind for f in findings]


def test_a_clean_crontab_is_quiet_and_exits_zero(tmp_path, capsys):
    path = tmp_path / "jobs"
    path.write_text("0 3 * * * root /bin/sh -c true\n")
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Every job unscheduled can check will run" in out


def test_findings_exit_one(tmp_path, capsys):
    path = tmp_path / "jobs"
    path.write_text("0 3 30 2 * root /bin/true\n")
    assert main([str(path)]) == 1
    assert "never-fires" in capsys.readouterr().out


def test_a_missing_path_exits_two_and_prints_nothing_to_stdout(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no such file" in captured.err


def test_json_carries_the_counts_and_the_findings(tmp_path, capsys):
    path = tmp_path / "jobs"
    path.write_text("0 3 30 2 * root /bin/true\n@reboot root /bin/true\n")
    assert main([str(path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["broken_jobs"] == 1
    assert payload["counts"]["notes"] == 1
    assert [f["kind"] for f in payload["findings"]] == ["never-fires", "needs-boot"]
    assert payload["findings"][0]["line"] == 1


def test_the_examples_show_every_kind(capsys):
    assert main([os.path.join(EXAMPLES, "cron.d"),
                 os.path.join(EXAMPLES, "cron.daily")]) == 1
    out = capsys.readouterr().out
    for kind in ("never-fires", "file-rejected", "file-not-read",
                 "command-truncated", "command-not-found", "day-fields-ored",
                 "needs-boot"):
        assert kind in out, kind


def test_a_copy_of_cron_d_is_not_judged_on_its_ownership(tmp_path):
    """Nothing in a git checkout is owned by root, and that is not a finding."""
    crond = tmp_path / "cron.d"
    crond.mkdir()
    (crond / "jobs").write_text("0 3 * * * root /bin/sh -c true\n")
    audit = audit_paths([str(crond)])
    assert audit.dead == []
    assert audit.files_read == 1


def test_a_user_crontab_read_as_a_system_one_says_so():
    findings, _ = analyse_crontab("spool/alice", "0 3 * * * /bin/true\n")
    assert findings[0].kind == "file-rejected"
    assert "pass --user" in findings[0].detail

    findings, _ = analyse_crontab("spool/alice", "0 3 * * * /bin/true\n", system=False)
    assert findings == []


def test_the_user_hint_is_not_offered_when_it_would_not_help():
    # A genuine unknown user: parsing as a user crontab does not fix it, so
    # suggesting --user would be a wrong guess dressed up as help.
    findings, _ = analyse_crontab("probe", "0 3 * * * nosuchuser-xyzzy\n")
    assert "pass --user" not in findings[0].detail


def test_a_never_firing_job_is_reported_once():
    # The command is missing too. One problem, one line, one finding.
    assert kinds("0 0 30 2 * root definitely-not-on-the-path\n") == ["never-fires"]


@pytest.mark.parametrize("command,path,expected", [
    ("/bin/sh -c true", "/usr/bin:/bin", True),
    ("/nowhere/at/all/binary", "/usr/bin:/bin", False),
    ("sh -c true", "/usr/bin:/bin", True),
    ("sh", "/nowhere", False),
    ("cd /tmp && true", "/nowhere", None),
    ("FOO=1 mytool", "/nowhere", None),
    ("$HOME/bin/tool", "/nowhere", None),
    ("test -x /srv/app", "/nowhere", None),
    ("echo hello", "/nowhere", None),
    # The pipe is in the second word. The shell still has to find the first
    # one, and it still cannot, so this is an answer rather than a shrug.
    ("tool | other", "/nowhere", False),
])
def test_resolvable_only_answers_when_it_can(command, path, expected):
    assert resolvable(command, path) is expected


def test_a_path_assignment_above_the_job_is_used_for_resolution(tmp_path):
    tool = tmp_path / "mytool"
    tool.write_text("#!/bin/sh\n")
    os.chmod(tool, 0o755)
    assert kinds("*/5 * * * * root mytool\n") == ["command-not-found"]
    assert kinds(f"PATH={tmp_path}\n*/5 * * * * root mytool\n") == []
    # Below the job, it reaches nothing.
    assert kinds(f"*/5 * * * * root mytool\nPATH={tmp_path}\n") == ["command-not-found"]


def test_the_summary_counts_jobs_not_findings(tmp_path):
    crond = tmp_path / "cron.d"
    crond.mkdir()
    (crond / "both").write_text(
        "0 0 30 2 * root /bin/true\n0 0 31 4 * root /bin/true\n")
    audit = audit_paths([str(crond)])
    assert counts(audit)["broken_jobs"] == 2
    assert "2 jobs that never run as written" in summary(audit)


def test_reading_nothing_is_not_a_clean_result(tmp_path, capsys, monkeypatch):
    unreadable = tmp_path / "locked"
    unreadable.mkdir()
    (unreadable / "jobs").write_text("0 3 * * * root /bin/true\n")
    os.chmod(unreadable, 0o000)
    try:
        assert main([str(unreadable)]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "not a clean result" in captured.err
    finally:
        os.chmod(unreadable, 0o755)
