"""Differential tests: Stage 0 Python VM vs the self-hosted Osaka VM.

Each case compiles a small guest program once, then executes the same SBC1
document on (a) the Python VM and (b) the self-hosted VM (selfhost/vm.saka,
running on the Python VM). Observable behavior per docs/SELF_HOSTING.md
"Runtime equivalence" must match: stdout, warnings, and unhandled errors
(error message text; traces and internal state are not semantics).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bootstrap_profile import validate_bootstrap_ast
from compiler import Compiler
from runtime import Runtime
from sbc import program_from_dict, program_to_dict
from selfhost_bundle import bundle_ast
from vm import VM


_BUNDLE_COMPILED = None


def bundle_functions():
    """Compile the bootstrap bundle once and return its function table."""
    global _BUNDLE_COMPILED
    if _BUNDLE_COMPILED is None:
        ast = bundle_ast("selfhost")
        validate_bootstrap_ast(ast)
        _BUNDLE_COMPILED = Compiler().compile_program(ast).functions
    return _BUNDLE_COMPILED


def run_on_python_vm(document, argv=()):
    """Run the SBC1 document on the Stage 0 Python VM."""
    from bytecode import BytecodeProgram

    program = program_from_dict(document)
    runtime = Runtime()
    runtime.program_args = list(argv)
    vm = VM(runtime)
    error = None
    try:
        vm.run(program)
    except Exception as exc:  # unhandled runtime error, like run_vm.py
        error = str(exc)
    return {
        "stdout": runtime.stdout.getvalue(),
        "warnings": list(runtime.warnings),
        "error": error,
    }


def run_on_osaka_vm(document, argv=()):
    """Run the SBC1 document on the self-hosted VM (vm.saka)."""
    from bytecode import BytecodeProgram, BytecodeProgram as _BP
    from bytecode import Value, PUSH_CONST, CALL_FUNC, HALT
    from verifier import verify_program

    functions = bundle_functions()
    consts = [
        Value(document, "truth"),
        Value(list(argv), "truth"),
    ]
    code = [
        (PUSH_CONST, 0, -1),
        (PUSH_CONST, 1, -1),
        (CALL_FUNC, ("vm_run", 2), -1),
        (HALT, None, -1),
    ]
    program = BytecodeProgram(consts=consts, code=code, functions=dict(functions))
    verify_program(program)
    runtime = Runtime()
    vm = VM(runtime)
    vm.run(program)
    frame = vm.frames[-1] if vm.frames else None
    envelope = frame.stack[-1].data if frame and frame.stack else None
    if envelope is None:
        raise RuntimeError("self-hosted VM returned no envelope")
    error = None
    if envelope["ok"] == 0:
        error = envelope["diagnostic"]["message"]
    return {
        "stdout": envelope["value"]["stdout"],
        "warnings": list(envelope["value"]["warnings"]),
        "error": error,
    }


def compile_document(source):
    """Compile guest source with the Stage 0 compiler to the SBC1 doc dict."""
    from lexer import lex
    from parser import Parser

    ast = Parser(lex(source)).parse()
    program = Compiler().compile_program(ast)
    return program_to_dict(program)


class SelfHostVMDifferential(unittest.TestCase):
    maxDiff = None

    def _check(self, source, argv=()):
        document = compile_document(source)
        py = run_on_python_vm(document, argv)
        saka = run_on_osaka_vm(document, argv)
        self.assertEqual(py["stdout"], saka["stdout"], "stdout mismatch")
        self.assertEqual(py["warnings"], saka["warnings"], "warnings mismatch")
        self.assertEqual(py["error"], saka["error"], "error mismatch")

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
        saka = run_on_osaka_vm(document)
        self.assertIsNotNone(py["error"])
        self.assertEqual(py["error"], saka["error"])
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