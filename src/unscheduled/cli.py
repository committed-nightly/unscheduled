"""unscheduled: say which of your cron jobs never run."""

import argparse
import os
import sys

from .analyse import audit_paths
from .files import RUN_PARTS_DIRS
from .report import render, render_json

# What cron on a Debian or Ubuntu machine actually reads, in the order it
# reads it. The spool directory is last because it is usually mode 1730 and
# an unprivileged run cannot list it; that comes back as a decline rather
# than as a clean bill of health.
MACHINE_PATHS = ("/etc/crontab", "/etc/cron.d") + RUN_PARTS_DIRS + (
    "/var/spool/cron/crontabs",
)

USAGE = """\
  unscheduled                     everything cron reads on this machine
  unscheduled FILE ...            one crontab, or several
  unscheduled DIR                 a drop-in directory of crontabs
  unscheduled --user FILE         a crontab with no user field
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="unscheduled",
        description="Say which of your cron jobs never run.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="crontab files or directories; default is this machine")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--user", dest="system", action="store_false", default=None,
                       help="these files are user crontabs: five fields and no username")
    group.add_argument("--system", dest="system", action="store_true",
                       help="these files are system crontabs: a username after the "
                            "time fields (the default for anything outside a spool "
                            "directory)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="one line per finding")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.paths:
        paths = list(args.paths)
        missing = [p for p in paths if not os.path.exists(p)]
        if missing:
            for path in missing:
                print(f"unscheduled: {path}: no such file or directory", file=sys.stderr)
            return 2
    else:
        paths = [p for p in MACHINE_PATHS if os.path.exists(p)]
        if not paths:
            print("unscheduled: nothing cron reads exists on this machine -- "
                  "no /etc/crontab, no /etc/cron.d, no cron.daily. Give me a file "
                  "to read instead.", file=sys.stderr)
            return 2

    audit = audit_paths(paths, system=args.system)

    # A run that read nothing must not print a clean bill of health: an
    # unreadable /etc/cron.d looks exactly like an empty one from here.
    if audit.files_read == 0 and not audit.findings:
        for path, reason in audit.declined:
            print(f"unscheduled: {path}: {reason}", file=sys.stderr)
        print("unscheduled: read nothing, so there is nothing to report. This is "
              "not a clean result.", file=sys.stderr)
        return 2

    print(render_json(audit) if args.json else render(audit, terse=args.quiet))
    return 1 if audit.dead else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
