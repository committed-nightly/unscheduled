"""Reading a crontab the way cron reads one, including the parts it refuses.

The unit of refusal is the file, not the line. One malformed field anywhere in
an /etc/cron.d file and cron throws the entire file away -- every other job in
it included, whether it sits above or below the bad line. Measured both ways;
nothing is logged where you would look for it.

Two shapes of crontab live here:

  system  -- /etc/crontab and /etc/cron.d/*, which have a user field
  user    -- /var/spool/cron/crontabs/*, which do not

Getting that backwards is itself a finding: a user crontab line pasted into
/etc/cron.d has its first command word read as a username, and the file is
refused for an unknown user.
"""

import pwd
import re

from .fields import SPECIALS, FieldError, parse_time_fields

ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class Job:
    """One job line cron accepted."""

    def __init__(self, lineno, line, fields, user, command, env, reboot=False):
        self.lineno = lineno
        self.line = line
        self.fields = fields          # five Fields, or None for @reboot
        self.user = user              # None in a user crontab
        self.command = command
        self.env = env                # environment in force at this line
        self.reboot = reboot

    @property
    def schedule(self):
        return " ".join(f.text for f in self.fields) if self.fields else "@reboot"


class Rejection:
    """The one line that cost the file everything.

    `user_field` is the word cron read as a username, when that is what went
    wrong. It is kept separately so a caller can tell a mistyped username
    from a user crontab in the wrong place, which look identical in the
    message and want opposite advice.
    """

    def __init__(self, lineno, line, reason, user_field=None):
        self.lineno = lineno
        self.line = line
        self.reason = reason
        self.user_field = user_field


class Crontab:
    """A parsed crontab: either a list of jobs, or the rejection that ended it.

    `jobs_before_rejection` is how many jobs cron had already accepted when it
    hit the bad line. They do not run either, and saying so is most of the
    value of knowing about the bad line at all.
    """

    def __init__(self, path, jobs, rejection, jobs_before_rejection=0):
        self.path = path
        self.jobs = jobs
        self.rejection = rejection
        self.jobs_before_rejection = jobs_before_rejection


def _strip_quotes(value):
    """cron removes one layer of matching quotes from an environment value."""
    for quote in ('"', "'"):
        if len(value) >= 2 and value[0] == quote and value[-1] == quote:
            return value[1:-1]
    return value


def _known_user(name):
    try:
        pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def parse(text, path="<text>", system=True, user_exists=_known_user):
    """Parse a crontab. Returns a Crontab.

    `user_exists` is injected so the tests can describe a machine with users
    this one does not have, rather than asserting against whoever happens to
    be in the runner's /etc/passwd.
    """
    jobs = []
    env = {}
    lines = text.split("\n")

    # Measured: if the file does not end in a newline and its last line is a
    # job line or an assignment, cron refuses the whole file. A final comment
    # or blank line without a newline is harmless.
    unterminated = None
    if lines and lines[-1] == "":
        lines.pop()
    elif lines:
        last = lines[-1]
        if last.strip() and not last.lstrip().startswith("#"):
            unterminated = len(lines)

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue

        if unterminated == lineno:
            return Crontab(path, [], Rejection(
                lineno, line,
                "the file does not end in a newline, so cron refuses it -- "
                "the last line is incomplete as far as cron is concerned",
            ), len(jobs))

        match = ENV_ASSIGNMENT.match(line)
        if match:
            env[match.group(1)] = _strip_quotes(match.group(2).strip())
            continue

        try:
            job = _parse_job(lineno, line, system, dict(env), user_exists)
        except FieldError as exc:
            return Crontab(path, [], Rejection(
                lineno, line, str(exc), getattr(exc, "user_field", None)), len(jobs))
        jobs.append(job)

    return Crontab(path, jobs, None)


def _parse_job(lineno, line, system, env, user_exists):
    body = line.strip()

    if body.startswith("@"):
        word, _, rest = body.partition(" ")
        keyword = word.lower()
        if keyword == "@reboot":
            fields = None
        elif keyword in SPECIALS:
            fields = parse_time_fields(SPECIALS[keyword].split())
        else:
            raise FieldError(f"{word!r} is not one of cron's @ keywords")
        remainder = rest.strip()
    else:
        parts = body.split(None, 5)
        if len(parts) < 6:
            # Six words minimum either way. A user crontab needs five time
            # fields and a command; a system crontab needs a username in
            # between, and that case is caught below on the username instead.
            raise FieldError(
                f"{len(parts)} words on this line: a crontab line needs five time "
                "fields and a command" + (", with a user before it" if system else "")
            )
        fields = parse_time_fields(parts[:5])
        remainder = parts[5]

    user = None
    if system:
        # Any whitespace, not a space. Every Debian /etc/crontab in the world
        # separates its fields with tabs, and splitting on " " here read the
        # username as `root\tcd` and declared the stock file broken.
        split = remainder.split(None, 1)
        user = split[0] if split else ""
        command = split[1] if len(split) > 1 else ""
        if not user_exists(user):
            error = FieldError(
                f"{user!r} is not a user on this machine -- in /etc/crontab and "
                "/etc/cron.d the word after the time fields is a username"
            )
            error.user_field = user
            raise error
        command = command.strip()
        if not command:
            raise FieldError(f"nothing after the username {user!r}: no command to run")
    else:
        command = remainder.strip()
        if not command:
            raise FieldError("nothing after the time fields: no command to run")

    return Job(lineno, line, fields, user, command, env, reboot=fields is None)


def split_percent(command):
    """Split a command at cron's first unescaped `%`.

    cron turns an unescaped `%` into a newline and feeds everything after it
    to the command on standard input. `date +%Y` in a crontab runs `date +`
    with `Y` on stdin, which fails, which is why nothing happened. `\\%` is a
    literal percent and the rest of the line survives.

    Returns (what cron runs, what becomes stdin or None).
    """
    out = []
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and index + 1 < len(command) and command[index + 1] == "%":
            out.append("%")
            index += 2
            continue
        if char == "%":
            return "".join(out), command[index + 1:]
        out.append(char)
        index += 1
    return "".join(out), None
