#!/usr/bin/env python3
"""Gate E spike: run one arithmetic guest program on the native VM and
compare the envelope against the Stage 0 Python VM."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_selfhost_vm import compile_document, run_on_python_vm

GUEST = """
truthaboutgrain x = 6 * 7 + 1;
Say(x);
Say(x - 1);
truthaboutgrain y = 10 % 3;
Say(y);
truthaboutgrain half = 1 / 2;
Say(half);
truthaboutgrain greeting = "hello" + " " + "native";
Say(greeting);
truthaboutgrain xs = [1, 2, 3];
push(xs, 4);
Say(xs);
Say(len(xs));
truthaboutgrain m = {"a": 1, "b": 2};
m["c"] = 3;
Say(contains(m, "c"));
Say(keys(m));
Say(len(m));
func fact(n) {
    if n <= 1 {
        return 1;
    }
    return n * fact(n - 1);
}
Say(fact(6));
truthaboutgrain total = 0;
truthaboutgrain i = 0;
while i < 5 {
    total = total + i;
    i = i + 1;
}
Say(total);
"""


def main():
    document = compile_document(GUEST)
    py = run_on_python_vm(document)

    doc_path = Path("/tmp/spike_doc.sbc")
    args_path = Path("/tmp/spike_args.json")
    doc_path.write_text(json.dumps(document, ensure_ascii=False,
                                   separators=(",", ":")) + "\n", encoding="utf-8")
    args_path.write_text("[]", encoding="utf-8")

    proc = subprocess.run(
        [str(ROOT / "native" / "osakavm"), "run", str(doc_path), str(args_path)],
        capture_output=True, text=True)
    if proc.returncode != 0 and not proc.stdout.strip():
        print("native VM failed:", proc.stderr)
        return 1
    native = json.loads(proc.stdout)

    ok = True
    if py["stdout"] != native["value"]["stdout"]:
        ok = False
        print("STDOUT MISMATCH")
        print("python:", repr(py["stdout"]))
        print("native:", repr(native["value"]["stdout"]))
    if py["warnings"] != native["value"]["warnings"]:
        ok = False
        print("WARNINGS MISMATCH")
        print("python:", py["warnings"])
        print("native:", native["value"]["warnings"])
    py_err = py["error"]
    nat_err = None
    if native["ok"] == 0:
        nat_err = native["diagnostic"]["message"]
    if py_err != nat_err:
        ok = False
        print("ERROR MISMATCH")
        print("python:", py_err)
        print("native:", nat_err)
    if ok:
        print("SPIKE PASS")
        print("stdout:", repr(py["stdout"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())