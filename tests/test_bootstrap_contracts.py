import json
import unittest

from bytecode import BytecodeProgram, HALT, JMP, JMP_IF_FALSE, PUSH_CONST, Value
from compiler import Compiler
from bootstrap_profile import validate_bootstrap_ast
from builtin_registry import BUILTIN_ARITIES
from frontend_contract import (
    FrontendDiagnostic, canonical_ast, canonical_diagnostic, canonical_tokens,
)
from lexer import decode_string_literal, lex
from parser import Parser
from runtime import Runtime
from sbc import FORMAT, LANGUAGE_VERSION, VERSION, dumps, loads, program_to_dict
from verifier import VerificationError, verify_program
from vm import VM
from saka import main as saka_main
from selfhost_bundle import DEFAULT_ORDER, bundle_ast
from selfhost_harness import invoke as invoke_selfhost


SELFHOST_BACKEND_ORDER = DEFAULT_ORDER[:9]


def _none_to_zero(node):
    """Map Python None (no else / missing arg) to Osaka's 0 sentinel."""
    if isinstance(node, dict):
        return {key: _none_to_zero(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_none_to_zero(value) for value in node]
    return 0 if node is None else node


def compile_source(source):
    return Compiler().compile_program(Parser(lex(source)).parse())


def run_source(source):
    runtime = Runtime()
    vm = VM(runtime)
    vm.run(compile_source(source))
    return runtime, vm


class TestBootstrapContracts(unittest.TestCase):
    def test_lexer_is_strict_and_decodes_supported_escapes(self):
        lexeme = lex(r'x = "a\n\t\"\\b"')[2][1]
        self.assertEqual(decode_string_literal(lexeme), 'a\n\t"\\b')
        with self.assertRaises(SyntaxError):
            lex('x = "unterminated')
        with self.assertRaises(SyntaxError):
            lex('x = @')

    def test_calls_bind_arguments_left_to_right(self):
        runtime, _ = run_source('''
func difference(a, b) { return a - b }
result = difference(10, 3)
Say(result)
''')
        self.assertEqual(runtime.stdout.getvalue(), "7\n")

    def test_control_flow_rejects_grain_condition(self):
        with self.assertRaisesRegex(RuntimeError, "must be truthaboutgrain"):
            run_source('grainsoftruth maybe = 1.5; if maybe { Say("bad"); }')

    def test_statement_calls_leave_main_stack_balanced(self):
        _, vm = run_source('func identity(x) { return x } identity(7); Say("ok");')
        self.assertEqual(vm.stack, [])

    def test_break_and_return_unwind_runtime_scopes(self):
        runtime, vm = run_source('''
func early() {
    while 1 == 1 {
        return 9
    }
}
i = 0
while i < 3 {
    if i == 1 { break }
    i = i + 1
}
result = early()
''')
        self.assertEqual(len(vm.rt.scopes), 1)
        self.assertEqual(vm.scopes[0]["result"].data, 9)

    def test_sbc_round_trip_is_deterministic(self):
        program = compile_source('func difference(a, b) { return a - b } result = difference(10, 3)')
        first = dumps(program)
        second = dumps(loads(first))
        self.assertEqual(first, second)
        document = json.loads(first)
        self.assertEqual(document["format"], FORMAT)
        self.assertEqual(document["version"], VERSION)
        self.assertEqual(document["language_version"], LANGUAGE_VERSION)
        self.assertIn("constants", document)
        self.assertIn("main", document)
        self.assertNotIn("magic", document)
        self.assertNotIn("code", document)
        self.assertTrue(verify_program(loads(first)))

    def test_canonical_frontend_records_are_implementation_neutral(self):
        source = 'truthaboutgrain x = "a\\n";'
        tokens = canonical_tokens(source)
        self.assertEqual(tokens[0]["kind"], "TRUTHABOUTGRAIN")
        self.assertEqual(tokens[3]["value"], "a\n")
        self.assertEqual(tokens[-1]["kind"], "EOF")
        self.assertEqual(tokens[0]["span"]["start"], {"column": 1, "line": 1, "offset": 0})

        ast = canonical_ast(Parser(lex(source)).parse())
        self.assertEqual(ast[0]["tag"], "assign")
        self.assertEqual(ast[0]["decl_kind"], "truth")
        self.assertEqual(ast[0]["expr"], {"line": 1, "tag": "string", "value": "a\n"})

    def test_canonical_lexer_failure_has_stable_code_and_span(self):
        with self.assertRaises(FrontendDiagnostic) as captured:
            canonical_tokens("x = @")
        record = captured.exception.record
        self.assertEqual(record["code"], "LEX_UNEXPECTED_CHAR")
        self.assertEqual(record["phase"], "lexer")
        self.assertEqual(record["span"]["start"], {"column": 5, "line": 1, "offset": 4})

    def test_canonical_parser_failure_has_stable_code_and_span(self):
        source = "if 1 == 1 {"
        try:
            Parser(lex(source)).parse()
        except SyntaxError as error:
            record = canonical_diagnostic(error, source)
        else:
            self.fail("malformed source unexpectedly parsed")
        self.assertEqual(record["code"], "PARSE_UNEXPECTED_EOF")
        self.assertEqual(record["phase"], "parser")
        self.assertEqual(record["found"], "EOF")
        self.assertEqual(record["span"]["start"]["offset"], len(source))

    def test_verifier_rejects_inconsistent_join_height(self):
        program = BytecodeProgram(
            consts=[Value(True, "truth"), Value(1, "truth")],
            code=[
                (PUSH_CONST, 0, 1),
                (JMP_IF_FALSE, 4, 1),
                (PUSH_CONST, 1, 2),
                (JMP, 4, 2),
                (HALT, None, 3),
            ],
        )
        with self.assertRaisesRegex(VerificationError, "Inconsistent stack height"):
            verify_program(program)

    def test_args_and_panic_match_between_frontends(self):
        ast = Parser(lex("truthaboutgrain a = Args(); Say(a);" )).parse()
        interp = __import__("equiv_test").run_interpreter(ast, program_args=["in.saka", "out.sbc"])
        vm = __import__("equiv_test").run_vm(ast, program_args=["in.saka", "out.sbc"])
        self.assertEqual(interp.stdout, "[in.saka, out.sbc]\n")
        self.assertEqual(interp.stdout, vm.stdout)

        panic_ast = Parser(lex('Panic("fatal");')).parse()
        self.assertEqual(
            __import__("equiv_test").run_interpreter(panic_ast).error,
            __import__("equiv_test").run_vm(panic_ast).error,
        )

    def test_builtin_registry_is_enforced_by_compiler_and_verifier(self):
        self.assertEqual(BUILTIN_ARITIES["Args"], 0)
        with self.assertRaisesRegex(RuntimeError, "Say.*expects 1"):
            compile_source("Say();")
        malformed = BytecodeProgram(
            consts=[],
            code=[("CALL_BUILTIN", ("NoSuchBuiltin", 0), 1), ("POP", None, 1), (HALT, None, -1)],
        )
        with self.assertRaisesRegex(VerificationError, "Unknown built-in"):
            verify_program(malformed)

    def test_bootstrap_profile_rejects_dynamic_modules(self):
        self.assertTrue(validate_bootstrap_ast(Parser(lex("x = Args(); Say(x);")).parse()))
        with self.assertRaisesRegex(RuntimeError, "Import"):
            validate_bootstrap_ast(Parser(lex("import std;")).parse())

    def test_cli_argument_separator_is_not_consumed_as_cli_input(self):
        import contextlib
        import io
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".saka", text=True)
        try:
            os.write(fd, b"truthaboutgrain a = Args(); Say(a);")
            os.close(fd)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = saka_main(["--vm-only", path, "--", "alpha", "beta"])
            self.assertEqual(result, 0)
            self.assertIn("[alpha, beta]", output.getvalue())
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            os.unlink(path)

    def test_selfhost_foundation_modules_bundle_and_fit_profile(self):
        from pathlib import Path
        foundation = (
            "support.saka", "diagnostic.saka", "token.saka", "ast.saka",
            "lexer.saka", "lower.saka", "parser.saka",
        )
        ast = bundle_ast("selfhost", foundation)
        self.assertTrue(validate_bootstrap_ast(ast))
        names = [node.name for node in ast if hasattr(node, "name") and node.__class__.__name__ == "FunctionDef"]
        self.assertIn("token_make", names)
        self.assertIn("diag_make", names)
        self.assertIn("ast_function", names)
        self.assertTrue(all(Path("selfhost", name).is_file() for name in foundation))
        program = Compiler().compile_program(ast)
        self.assertTrue(verify_program(program))
        self.assertGreaterEqual(len(program.functions), 40)

    def test_selfhost_backend_matches_stage0_sbc1_and_runs(self):
        sources = (
            "x = 2 + 3 * 4; Say(x);",
            "grainsoftruth y = 2.5; y = y * 2; Say(y);",
            'i = 0; while i < 5 { if i == 2 { i = i + 1; continue; } Say(i); i = i + 1; }',
            "func add(a, b) { return a + b } func twice(n) { return add(n, n) } Say(twice(21));",
            "func early(n) { while 1 == 1 { return n } } Say(early(7));",
            'x = 1; if x == 1 { Say("one"); } else { Say("other"); }',
            'm = {"k": 1, "j": 2}; m["k"] = m["k"] + 10; Say(m["k"] + m["j"]);',
        )
        for source in sources:
            with self.subTest(source=source):
                stage0 = Compiler().compile_program(Parser(lex(source)).parse())
                expected = _none_to_zero(program_to_dict(stage0))
                canonical = _none_to_zero(canonical_ast(Parser(lex(source)).parse()))
                result = invoke_selfhost("compile_ir", [canonical], SELFHOST_BACKEND_ORDER)
                self.assertEqual(result["ok"], 1, result["diagnostic"])
                self.assertEqual(result["value"], expected)

                # The self-hosted document must load into the canonical SBC1
                # reader, verify, and execute identically to Stage 0 output.
                program_b = loads(json.dumps(result["value"]))
                self.assertTrue(verify_program(program_b))
                runtime_a = Runtime()
                VM(runtime_a).run(stage0)
                runtime_b = Runtime()
                VM(runtime_b).run(program_b)
                self.assertEqual(
                    runtime_b.stdout.getvalue(), runtime_a.stdout.getvalue()
                )

    def test_selfhost_verifier_matches_stage0_verdicts(self):
        order = DEFAULT_ORDER[:10]  # through verifier.saka

        def instr(op, arg=None):
            return {"op": op, "arg": arg, "line": -1}

        def doc(main, functions=None, consts=None):
            return {
                "constants": consts or [],
                "format": FORMAT,
                "functions": functions or {},
                "language_version": LANGUAGE_VERSION,
                "main": main,
                "version": VERSION,
            }

        positives = (
            "x = 2 + 3 * 4; Say(x);",
            "func add(a, b) { return a + b } Say(add(20, 1));",
            'm = {"k": 1}; Say(m["k"]);',
        )
        for source in positives:
            with self.subTest(source=source):
                stage0 = Compiler().compile_program(Parser(lex(source)).parse())
                self.assertTrue(verify_program(stage0))
                document = _none_to_zero(program_to_dict(stage0))
                result = invoke_selfhost("vf_verify", [document], order)
                self.assertEqual(result["ok"], 1, result["diagnostic"])

                # Self-verification: the Osaka compiler's own output must
                # pass the Osaka verifier as well.
                canonical = _none_to_zero(canonical_ast(Parser(lex(source)).parse()))
                osaka_doc = invoke_selfhost(
                    "compile_ir", [canonical], SELFHOST_BACKEND_ORDER
                )["value"]
                self.assertTrue(verify_program(loads(json.dumps(osaka_doc))))
                result = invoke_selfhost("vf_verify", [osaka_doc], order)
                self.assertEqual(result["ok"], 1, result["diagnostic"])

        # Malformed documents: verdicts (and error codes) must agree.
        negatives = {
            "unknown_builtin": doc([
                instr("CALL_BUILTIN", ["NoSuchBuiltin", 0]),
                instr("POP"), instr("HALT"),
            ]),
            "builtin_arity": doc([
                instr("CALL_BUILTIN", ["Say", 0]),
                instr("POP"), instr("HALT"),
            ]),
            "stack_underflow": doc([instr("POP"), instr("HALT")]),
            "ret_in_main": doc([instr("RET")]),
            "bad_jump_target": doc([instr("JMP", 99), instr("HALT")]),
            "bad_const_index": doc([instr("PUSH_CONST", 7), instr("HALT")]),
            "empty_block": doc([]),
            "no_halt": doc([instr("PUSH_CONST", 0)], consts=[{"data": 1, "kind": "truth"}]),
            "fn_no_ret": doc(
                [instr("HALT")],
                functions={"f": {"code": [instr("PUSH_CONST", 0)],
                                 "constants": [{"data": 0, "kind": "truth"}],
                                 "params": ["a"]}},
            ),
            "unknown_function": doc([
                instr("CALL_FUNC", ["ghost", 0]),
                instr("POP"), instr("HALT"),
            ]),
        }
        for name, document in negatives.items():
            with self.subTest(case=name):
                with self.assertRaises(VerificationError):
                    verify_program(loads(json.dumps(document)))
                result = invoke_selfhost("vf_verify", [document], order)
                self.assertEqual(result["ok"], 0)
                self.assertEqual(result["diagnostic"]["code"], "VERIFY_ERROR")

    def test_selfhost_emit_sbc_matches_stage0_bytes(self):
        order = DEFAULT_ORDER[:11]  # through emit_sbc.saka
        sources = (
            "x = 2 + 3 * 4; Say(x);",
            "grainsoftruth y = 2.5; y = y * 2; Say(y);",
            's = "quote\\" back\\\\slash \\n tab\\t"; Say(s);',
            "func add(a, b) { return a + b } func twice(n) { return add(n, n) } Say(twice(21));",
            "func ping() { return 7 } Say(ping());",
            'm = {"zeta": 1, "alpha": [1, 2.5, "x"]}; m["zeta"] = m["zeta"] + 10; Say(m["alpha"][1]);',
        )
        for source in sources:
            with self.subTest(source=source):
                document = _none_to_zero(program_to_dict(compile_source(source)))
                expected = dumps(loads(json.dumps(document)))
                actual = invoke_selfhost("em_dump", [document], order)
                self.assertEqual(actual, expected)
                # The emitted text must round-trip back to the same document.
                self.assertEqual(json.loads(actual), document)

    def test_selfhost_main_cli_pipeline_smoke(self):
        # Exercise main.saka's full CLI path on the VM (Args -> ReadFile loop
        # -> parse -> compile -> verify -> em_dump -> WriteFile) against a
        # tiny source root, so the expensive full-bundle gate stays a
        # standalone script (run_gate_c.py) while the entry point itself
        # stays covered by the fast suite.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tiny"
            root.mkdir()
            for index, name in enumerate(DEFAULT_ORDER):
                (root / name).write_text(
                    f"func smoke_{index}() {{ return {index} }}\n",
                    encoding="utf-8",
                )
            output = Path(tmp) / "out.sbc"
            # Run the real bundled program: its top-level mn_main() reads
            # Args(), so the CLI surface is exercised exactly as the gate does.
            program = Compiler().compile_program(bundle_ast("selfhost", DEFAULT_ORDER))
            runtime = Runtime()
            runtime.program_args = [str(root), str(output)]
            VM(runtime).run(program)
            self.assertIn("osakac: wrote", runtime.stdout.getvalue())

            text = output.read_text(encoding="utf-8")
            program = loads(text)
            self.assertTrue(verify_program(program))
            # Deterministic: re-serializing the loaded artifact is byte-stable.
            self.assertEqual(dumps(program), text)
            self.assertEqual(len(program.functions), 12)

    def test_selfhost_lexer_matches_canonical_stage0_records(self):
        order = ("support.saka", "diagnostic.saka", "token.saka", "lexer.saka")
        sources = (
            'truthaboutgrain x = "a\\n"; // note\r\nSay(x);',
            'func f(a, b) { if a <= b and not a == 0 { return [1, 2.5]; } }',
        )
        for source in sources:
            result = invoke_selfhost("lex_source", [source], order)
            self.assertEqual(result["ok"], 1)
            expected = canonical_tokens(source)
            expected[-1]["value"] = 0  # Osaka has no null literal yet.
            self.assertEqual(result["tokens"], expected)

        result = invoke_selfhost("lex_source", ["x = @"], order)
        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["diagnostic"]["code"], "LEX_UNEXPECTED_CHAR")
        self.assertEqual(result["diagnostic"]["span"]["start"]["offset"], 4)


if __name__ == "__main__":
    unittest.main()