"""Launch the Gate C chain fully detached from any terminal.

Uses the classic double-fork + setsid: the watchdog ends up in its own
session with no controlling terminal, so closing VS Code, terminals, or
Ctrl-C on `tail -f` cannot kill it (reparented to launchd instead).

Note: a launchd LaunchAgent was tried first, but macOS privacy (TCC)
denies launchd-spawned processes read access to ~/Desktop — terminal-
inherited processes keep that access, so detached-from-terminal is the
correct mechanism while this project lives on the Desktop.

Usage:
    python3 start_gate_detached.py

Monitor with:
    ./gate_status.sh
"""
import os
import sys

if os.fork() > 0:
    sys.exit(0)          # parent returns to the shell immediately
os.setsid()              # new session: no controlling terminal
if os.fork() > 0:
    os._exit(0)          # intermediate parent exits; child is reparented

os.chdir(os.path.dirname(os.path.abspath(__file__)))
with open(os.devnull, "rb") as devnull_in:
    os.dup2(devnull_in.fileno(), 0)
# exec caffeinate to keep the Mac awake for the whole chain
os.execvp("/bin/zsh", [
    "/bin/zsh", "-c",
    "exec /usr/bin/caffeinate -ims /bin/zsh ./gate_watchdog.sh",
])