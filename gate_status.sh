#!/bin/zsh
# One-command health check for the Gate C bootstrap chain.
#
#   ./gate_status.sh          # everything that matters, ~instant
#   ./gate_status.sh -f       # also live-follow the log (Ctrl-C to stop)
#
# Safe to run anytime — it never touches the background run.

cd "$(dirname "$0")"

now=$(date +%s)
log_age=$(( now - $(stat -f %m gate_c.log 2>/dev/null || echo 0) ))

echo "=== Gate C status — $(date '+%H:%M:%S') ==="

# --- Result / state ---
if [ -f bootstrap/GATE_C_PROVENANCE.md ]; then
  echo "RESULT:   GATE C PASSED — see bootstrap/GATE_C_PROVENANCE.md"
  grep -E "sha256|fixed point" bootstrap/GATE_C_PROVENANCE.md | sed 's/^/  /'
elif grep -q "GATE C FAILED" gate_c.log 2>/dev/null; then
  echo "RESULT:   GATE C FAILED (see gate_c.log)"
elif grep -q "FATAL" gate_c.log 2>/dev/null; then
  echo "RESULT:   DRIVER ERROR (see gate_c.log)"
else
  pid=$(pgrep -f "run_gate_c.py" | head -1)
  stage=$(grep "Running osakac" gate_c.log 2>/dev/null | tail -1 | sed 's/^Running //; s/ .*->/ ->/; s/ (this.*//')
  if [ -n "$pid" ]; then
    cpu=$(ps -o %cpu= -p "$pid" | tr -d ' ')
    if [ "$log_age" -le 120 ]; then
      echo "STATE:    RUNNING ✓  (pid $pid, cpu ${cpu}%)"
    else
      echo "STATE:    RUNNING but log idle ${log_age}s — check again in 2 min"
    fi
  else
    wd=$(pgrep -f "gate_watchdog.sh" | head -1)
    if [ -n "$wd" ]; then
      echo "STATE:    watchdog alive (pid $wd) — driver restarting…"
    elif [ "$log_age" -gt 120 ]; then
      echo "STATE:    NOT RUNNING ✗  (log idle ${log_age}s)"
      echo "          Restart with:  python3 start_gate_detached.py"
    else
      echo "STATE:    starting up…"
    fi
  fi
  [ -n "$stage" ] && echo "STAGE:    $stage"
fi

# --- Artifacts ---
line="ARTIFACTS:"
for s in stage1 stage2 stage3; do
  f="bootstrap/osakac.$s.sbc"
  if [ -f "$f" ]; then
    line="$line $s ✓($(( $(stat -f %z "$f") / 1024 )) KB)"
  else
    line="$line $s —"
  fi
done
echo "$line"

# --- Latest activity ---
echo "LOG (last 3 lines):"
tail -3 gate_c.log 2>/dev/null | sed 's/^/  /'

# --- Optional live follow ---
if [ "$1" = "-f" ]; then
  echo "--- following gate_c.log (Ctrl-C to stop; run keeps going) ---"
  tail -f gate_c.log
fi