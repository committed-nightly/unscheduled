"""Printing findings.

The default output puts the place and the kind on one line and the reason
under it, because every finding here needs a sentence: "this never runs" is
not useful without the part that says why not.

The summary counts jobs, not findings. One job line can be wrong in two ways
at once, and a file nothing reads can hold nine jobs; a count of findings
would make the first look worse than it is and the second look better.
"""

import json
import textwrap

JOB_KINDS = ("never-fires", "command-truncated", "command-not-found")


def _plural(count, word, suffix="s"):
    return f"{count} {word}{'' if count == 1 else suffix}"


def _wrap(text, indent="    "):
    return textwrap.fill(text, width=88, initial_indent=indent,
                         subsequent_indent=indent)


def render(audit, terse=False):
    lines = []
    for finding in audit.dead + audit.notes:
        if terse:
            lines.append(f"{finding.where}  {finding.kind:<17}  {finding.detail}")
        else:
            lines.append(f"{finding.where}  {finding.kind}")
            lines.append(_wrap(finding.detail))
    lines.append("")
    lines.append(summary(audit))
    for path, reason in audit.declined:
        line = f"declined: {path}: {reason}"
        # /var/spool/cron/crontabs is mode 1730 on a Debian machine, so an
        # unprivileged run cannot see a single user crontab. Saying which
        # jobs were not looked at matters more than the errno.
        if reason == "Permission denied" and "cron/crontabs" in path:
            line += " -- user crontabs are not included in the numbers above; "\
                    "run as root to read them"
        lines.append(line)
    return "\n".join(lines)


def counts(audit):
    """The numbers the summary is made of, so a test can assert on them."""
    broken_jobs = {(f.path, f.lineno) for f in audit.dead if f.kind in JOB_KINDS}
    unread = [f for f in audit.dead if f.kind == "file-not-read"]
    refused = [f for f in audit.dead if f.kind == "file-rejected"]
    return {
        "broken_jobs": len(broken_jobs),
        "unread_files": len(unread),
        "unread_job_lines": sum(f.extra.get("jobs_lost", 0) for f in unread),
        "refused_files": len(refused),
        "refused_job_lines": sum(f.extra.get("jobs_lost", 0) for f in refused),
        "jobs": audit.jobs,
        "files_read": audit.files_read,
        "notes": len(audit.notes),
    }


def summary(audit):
    number = counts(audit)
    clauses = []
    if number["broken_jobs"]:
        clauses.append(f"{_plural(number['broken_jobs'], 'job')} that never "
                       f"{'runs' if number['broken_jobs'] == 1 else 'run'} as written")
    if number["unread_files"]:
        clause = f"{_plural(number['unread_files'], 'file')} nothing reads"
        if number["unread_job_lines"]:
            clause += f" ({_plural(number['unread_job_lines'], 'job line')} in them)"
        clauses.append(clause)
    if number["refused_files"]:
        clause = f"{_plural(number['refused_files'], 'file')} cron refuses outright"
        if number["refused_job_lines"]:
            clause += (f", losing {_plural(number['refused_job_lines'], 'job line')} "
                       "that are themselves fine")
        clauses.append(clause)

    if not clauses:
        head = (f"Every job unscheduled can check will run. "
                f"{_plural(audit.jobs, 'job')} in "
                f"{_plural(audit.files_read, 'file')}.")
    else:
        if len(clauses) > 1:
            listed = ", ".join(clauses[:-1]) + f", and {clauses[-1]}"
        else:
            listed = clauses[0]
        head = (f"Found {listed}. Read {_plural(audit.files_read, 'file')}, "
                f"holding {_plural(audit.jobs, 'job')} cron keeps.")

    if number["notes"]:
        head += (f" {_plural(number['notes'], 'note')}, which "
                 f"{'does' if number['notes'] == 1 else 'do'} run.")
    return head


def render_json(audit):
    payload = {"counts": counts(audit),
               "declined": [{"file": p, "reason": r} for p, r in audit.declined],
               "findings": [f.as_dict() for f in audit.dead + audit.notes]}
    return json.dumps(payload, indent=2)
