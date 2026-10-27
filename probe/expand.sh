#!/bin/bash
# Print the value sets cron actually builds for a crontab line, out of cron's
# own parser debug output.
#
#   probe/expand.sh '0 0 * * SAT-SUN root true'
#   0 0 * * SAT-SUN root true   min=[0] hour=[0] dom=[1,2,...,31] mon=[1,...,12] dow=[]
#
# That empty day-of-week set is the point: the line is accepted, the file is
# kept, and the job can never fire.
#
# THE TRAP. `cron -N` exits with _exit(), so its debug output is whatever
# happened to be flushed from a stdio buffer -- truncated at an arbitrary
# point, and sometimes nothing at all. A truncated read and an empty field
# set look exactly the same from out here. So every answer below is thrown
# away unless the parse ran all the way to `returning successfully`. Without
# that check this script reported four expansions that were not real, and I
# believed them.
#
# Needs sudo, unshare and cron. Runs in a mount namespace; nothing outside it
# is touched.
set -u
E=${PROBE_ROOT:-/tmp/unscheduled-expand}
CRON=${CRON:-/usr/sbin/cron}

for line in "$@"; do
  sudo rm -rf "$E"
  mkdir -p "$E/etc/cron.d" "$E/spool"
  printf 'SHELL=/bin/sh\n' > "$E/etc/crontab"
  printf '%s\n' "$line" > "$E/etc/cron.d/probe"
  sudo chown -R root:root "$E/etc"
  sudo chmod 755 "$E/etc/cron.d"
  sudo chmod 644 "$E/etc/cron.d/probe"

  raw=$(sudo unshare -m bash -c "
    mount --bind $E/etc/crontab /etc/crontab
    mount --bind $E/etc/cron.d /etc/cron.d
    mkdir -p /var/spool/cron/crontabs
    mount --bind $E/spool /var/spool/cron/crontabs
    chmod 1730 /var/spool/cron/crontabs
    $CRON -N -x load,pars 2>/dev/null" | awk '/probe:load_user/{on=1} on')

  printf '%-30s ' "$line"
  if ! grep -qF 'load_entry()...returning successfully' <<<"$raw"; then
    if grep -qF 'about to parse numerics' <<<"$raw"; then
      echo 'REJECTED, or the debug output was truncated -- no answer'
    else
      echo 'the file was never read'
    fi
    continue
  fi
  for spec in '0,59:min' '0,23:hour' '1,31:dom' '1,12:mon' '0,7:dow'; do
    bounds=${spec%%:*}
    label=${spec##*:}
    values=$(grep -oE "set_element\(\?,${bounds},[0-9]+\)" <<<"$raw" \
             | grep -oE '[0-9]+\)$' | tr -d ')' | paste -sd, -)
    printf '%s=[%s] ' "$label" "$values"
  done
  echo
done
