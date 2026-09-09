#!/bin/zsh
# Gate C supervisor: reruns the (resumable) gate driver until it exits 0.
# Each restart skips completed stages AND completed pipeline phases, so a
# crash only loses the current phase. See docs/SELF_HOSTING.md.
#
# NOTE: the loop variable must NOT be named `status` — that is a read-only
# special parameter in zsh, and assigning it crashed this script (and with
# it the whole restart safety net) the first time the driver exited.
cd "$(dirname "$0")"
while true; do
  pypy3 -u run_gate_c.py >>gate_c.log 2>>gate_err.log
  rc=$?
  echo "[watchdog] gate driver exited with rc=$rc at $(date)" >>gate_c.log
  if [ $rc -eq 0 ]; then
    break
  fi
  sleep 10
done