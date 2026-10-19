#!/usr/bin/env bash
# Keep-alive entry point for the CYKF tracker supervisor (cron every 5 min + ops watchdog).
# It sleeps outside Chartright windows (30 min before shift start -> shift end) and polls
# every 30 s inside them. One instance only: pgrep here, plus a flock inside the script.
export PATH=/usr/local/bin:/usr/bin:/bin
cd "$(dirname "$0")" || exit 1
pgrep -f "python3 -m ykf.run supervise$" >/dev/null && exit 0
mkdir -p /home/box/shared/ykf-tracker
setsid nohup python3 -m ykf.run supervise >> /home/box/shared/ykf-tracker/supervisor.out 2>&1 < /dev/null &
