"""Which of these files does anything ever open?

Two different sets of rules, and the difference catches people out:

/etc/cron.d        read by cron. The name must be letters, digits, underscore
                   and hyphen only -- so `backup.sh` and `jobs.conf` are
                   never read -- the file must be owned by root and must not
                   be group- or other-writable. It does not need to be
                   executable.

/etc/cron.daily    handed to run-parts by a line in /etc/crontab. Same naming
/etc/cron.hourly   rule, because cron.d borrowed it from run-parts, but here
/etc/cron.weekly   the file does have to be executable -- and an unexecutable
/etc/cron.monthly  script in cron.daily is the most common way to have a job
                   that silently never runs.

All of it measured against cron 3.0pl1 and run-parts 5.17; the naming rule in
particular is the one place the cron man page sends you to another man page.
"""

import os
import re
import stat

# run-parts(8): "the names must consist entirely of ASCII upper- and
# lower-case letters, ASCII digits, ASCII underscores, and ASCII
# minus-hyphens". cron applies the same rule to /etc/cron.d.
RUN_PARTS_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

RUN_PARTS_DIRS = ("/etc/cron.hourly", "/etc/cron.daily",
                  "/etc/cron.weekly", "/etc/cron.monthly")


def name_rejected(name):
    """The reason this filename is never read, or None."""
    if RUN_PARTS_NAME.match(name):
        return None
    bad = sorted({c for c in name if not re.match(r"[A-Za-z0-9_-]", c)})
    shown = " ".join(repr(c) for c in bad)
    return (f"the name contains {shown}, and only letters, digits, underscore "
            "and hyphen are allowed")


def ownership_rejected(path):
    """Why cron refuses to read this file for its mode or owner, or None.

    cron checks the file it ends up at, so a symlink is fine as long as the
    target passes. os.stat follows the link, which is the check we want.
    """
    try:
        info = os.stat(path)
    except OSError as exc:
        return f"cannot be read: {exc.strerror}"
    if not stat.S_ISREG(info.st_mode):
        return "not a regular file"
    if info.st_uid != 0:
        owner = info.st_uid
        return f"owned by uid {owner}, not root -- cron will not read it"
    if info.st_mode & stat.S_IWGRP:
        return f"mode {info.st_mode & 0o777:04o} is group-writable -- cron will not read it"
    if info.st_mode & stat.S_IWOTH:
        return f"mode {info.st_mode & 0o777:04o} is world-writable -- cron will not read it"
    return None


def run_parts_rejected(path):
    """Why run-parts will never run this file, or None."""
    name = os.path.basename(path)
    reason = name_rejected(name)
    if reason:
        return reason
    try:
        info = os.stat(path)
    except OSError as exc:
        return f"cannot be read: {exc.strerror}"
    if not stat.S_ISREG(info.st_mode):
        return "not a regular file"
    if not info.st_mode & 0o111:
        return (f"mode {info.st_mode & 0o777:04o} -- run-parts only runs "
                "executable files, and this one is not")
    return None


def cron_d_rejected(path, deployed=None):
    """Why cron will never read this /etc/cron.d file, or None.

    `deployed` says whether this is the real thing or a copy. The naming rule
    applies either way -- a file called `backup.sh` in a repository will be
    called `backup.sh` once it is installed, and will be just as ignored --
    but mode and ownership are only meaningful where cron is going to look.
    A checkout of a configuration repository is owned by whoever cloned it
    and git records no mode beyond the execute bit, so judging the copy would
    report every file in it and mean nothing. Defaults to true under /etc.
    """
    reason = name_rejected(os.path.basename(path))
    if reason:
        return reason
    if deployed is None:
        deployed = is_deployed(path)
    return ownership_rejected(path) if deployed else None


def is_deployed(path):
    """Is this path somewhere cron is actually going to look?"""
    return os.path.realpath(path).startswith("/etc/")
