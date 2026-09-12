"""Gate E step E3b: conformance corpus on the native VM.

Compiles every corpus program with the Stage 0 compiler, then executes the
same SBC1 document on the Python VM and the NATIVE VM (native/osakavm, the
transpiled selfhost/vm.saka). A program passes when stdout, warnings, and
unhandled error text match exactly (the tests/test_native_vm.py contract;
Gate D already established Osaka VM parity).

Corpus: tests/*.saka, tests/equivalence/*.saka, tests/Stage3/*.saka.
Writes bootstrap/GATE_E_CONFORMANCE.json and prints a human-readable table.

Usage: python3 run_conformance_e.py [--only SUBSTRING]
"""

import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lexer import lex
from parser import Parser
from compiler import Compiler
from sbc import program_to_dict
from tests.test_selfhost_vm import run_on_python_vm
from tests.test_native_vm import run_on_native_vm

ROOT = Path(__file__).resolve().parent
CORPUS_DIRS = (ROOT / "tests", ROOT / "tests" / "equivalence",
               ROOT / "tests" / "Stage3")
REPORT = ROOT / "bootstrap" / "GATE_E_CONFORMANCE.json"


def corpus_files():
    files = []
    for directory in CORPUS_DIRS:
        files.extend(sorted(directory.glob("*.saka")))
    # Deterministic order: relative path string.
    return sorted(files, key=lambda p: str(p.relative_to(ROOT)))


def run_one(path):
    """Returns a result dict for one corpus program."""
    rel = str(path.relative_to(ROOT))
    src = path.read_text(encoding="utf-8")
    record = {"program": rel, "status": "pass", "reason": ""}
    started = time.time()
    try:
        document = program_to_dict(
            Compiler().compile_program(Parser(lex(src)).parse()))
    except Exception as exc:
        record["status"] = "compile-error"
        record["reason"] = f"{type(exc).__name__}: {exc}"
        return record
    # Suppress host console noise ([Info]/[Warning] from bundle execution):
    # program output goes to rt.stdout buffers, console output is not part
    # of the compared contract.
    noise = io.StringIO()
    try:
        with contextlib.redirect_stdout(noise):
            py = run_on_python_vm(document, source_path=str(path))
    except Exception as exc:
        record["status"] = "harness-error"
        record["reason"] = f"python vm: {type(exc).__name__}: {exc}"
        return record
    try:
        with contextlib.redirect_stdout(noise):
            native = run_on_native_vm(document)
    except Exception as exc:
        record["status"] = "harness-error"
        record["reason"] = f"native vm: {type(exc).__name__}: {exc}"
        return record
    record["elapsed_s"] = round(time.time() - started, 1)
    if py["stdout"] != native["stdout"]:
        record["status"] = "fail"
        py_lines = py["stdout"].splitlines()
        native_lines = native["stdout"].splitlines()
        for i in range(max(len(py_lines), len(native_lines))):
            a = py_lines[i] if i < len(py_lines) else "<EOF>"
            b = native_lines[i] if i < len(native_lines) else "<EOF>"
            if a != b:
                record["reason"] = f"stdout line {i + 1}: py={a!r} native={b!r}"
                break
        return record
    if py["warnings"] != native["warnings"]:
        record["status"] = "fail"
        record["reason"] = (f"warnings differ: py={py['warnings']!r} "
                            f"native={native['warnings']!r}")
        return record
    if py["error"] != native["error"]:
        record["status"] = "fail"
        record["reason"] = (f"error differs: py={py['error']!r} "
                            f"native={native['error']!r}")
    return record


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    files = corpus_files()
    if only:
        files = [f for f in files if only in str(f)]
    print(f"Gate E conformance inventory: {len(files)} programs\n")
    results = []
    counts = {}
    for path in files:
        record = run_one(path)
        results.append(record)
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        mark = {"pass": "PASS", "fail": "FAIL"}.get(record["status"],
                                                    record["status"].upper())
        line = f"[{mark:14s}] {record['program']}"
        if record["status"] != "pass":
            line += f"  -- {record['reason'][:160]}"
        if "elapsed_s" in record:
            line += f"  ({record['elapsed_s']}s)"
        print(line, flush=True)
    print(f"\nSummary: {json.dumps(counts)}")
    REPORT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"Report written to {REPORT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())