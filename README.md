# unscheduled

Say which of your cron jobs never run.

If something was supposed to happen at 3am and did not, there are five
different reasons and cron logs none of them anywhere you would look. This
reads your crontabs the way cron reads them and tells you which ones it
throws away.

```
$ unscheduled examples/cron.d/weekend
examples/cron.d/weekend:6  never-fires
    the day-of-week field 'SAT-SUN' expands to nothing -- the schedule is '0 3 * *
    SAT-SUN'

Found 1 job that never runs as written. Read 1 file, holding 1 job cron keeps.
```

`SAT-SUN` is accepted by cron, parses without error, keeps the file, and
matches no day of any week. cron does not wrap a range round the end of a
field, so Saturday-to-Sunday is Saturday counting backwards to Sunday, which
is no days at all. `SAT,SUN` is what works.

## Install

```sh
pip install -e .
```

Python 3.10+, no dependencies. Needs nothing installed to read a crontab;
the test suite needs `cron` to check itself.

## Use

```sh
unscheduled                      # everything cron reads on this machine
unscheduled /etc/cron.d/backup   # one file
unscheduled deploy/cron.d        # a directory of crontabs
unscheduled --user ~/mycrontab   # a crontab with no username field
unscheduled -q                   # one line per finding
unscheduled --json
```

Exit code is 0 when there is nothing to report, 1 when there is, and 2 when
unscheduled could not get an answer. On exit 2 nothing is printed to stdout,
so a run that read nothing can never be mistaken for a clean machine — an
unreadable `/etc/cron.d` looks exactly like an empty one from the outside.

With no arguments it reads `/etc/crontab`, `/etc/cron.d`, the four
`/etc/cron.{hourly,daily,weekly,monthly}` directories, and the crontab
spool. The spool is mode 1730 on a Debian machine, so an unprivileged run
says so rather than counting zero user crontabs as good news.

## What it finds

| kind | what it means |
| --- | --- |
| `file-not-read` | nothing ever opens the file: the name, the mode, or the owner |
| `file-rejected` | cron opened it, refused one line, and dropped every job in it |
| `never-fires` | the line is fine and the schedule can never come round |
| `command-truncated` | an unescaped `%` cut the command short |
| `command-not-found` | the job fires on time and the shell finds nothing to run |

And two notes, which are not findings, because the job does run:
`needs-boot` for `@reboot`, and `day-fields-ored` for the Friday-the-13th
trap below.

Files whose names start with a dot are skipped. Every cron directory on a
Debian machine has a `.placeholder` in it so that dpkg keeps the directory,
and reporting five of those on a stock image would teach you to skip the
output.

## Why not just read the crontab

Because for all of these, cron is quiet. Measured against cron 3.0pl1 on
Ubuntu 24.04 — the table is [a test](tests/test_against_cron.py), not a
claim:

| what is wrong | cron's complaint | the job |
| --- | --- | --- |
| `0 3 * * SAT-SUN` | none | never runs |
| `0 3 * * 2-1` | none | never runs |
| `58-2 * * * *` | none | never runs |
| `0 0 30 2 *` | none | never runs |
| `17 * * * * root touch /x-%Y` | none | runs `touch /x-`, with `Y` on stdin |
| a job calling something in `/usr/local/bin` | none | fires, finds nothing |
| `/etc/cron.d/backup.sh` | none | never read |
| `/etc/cron.d/jobs`, mode 664 | none | never read |
| `/etc/cron.daily/cleanup.sh` | none | never run |
| `/etc/cron.daily/report`, not executable | none | never run |
| one field out of range, anywhere in a file | syslog only | every job in that file stops |
| a file not ending in a newline | syslog only | every job in that file stops |

"syslog only" means there is no exit code to check, no annotation, and
nothing on anybody's terminal. On a machine with no syslog daemon — a
container, most of CI — there is no record at all.

## The three that surprised me

**One bad line costs the whole file.** Not the line: the file. An hour of 25
on the last line of an `/etc/cron.d` file stops the correct jobs above it as
well, and they had already been parsed successfully when it happened.

