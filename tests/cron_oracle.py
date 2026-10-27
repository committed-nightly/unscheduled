"""Ask the cron on this machine what it does, so a test can check a claim.

This is the oracle the rest of the suite is measured against. It puts one
job in a hermetic /etc/cron.d inside a mount namespace, runs `cron -N`, and
reports whether the job ran. Nothing outside the namespace is touched.

Two properties of `cron -N` shape every helper here, both found the hard way:

  * it runs exactly one job per invocation, so a probe must contain exactly
    one job that could run in this minute;
  * it honours the schedule, which is what makes it an oracle at all rather
    than a way of running everything.

A probe answers "would this fire in the minute we asked", which is not the
same question as "would this ever fire". That is why the reversed-range
cases below pin a field that matches *now* (`2-1` on a Tuesday): if cron
wrapped the range round, the job would run this minute, and it does not.
"""

import os
import shutil
import subprocess
import tempfile

import pytest

CANARY = "CANARY"


def available():
    """Can we ask cron anything at all on this machine?"""
    if shutil.which("cron") is None and not os.path.exists("/usr/sbin/cron"):
        return False
    if shutil.which("unshare") is None or shutil.which("sudo") is None:
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


needs_cron = pytest.mark.skipif(not available(), reason="needs cron, sudo and unshare")

CRON = shutil.which("cron") or "/usr/sbin/cron"


def _run(files, out_dir, modes=None):
    """Build a hermetic /etc + spool, run `cron -N`, return the markers made."""
    modes = modes or {}
    # Markers from an earlier probe in the same test would read as this
    # probe's result.
    for stale in os.listdir(out_dir):
        os.unlink(os.path.join(out_dir, stale))
    root = tempfile.mkdtemp(prefix="unscheduled-oracle-")
    os.chmod(root, 0o755)
    etc = os.path.join(root, "etc")
    crond = os.path.join(etc, "cron.d")
    spool = os.path.join(root, "spool")
    os.makedirs(crond)
    os.makedirs(spool)
    with open(os.path.join(etc, "crontab"), "w") as handle:
        handle.write("SHELL=/bin/sh\n")
    for name, text in files.items():
        with open(os.path.join(crond, name), "w") as handle:
            handle.write(text)

    subprocess.run(["sudo", "chown", "-R", "root:root", etc], check=True)
    subprocess.run(["sudo", "chmod", "755", crond], check=True)
    for name in files:
        target = os.path.join(crond, name)
        mode, owner = modes.get(name, ("644", "root:root"))
        subprocess.run(["sudo", "chmod", mode, target], check=True)
        subprocess.run(["sudo", "chown", owner, target], check=True)

    script = f"""
    mount --bind {etc}/crontab /etc/crontab
    mount --bind {crond} /etc/cron.d
    mkdir -p /var/spool/cron/crontabs
    mount --bind {spool} /var/spool/cron/crontabs
    chmod 1730 /var/spool/cron/crontabs
    {CRON} -N >/dev/null 2>&1
    sleep 0.3
    """
    subprocess.run(["sudo", "unshare", "-m", "bash", "-c", script], check=True)
    made = sorted(os.listdir(out_dir))
    shutil.rmtree(root, ignore_errors=True)
    return made


def file_is_read(tmp_path, name, mode="644", owner="root:root"):
    """Does cron read a /etc/cron.d file with this name, mode and owner?

    The job inside is a plain `* * * * *`, so a False here means the file was
    never opened rather than that its schedule did not match.
    """
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    os.chmod(out, 0o777)
    files = {name: f"* * * * * root touch {out}/{CANARY}\n"}
    made = _run(files, str(out), modes={name: (mode, owner)})
    return CANARY in made


def line_is_kept(tmp_path, line, terminate=True):
    """Does cron keep a file containing this line, plus one known-good job?

    A malformed line takes its whole file down, so the canary is the probe:
    present means cron accepted `line`, absent means it refused it and threw
    the file away.

    The canary goes last on purpose. cron runs the job nearest the end of the
    file first, and `cron -N` runs one job and stops -- so with the canary
    last it wins the race even when the suspect line is valid and also due
    this minute. Put the canary first and a perfectly good suspect line
    steals the single run, and every accepted line reads as rejected.
    """
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    os.chmod(out, 0o777)
    canary = f"* * * * * root touch {out}/{CANARY}"
    if terminate:
        text = f"{line}\n{canary}\n"
    else:
        # The missing newline has to be on the suspect line, so here the
        # suspect goes last and the canary is above it. Nothing is lost: an
        # unterminated line cannot run, so it cannot steal the single run.
        text = f"{canary}\n{line}"
    made = _run({"probe": text}, str(out))
    return CANARY in made


def job_runs(tmp_path, line, marker="MARKER", prelude=""):
    """Does this one job run in this minute, and what did it leave behind?

    Returns (ran, markers). The job must be the only one in the file: cron -N
    runs one job per invocation.
    """
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    os.chmod(out, 0o777)
    text = prelude + line.replace("{out}", str(out)).replace("{marker}", marker) + "\n"
    made = _run({"probe": text}, str(out))
    return (made != [], made)
