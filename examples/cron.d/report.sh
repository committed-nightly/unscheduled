# A perfectly good crontab in a file cron will never open, because of the
# name. Four jobs here, all dead.
0 6 * * 1 root /usr/bin/weekly-report
0 6 1 * * root /usr/bin/monthly-report
@daily root /usr/bin/daily-report
0 7 * * * root /usr/bin/health-check
