#!/usr/bin/env python3
"""Phase 0: measure raw interpreter throughput of the native VM."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_selfhost_vm import compile_document

N = 200000
GUEST = f"""
truthaboutgrain total = 0;
truthaboutgrain i = 0;
while i < {N} {{
    total = total + i;
    i = i + 1;
}}
Say(total);
"""


def main():
    document = compile_document(GUEST)
    doc = Path("/tmp/bench_doc.sbc")
    args = Path("/tmp/bench_args.json")
    doc.write_text(json.dumps(document, separators=(",", ":")) + "\n")
    args.write_text("[]")
    t0 = time.time()
    proc = subprocess.run(
        [str(ROOT / "native" / "osakavm"), "run", str(doc), str(args)],
        capture_output=True, text=True)
    elapsed = time.time() - t0
    env = json.loads(proc.stdout)
    print("result:", repr(env["value"]["stdout"].strip()))
    print("elapsed: %.3fs for %d loop iterations" % (elapsed, N))
    print("exit:", proc.returncode)


if __name__ == "__main__":
    main()