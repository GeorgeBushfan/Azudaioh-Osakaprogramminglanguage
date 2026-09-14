"""Differential tests: Stage 0 Python VM vs the Gate E native VM.

Each case compiles a small guest program once, then executes the same SBC1
document on (a) the Python VM and (b) the native VM (native/osakavm, the
transpiled selfhost/vm.saka). Observable behavior per docs/SELF_HOSTING.md
"Runtime equivalence" must match: stdout, warnings, and unhandled errors.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lexer import lex
from parser import Parser
from compiler import Compiler
from sbc import program_to_dict
from tests.test_selfhost_vm import compile_document, run_on_python_vm

ROOT = Path(__file__).resolve().parents[1]
OSAKAVM = ROOT / "native" / "osakavm"


def _compile_source_to_document(text):
    """Stage 0 front end: source text -> canonical SBC1 document dict."""
    return program_to_dict(
        Compiler().compile_program(Parser(lex(text)).parse()))


def _collect_saka_imports(document):
    """Return the .saka import-path strings referenced by a document's
    constant pools (runtime file-module imports)."""
    found = []
    pools = [document.get("constants", [])]
    for fn in document.get("functions", {}).values():
        pools.append(fn.get("constants", []))
    for pool in pools:
        for rec in pool:
            data = rec.get("data")
            if isinstance(data, str) and data.endswith(".saka"):
                if data not in found:
                    found.append(data)
    return found


def _ensure_module_artifacts(document, base_dir, _seen=None):
    """Compile .saka module sources to .sbc artifacts next to them so the
    native VM (which loads compiled artifacts, unlike the Python VM that
    compiles source on the fly) can satisfy runtime imports. Recurses into
    the modules' own imports. Mirrors vm.py's path resolution."""
    if _seen is None:
        _seen = set()
    for raw in _collect_saka_imports(document):
        path = Path(raw)
        if path.is_absolute():
            target = path
        else:
            stripped = raw[2:] if raw.startswith("./") else raw
            target = base_dir / stripped
        artifact = target.with_suffix(".sbc")
        key = str(artifact)
        if key in _seen:
            continue
        _seen.add(key)
        if (not artifact.exists()
                or artifact.stat().st_mtime < target.stat().st_mtime):
            module_doc = _compile_source_to_document(
                target.read_text(encoding="utf-8"))
            artifact.write_text(
                json.dumps(module_doc, ensure_ascii=False,
                           separators=(",", ":")) + "\n",
                encoding="utf-8")
        else:
            module_doc = json.loads(artifact.read_text(encoding="utf-8"))
        _ensure_module_artifacts(module_doc, target.parent, _seen)


def run_on_native_vm(document, argv=(), source_path=None):
    """Run the SBC1 document on the native VM; return the same envelope
    shape as run_on_python_vm.

    When source_path is given (the .saka the document was compiled from),
    the doc is staged in the source's directory so runtime file-module
    imports resolve exactly like the Python VM's (relative to the source),
    and module .sbc artifacts are compiled as needed."""
    if source_path is not None:
        base_dir = Path(source_path).resolve().parent
        _ensure_module_artifacts(document, base_dir)
        doc_path = base_dir / (".native_tmp_%d.sbc" % os.getpid())
    else:
        doc_path = Path("/tmp/native_test_doc.sbc")
    args_path = Path("/tmp/native_test_args.json")
    doc_path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")
    args_path.write_text(json.dumps(list(argv)), encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(OSAKAVM), "run", str(doc_path), str(args_path)],
            capture_output=True, text=True)
    finally:
        if source_path is not None:
            doc_path.unlink(missing_ok=True)
    if not proc.stdout.strip():
        raise RuntimeError("native VM produced no envelope: %s" % proc.stderr)
    envelope = json.loads(proc.stdout)
    error = None
    if envelope["ok"] == 0:
        error = envelope["diagnostic"]["message"]
    return {
        "stdout": envelope["value"]["stdout"],
        "warnings": list(envelope["value"]["warnings"]),
        "error": error,
    }


class NativeVMDifferential(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not OSAKAVM.exists():
            raise RuntimeError("native/osakavm not built (run make in native/)")

    def _check(self, source, argv=()):
        document = compile_document(source)
        py = run_on_python_vm(document, argv)
        native = run_on_native_vm(document, argv)
        self.assertEqual(py["stdout"], native["stdout"], "stdout mismatch")
        self.assertEqual(py["warnings"], native["warnings"], "warnings mismatch")
        self.assertEqual(py["error"], native["error"], "error mismatch")

    # ---------- basics ----------

    def test_arithmetic_and_say(self):
        self._check(
            """
            truthaboutgrain x = 6 * 7 + 1;
            Say(x);
            Say(x - 1);
            truthaboutgrain y = 10 % 3;
            Say(y);
            """
        )

    def test_string_concat_and_slices(self):
        self._check(
            """
            truthaboutgrain greeting = "hello" + " " + "osaka";
            Say(greeting);
            Say(slice(greeting, 0, 5));
            Say(len(greeting));
            """
        )

    def test_floats_print(self):
        self._check(
            """
            truthaboutgrain half = 1 / 2;
            Say(half);
            Say(half + 0.25);
            """
        )

    def test_lists(self):
        self._check(
            """
            truthaboutgrain xs = [1, 2, 3];
            Say(xs);
            xs[1] = 42;
            Say(xs[1]);
            Say(xs);
            push(xs, 4);
            Say(pop(xs));
            Say(len(xs));
            """
        )

    def test_maps(self):
        self._check(
            """
            truthaboutgrain m = {"a": 1, "b": 2};
            Say(m);
            Say(m["a"]);
            m["c"] = 3;
            Say(contains(m, "c"));
            Say(keys(m));
            Say(values(m));
            """
        )

    def test_control_flow(self):
        self._check(
            """
            truthaboutgrain total = 0;
            truthaboutgrain i = 0;
            while i < 10 {
                i = i + 1;
                if i == 3 {
                    continue;
                }
                if i == 8 {
                    break;
                }
                total = total + i;
            }
            Say(total);
            if total > 20 {
                Say("big");
            } else {
                Say("small");
            }
            """
        )

    def test_functions_and_recursion(self):
        self._check(
            """
            func fact(n) {
                if n <= 1 {
                    return 1;
                }
                return n * fact(n - 1);
            }
            func add(a, b) {
                return a + b;
            }
            Say(fact(6));
            Say(add(fact(3), 4));
            """
        )

    def test_scoping_blocks(self):
        self._check(
            """
            truthaboutgrain x = 1;
            if 1 == 1 {
                truthaboutgrain x = 2;
                Say(x);
            }
            Say(x);
            while x < 3 {
                truthaboutgrain inner = x * 10;
                Say(inner);
                x = x + 1;
            }
            """
        )

    def test_grain_kind_and_say_warning(self):
        self._check(
            """
            grainsoftruth mystery = 5 / 2;
            Say(mystery);
            """
        )

    def test_condition_kind_error_caught(self):
        self._check(
            """
            try {
                grainsoftruth weird = 0 / 1;
                if weird == 0 {
                    Say("never");
                }
            } catch {
                Say("caught condition kind error");
            }
            Say("after");
            """
        )

    def test_panic_caught(self):
        self._check(
            """
            try {
                Panic("bad thing");
            } catch {
                Say("caught panic");
            }
            Say("continues");
            """
        )

    def test_panic_uncaught(self):
        self._check('Panic("boom");')

    def test_division_by_zero_uncaught(self):
        self._check(
            """
            truthaboutgrain x = 5;
            Say(x / 0);
            """
        )

    def test_undefined_variable_defaults_zero(self):
        self._check(
            """
            Say(ghost);
            """
        )

    def test_policy_ah_hecho(self):
        self._check(
            """
            truthaboutgrain counter = 0;
            counter = counter + 1;
            Ah(counter);
            counter = counter + 1;
            Hecho(counter);
            Say(counter);
            """
        )

    def test_policy_hecho_freeze_warning(self):
        self._check(
            """
            truthaboutgrain frozen = 1;
            Hecho(frozen);
            frozen = 2;
            Say(frozen);
            """
        )

    def test_policy_mutation_warning(self):
        self._check(
            """
            truthaboutgrain tracked = 1;
            tracked = 2;
            tracked = 3;
            Say(tracked);
            """
        )

    def test_policy_legacy(self):
        self._check(
            """
            truthaboutgrain old = 1;
            Ivebeengot(old);
            old = 2;
            Say(old);
            """
        )

    def test_policy_assumed(self):
        self._check(
            """
            truthaboutgrain assumed_var = 1;
            assumed_var = 2;
            youknowsealsright(assumed_var);
            assumed_var = 3;
            Say(assumed_var);
            """
        )

    def test_std_module(self):
        self._check(
            """
            import std;
            truthaboutgrain xs = [10, 20, 30];
            Say(std.len(xs));
            push(xs, 40);
            Say(std.len(xs));
            Say(std.pop(xs));
            Say(std.slice(xs, 0, 2));
            Say(std.contains({"k": 1}, "k"));
            """
        )

    def test_uncaught_error_message_div_by_zero(self):
        document = compile_document("Say(1 / 0);")
        py = run_on_python_vm(document)
        native = run_on_native_vm(document)
        self.assertIsNotNone(py["error"])
        self.assertEqual(py["error"], native["error"])
        self.assertEqual(py["error"], "division by zero")

    def test_modulo_by_zero(self):
        self._check("Say(5 % 0);")

    def test_string_comparison_and_condition(self):
        self._check(
            """
            truthaboutgrain name = "osaka";
            if name == "osaka" {
                Say("matched");
            }
            if name < "zetasu" {
                Say("earlier");
            }
            """
        )

    def test_nested_function_calls_share_scopes(self):
        self._check(
            """
            truthaboutgrain base = 100;
            func inner(x) {
                truthaboutgrain scoped = x * 2;
                return scoped;
            }
            func outer(y) {
                truthaboutgrain a = inner(y);
                truthaboutgrain b = inner(y + 1);
                return a + b + base;
            }
            Say(outer(5));
            Say(base);
            """
        )

    def test_index_growth_with_nulls(self):
        self._check(
            """
            truthaboutgrain xs = [1];
            xs[3] = 9;
            Say(xs);
            Say(len(xs));
            """
        )

    def test_while_with_grain_condition_fails(self):
        self._check(
            """
            grainsoftruth flag = 1;
            while flag == 1 {
                Say("once");
                flag = 0;
            }
            """
        )

    def test_try_catch_scope_unwind(self):
        self._check(
            """
            func boom() {
                if 1 == 1 {
                    Panic("inside block");
                }
                return 0;
            }
            try {
                boom();
                Say("not reached");
            } catch {
                Say("caught");
            }
            Say("done");
            """
        )

    def test_ohmygah_warning(self):
        self._check('Ohmygah("soft failure");')


if __name__ == "__main__":
    unittest.main()