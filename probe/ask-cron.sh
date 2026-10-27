#!/bin/bash
# Ask cron what it does with a crontab, without waiting for a minute to come
# round and without touching this machine's real cron setup.
#
#   probe/ask-cron.sh <file-in-cron.d-called-this> <mode> <owner> <<'EOF'
#   * * * * * root touch /tmp/marker
#   EOF
#
# Prints RAN or NOTHING, and then every file the jobs created.
#
# How it works, and the two traps in it:
#
#  * `cron -N` is documented as "run cron jobs Now, immediately, and exit ...
#    useful to perform tests". It honours the schedule -- a job for January 1
#    does not run in October -- but it runs exactly ONE job per invocation
#    and then exits, so a probe must contain exactly one job it expects to
#    run. Two valid jobs and you learn nothing about the one that stayed put.
#
#  * cron -N exits without flushing stdio, so its own `-x` debug output is
#    truncated at an arbitrary point. Anything read out of that stream has to
#    be checked for a completeness marker first; a short read looks exactly
#    like an empty field set. See probe/expand.sh.
#
# Needs sudo and unshare. Everything happens inside a mount namespace, so
# /etc/crontab, /etc/cron.d and the crontab spool of the machine running this
# are never modified.
set -u
ROOT=${PROBE_ROOT:-/tmp/unscheduled-probe}
NAME=$1
MODE=${2:-644}
OWNER=${3:-root:root}

sudo rm -rf "$ROOT"
mkdir -p "$ROOT/etc/cron.d" "$ROOT/spool" "$ROOT/out"
chmod 777 "$ROOT/out"
printf 'SHELL=/bin/sh\n' > "$ROOT/etc/crontab"
cat > "$ROOT/etc/cron.d/$NAME"
sudo chown -R root:root "$ROOT/etc"
sudo chmod 755 "$ROOT/etc/cron.d"
sudo chmod "$MODE" "$ROOT/etc/cron.d/$NAME"
sudo chown "$OWNER" "$ROOT/etc/cron.d/$NAME"

sudo unshare -m bash -c "
  mount --bind $ROOT/etc/crontab /etc/crontab
  mount --bind $ROOT/etc/cron.d /etc/cron.d
  mkdir -p /var/spool/cron/crontabs
  mount --bind $ROOT/spool /var/spool/cron/crontabs
  chmod 1730 /var/spool/cron/crontabs
  cron -N >/dev/null 2>&1
  sleep 0.3
"

if [ -n "$(ls -A "$ROOT/out" 2>/dev/null)" ]; then
  echo RAN
  for f in "$ROOT/out"/*; do
    echo "  $(basename "$f")$([ -s "$f" ] && printf ': %s' "$(head -c 200 "$f")")"
  done
else
  echo NOTHING
fi
