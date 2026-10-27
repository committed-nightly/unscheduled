"""Findings: the jobs that never run, and why.

Each kind is a different answer to "nothing happened at 3am":

  file-not-read       nothing ever opened the file
  file-rejected       cron opened it, refused one line, and dropped them all
  never-fires         the line is fine and the schedule can never come round
  command-truncated   an unescaped % cut the command short
  command-not-found   the job fires and the shell cannot find the program

And two notes, which are not findings because the job does run:

  needs-boot          @reboot, which fires at boot and at no other time
  day-fields-ored     day-of-month and day-of-week both restricted, so cron
                      ORs them and the job runs on more days than written
"""

import os
import shutil

from .files import RUN_PARTS_DIRS, cron_d_rejected, run_parts_rejected
from .parse import ENV_ASSIGNMENT, parse, split_percent
from .schedule import unreachable

# cron's own default, from its source and confirmed by asking a job what it
# got: not the PATH of whoever edited the crontab, and no /usr/local/bin.
CRON_DEFAULT_PATH = "/usr/bin:/bin"

# Things sh runs without consulting PATH. Reporting one of these as missing
# would be the most annoying thing this tool could do, so the list is
# deliberately generous: a missed finding costs less than a false one.
SHELL_BUILTINS = frozenset("""
: . [ alias bg break cd command continue echo eval exec exit export false fc fg
getopts hash jobs kill let local printf pwd read readonly return set shift
source test times trap true type ulimit umask unalias unset wait
if then else elif fi for while until do done case esac function time
""".split())

DEAD = "dead"
NOTE = "note"


class Finding:
    def __init__(self, kind, severity, path, lineno, detail, extra=None):
        self.kind = kind
        self.severity = severity
        self.path = path
        self.lineno = lineno
        self.detail = detail
        self.extra = extra or {}

    @property
    def where(self):
        return f"{self.path}:{self.lineno}" if self.lineno else self.path

    def as_dict(self):
        out = {"kind": self.kind, "severity": self.severity, "file": self.path,
               "detail": self.detail}
        if self.lineno:
            out["line"] = self.lineno
        out.update(self.extra)
        return out


def resolvable(command, path_value):
    """Can the shell find the first word of this command under this PATH?

    True yes, False no, None don't know -- and don't know is the answer for
    anything with shell syntax in it, because resolving a pipeline or a
    variable properly means being a shell.
    """
    stripped = command.strip()
    if not stripped:
        return None
    first = stripped.split()[0]
    if first in SHELL_BUILTINS:
        return None
    if any(char in first for char in "'\"$`(){}|&;<>*?=!~"):
        return None
    if first.startswith("-"):
        return None
    if "/" in first:
        return os.access(first, os.X_OK)
    return shutil.which(first, path=path_value) is not None


def _looks_like_a_command(word):
    """Is this word more plausibly a program than a username?"""
    if "/" in word:
        return True
    return word in SHELL_BUILTINS or shutil.which(word, path=CRON_DEFAULT_PATH) is not None


def _command_findings(path, job):
    """Findings about the command, after cron has had its way with it."""
    out = []
    runs, stdin = split_percent(job.command)
    if stdin is not None:
        out.append(Finding(
            "command-truncated", DEAD, path, job.lineno,
            f"an unescaped % ends the command: cron runs {runs.strip()!r} and feeds "
            f"it {stdin!r} on standard input. Write \\% for a literal percent.",
            {"runs": runs.strip(), "stdin": stdin},
        ))

    path_value = job.env.get("PATH", CRON_DEFAULT_PATH)
    words = runs.strip().split()
    if words and resolvable(runs, path_value) is False:
        first = words[0]
        where = ("an absolute path with no executable there" if "/" in first
                 else f"not on cron's PATH, which here is {path_value}")
        out.append(Finding(
            "command-not-found", DEAD, path, job.lineno,
            f"{first!r} is {where} -- the job fires on time and the shell finds "
            "nothing to run",
            {"command": first, "path": path_value},
        ))
    return out


def _schedule_findings(path, job):
    if job.reboot:
        return [Finding(
            "needs-boot", NOTE, path, job.lineno,
            "@reboot fires when cron starts and at no other time -- so never in "
            "an image where nothing starts cron",
        )]

    minute, hour, dom, month, dow = job.fields
    reason = unreachable(minute, hour, dom, month, dow)
    if reason:
        return [Finding(
            "never-fires", DEAD, path, job.lineno,
            f"{reason} -- the schedule is {job.schedule!r}",
            {"schedule": job.schedule},
        )]

    if not dom.star and not dow.star:
        return [Finding(
            "day-fields-ored", NOTE, path, job.lineno,
            f"day-of-month {dom.text!r} and day-of-week {dow.text!r} are both set, "
            "and cron ORs them: this runs on every matching day of the month and "
            "on every matching weekday, not only on the days that satisfy both",
        )]
    return []


