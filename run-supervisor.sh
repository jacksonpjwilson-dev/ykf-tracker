#!/usr/bin/env bash
# Start the CYKF tracker supervisor. It sleeps outside Chartright windows
# (30 min before shift start -> shift end) and polls every 30 s inside them.
# Single instance is enforced with a lock file. NOT scheduled yet: needs Jackson's OK.
cd "$(dirname "$0")" || exit 1
exec python3 -m ykf.run supervise "$@" >> /home/box/shared/ykf-tracker/supervisor.out 2>&1