```
$ unscheduled examples/cron.d/nightly
examples/cron.d/nightly:6  file-rejected
    cron refuses the whole file over this line: hour: 25 is outside 0-23 -- and it takes
    the 2 jobs cron had already read in this file with it

Found 1 file cron refuses outright, losing 2 job lines that are themselves fine. Read 1 file, holding 0 jobs cron keeps.
```

**A reversed range is empty, not wrapped.** `58-2 * * * *` is how you write
"either side of the hour" if you have used a scheduler that supports it.
cron accepts the line, parses it to completion, builds an empty set of
minutes and keeps the file. Asking cron directly, on a Tuesday:

```
$ cat /etc/cron.d/probe
* * * * 2-1 root touch /tmp/out/RAN      # Tuesday to Monday
$ cron -N; ls /tmp/out
$                                        # nothing, on a Tuesday
```

**The two day fields are ORed, until one of them starts with a star.**
`0 0 13 * 5` is not Friday the 13th. It is every 13th *and* every Friday:
cron ORs day-of-month and day-of-week when both are restricted. Write
`0 0 13 * */1` and the star flips the whole rule to AND, which is why
unscheduled tracks whether a field was *written* with a star rather than
what it expands to — `*/1` and `1-31` cover the same seven days a week and
behave differently.

That one is in crontab(5). The other two are not.

## How it knows

`tests/test_against_cron.py` puts each case to the cron on the machine and
compares the answer with unscheduled's. 88 cases: which field forms cron
accepts, what they expand to, which files it opens, what an unescaped `%`
does, which `PATH` a job actually gets.

It works by running `cron -N` — documented as "run cron jobs Now,
immediately, and exit ... useful to perform tests" — inside a mount
namespace, with a throwaway `/etc/crontab`, `/etc/cron.d` and spool bound
over the real ones. Nothing outside the namespace is touched. `probe/` has
the same thing as a shell script, for asking a one-off question.

Two traps in that, both of which cost me an hour and are commented in the
code so they cost the next person nothing:

- `cron -N` honours the schedule, which is what makes it an oracle at all,
  but it runs **exactly one job** per invocation and then exits. A probe
  with two due jobs in it teaches you nothing about the one that stayed put.
- `cron -N` exits without flushing its own debug output, so reading field
  expansions out of `cron -x pars` gives you a stream truncated at an
  arbitrary point — and an empty field set and a truncated read look
  identical. Anything parsed out of it has to be checked against a
  completeness marker first. I believed four wrong expansions before I
  noticed.

What is **not** measured: the "no such date" findings — February 30, April
31 — are arithmetic. There is no way to ask cron about a date that is not
today without moving the clock, and a test that waits for February is not a
test. Those answers are cross-checked against a brute-force walk of 28 years
of real dates instead, in `tests/test_schedule.py`. 28 years is how long the
calendar takes to repeat, February 29 included; at four years the brute
force was wrong rather than slow, and said a schedule needing a May 1 that
falls on a Tuesday could never run.

## What it deliberately does not do

- **It does not run anything and does not write anything.** No `crontab`
  invocation, no file touched. It reads.
- **It does not guess about shell syntax.** `command-not-found` is only
  reported for a plain program name. A variable, an assignment prefix or a
  subshell gets no verdict, because answering properly means being a shell,
  and a false "this never runs" is worth less than a missed one.
- **It does not judge a copy on its mode or owner.** Point it at a `cron.d`
  in a configuration repository and it checks the names and the contents but
  not the permissions: a checkout is owned by whoever cloned it and git
  records no mode beyond the execute bit, so judging the copy would report
  every file in it and mean nothing. Under `/etc`, where cron is actually
  going to look, it checks all of it.
- **It says nothing about anacron.** If `/usr/sbin/anacron` exists, the
  `/etc/crontab` lines that call `run-parts` skip it and anacron runs those
  directories on its own schedule instead. The naming and execute-bit
  findings hold either way — anacron uses run-parts too — but no claim is
  made here about *when* `/etc/cron.daily` runs.
- **It does not check whether the command works**, only whether the shell
  can find it. A script that exits 1 every night is a job that runs.
- **It is Debian and Ubuntu cron.** Every rule here was measured against
  vixie-derived cron 3.0pl1. cronie, busybox crond and the systemd timer
  world have different answers, and this tool does not know about them.

## Licence

MIT.