def analyse_crontab(path, text, system=True, user_exists=None):
    """Findings for one crontab's contents, and the jobs cron would keep.

    Returns (findings, jobs). On a rejected file there are no jobs: that is
    the finding.
    """
    kwargs = {} if user_exists is None else {"user_exists": user_exists}
    table = parse(text, path=path, system=system, **kwargs)
    if table.rejection:
        lost = table.jobs_before_rejection
        tail = ""
        if lost:
            tail = (f" -- and it takes the {lost} job{'s' if lost != 1 else ''} cron "
                    "had already read in this file with it")
        # A user crontab read as a system one fails on the username, because
        # the first word of the command is sitting where the username goes.
        # Only say so when the word actually looks like a command: nearly
        # every mistyped username also parses as a user crontab, and
        # suggesting --user for all of them would make the hint worthless.
        suspect = table.rejection.user_field
        if system and suspect and _looks_like_a_command(suspect):
            if parse(text, path=path, system=False, **kwargs).rejection is None:
                tail += (". This file parses cleanly as a user crontab, which has no "
                         "username field -- if that is what it is, pass --user")
        return [Finding(
            "file-rejected", DEAD, path, table.rejection.lineno,
            f"cron refuses the whole file over this line: {table.rejection.reason}{tail}",
            {"jobs_lost": lost, "line_text": table.rejection.line},
        )], []

    findings = []
    for job in table.jobs:
        schedule = _schedule_findings(path, job)
        findings.extend(schedule)
        # A job that can never fire is not also a job with a missing program.
        # Saying both would be true and would read as two problems.
        if any(f.kind == "never-fires" for f in schedule):
            continue
        findings.extend(_command_findings(path, job))
    return findings, table.jobs


class Audit:
    """What one run looked at, and what it found."""

    def __init__(self):
        self.findings = []
        self.jobs = 0
        self.files_read = 0
        self.declined = []

    @property
    def dead(self):
        return [f for f in self.findings if f.severity == DEAD]

    @property
    def notes(self):
        return [f for f in self.findings if f.severity == NOTE]


def _read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        return handle.read()


def is_system_crontab(path):
    """A user crontab has no user field. Files in a spool directory are those."""
    return "cron/crontabs" not in os.path.dirname(path).replace("\\", "/")


def is_run_parts_dir(path):
    """Is this a directory of scripts rather than a directory of crontabs?

    Matched on the name, not only on the absolute path, so that a copy of a
    machine's cron.daily in a repository is read as what it is. The two kinds
    of directory have different rules -- these files have to be executable --
    and guessing wrong gives every file in it the wrong verdict.
    """
    name = os.path.basename(os.path.normpath(os.path.realpath(path)))
    return name in {os.path.basename(d) for d in RUN_PARTS_DIRS}


def audit_paths(paths, system=None, audit=None):
    """Audit files, drop-in directories of crontabs, and run-parts directories.

    A directory is treated as a run-parts directory when it is one of the four
    /etc/cron.<period> paths, and as a drop-in directory of crontabs otherwise
    -- which is what /etc/cron.d is, and what a copy of it in a repository is.
    """
    audit = audit or Audit()
    for path in paths:
        if os.path.isdir(path):
            _audit_dir(path, audit, system)
        else:
            _audit_file(path, audit, is_system_crontab(path) if system is None else system)
    return audit


def _audit_dir(path, audit, system):
    try:
        entries = sorted(os.listdir(path))
    except OSError as exc:
        audit.declined.append((path, exc.strerror))
        return

    # A name starting with a dot is not a mistake. Every cron directory on a
    # Debian machine has a `.placeholder` in it so dpkg keeps the directory,
    # and nobody who writes one is expecting it to run. Reporting five of
    # those on a stock image would teach a reader to skip the output.
    entries = [name for name in entries if not name.startswith(".")]

    if is_run_parts_dir(path):
        for name in entries:
            full = os.path.join(path, name)
            reason = run_parts_rejected(full)
            if reason:
                audit.findings.append(Finding(
                    "file-not-read", DEAD, full, None,
                    f"run-parts never runs this: {reason}"))
            else:
                audit.files_read += 1
                audit.jobs += 1
        return

    for name in entries:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            continue
        reason = cron_d_rejected(full)
        if reason:
            lost = _job_count(full)
            detail = f"cron never reads this file: {reason}"
            if lost:
                detail += f" -- {lost} job line{'s' if lost != 1 else ''} in it"
            audit.findings.append(Finding(
                "file-not-read", DEAD, full, None, detail, {"jobs_lost": lost}))
            continue
        _audit_file(full, audit, True if system is None else system)


def _job_count(path):
    """How many job lines are in a file nothing reads. Best effort, by eye."""
    try:
        text = _read(path)
    except OSError:
        return 0
    count = 0
    for line in text.splitlines():
        body = line.strip()
        if not body or body.startswith("#") or ENV_ASSIGNMENT.match(body):
            continue
        count += 1
    return count


def _audit_file(path, audit, system):
    try:
        text = _read(path)
    except OSError as exc:
        audit.declined.append((path, exc.strerror))
        return
    audit.files_read += 1
    findings, jobs = analyse_crontab(path, text, system=system)
    audit.jobs += len(jobs)
    audit.findings.extend(findings)
