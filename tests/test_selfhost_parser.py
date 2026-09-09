"""Gate B: differential parser conformance between Stage 0 and the Osaka parser.

The Osaka parser (selfhost/parser.saka + selfhost/lower.saka) must produce
canonical tagged-map AST records identical to frontend_contract.canonical_ast
over the tracked corpus, and identical normalized diagnostics on malformed
input. Osaka has no null/boolean literals yet, so Stage 0 records are
normalized (None->0, True->1, False->0) before comparison - the same
convention established for EOF token values in the lexer gate.
"""
import glob
import json
import unittest
from pathlib import Path

from frontend_contract import canonical_ast, canonical_diagnostic, canonical_tokens
from lexer import lex
from parser import Parser
from selfhost_harness import invoke as invoke_selfhost


ORDER = (
    "support.saka", "diagnostic.saka", "token.saka", "ast.saka",
    "lexer.saka", "lower.saka", "parser.saka",
)


def to_osaka(node):
    """Normalize Stage 0 canonical records to Osaka value conventions."""
    if node is None:
        return 0
    if node is True:
        return 1
    if node is False:
        return 0
    if isinstance(node, list):
        return [to_osaka(item) for item in node]
    if isinstance(node, dict):
        return {key: to_osaka(value) for key, value in node.items()}
    return node


def stage0_ast(source):
    return to_osaka(canonical_ast(Parser(lex(source)).parse()))


def osaka_parse(source):
    return invoke_selfhost("parser_parse_program", [source], ORDER)


def osaka_ast(source):
    result = osaka_parse(source)
    if result["ok"] != 1:
        raise AssertionError(f"Osaka parser failed: {result['diagnostic']}")
    return result["value"]


def tracked_sources():
    files = []
    for pattern in ("tests/equivalence/*.saka", "tests/Stage3/*.saka", "examples/*.saka", "tests/*.saka"):
        files.extend(sorted(glob.glob(pattern)))
    return files


INLINE_SOURCES = (
    'truthaboutgrain x = "a\\n"; Say(x);',
    'func f(a, b) { if a <= b and not a == 0 { return [1, 2.5]; } }',
    'for (i = 0; i < 3; i = i + 1) { if i == 1 { continue } Say(i); }',
    'import std; x = {"key": 1, "other": [2, 3]}; y = x["key"];',
    'try { risky() } catch { Say("caught") }',
    'export truthaboutgrain greeting = "hi";',
    'truthaboutgrain m = {"a": 1}; m["b"] = 2; while m["a"] < 5 { m["a"] = m["a"] + 1; }',
    'Ah(x); Hecho(y); Ohmygah(z); youknowsealsright(w); Ivebeengot(v);',
    'Escalator temp; Elevator done; Getittogether();',
    'if 1 == 1 { Say("one") } else if 2 == 2 { Say("two") } else { Say("three") }',
    'x = Math.abs(-3) + Math.PI; y = m.k; z = list[0][1];',
    'func outer() { while x < 10 { x = x + 1; } return x }',
    'import "./modules/mod_values.saka" as mod; Say(mod.add2(2, 3));',
    'export func add2(a, b) { return a + b; }',
    'x = [1, [2, 3], "four"];',
    'a = 1; b = 2; c = a < b and not (b == 2 or a >= 1);',
    'while x < 3 { if x == 1 { x = x + 1; continue; } break; }',
    'func empty_params() { return 0 }',
    'truthaboutgrain nested = {"outer": {"inner": [1, {"deep": 2}]}};',
    'func f(a,) { return 1 }',
)

NEGATIVE_SOURCES = (
    "if 1 == 1 {",
    "x = (1",
    "m = {1: 2}",
    "Ah x",
    "Hecho(x;",
    "func (",
    "x = - +",
    "truthaboutgrain = 5;",
    "for (i = 0; i < 3; i = i + 1 {",
    "import as;",
    "x = [1, 2",
    "func empty_params() { return; }",
    "x = [1, [2, 3], \"four\"][1][0];",
    "x = y[0] = 5;",
)


class TestSelfhostParserGateB(unittest.TestCase):
    maxDiff = None

    def test_tracked_corpus_matches_stage0(self):
        files = tracked_sources()
        self.assertGreaterEqual(len(files), 25)
        for path in files:
            with self.subTest(path=path), open(path, encoding="utf-8") as handle:
                source = handle.read()
            # Some tracked files use syntax Stage 0 itself rejects; for those
            # the comparison target is the normalized Stage 0 diagnostic.
            try:
                Parser(lex(source)).parse()
            except SyntaxError as error:
                expected = to_osaka(canonical_diagnostic(error, source))
                result = osaka_parse(source)
                self.assertEqual(result["ok"], 0, path)
                self.assertEqual(result["diagnostic"], expected, path)
            else:
                self.assertEqual(osaka_ast(source), stage0_ast(source), path)

    def test_inline_feature_sources_match_stage0(self):
        for source in INLINE_SOURCES:
            with self.subTest(source=source):
                self.assertEqual(osaka_ast(source), stage0_ast(source))

    def test_negative_sources_match_stage0_diagnostics(self):
        for source in NEGATIVE_SOURCES:
            with self.subTest(source=source):
                # Stage 0 must fail for every negative fixture.
                try:
                    Parser(lex(source)).parse()
                except SyntaxError as error:
                    expected = to_osaka(canonical_diagnostic(error, source))
                else:
                    self.fail("negative source unexpectedly parsed by Stage 0")
                result = osaka_parse(source)
                self.assertEqual(result["ok"], 0, source)
                self.assertEqual(result["diagnostic"], expected, source)

    def test_lexical_failures_surface_through_parser_entry(self):
        source = "x = @"
        # The Stage 0 lexer rejects this before parsing begins.
        with self.assertRaises(Exception):
            canonical_tokens(source)
        result = osaka_parse(source)
        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["diagnostic"]["code"], "LEX_UNEXPECTED_CHAR")
        self.assertEqual(result["diagnostic"]["span"]["start"]["offset"], 4)

    def test_golden_corpus_is_stable(self):
        golden_dir = Path("tests/selfhost/parser/golden")
        golden_files = sorted(golden_dir.glob("*.saka"))
        self.assertGreaterEqual(len(golden_files), 2)
        for path in golden_files:
            with self.subTest(path=path.as_posix()):
                source = path.read_text(encoding="utf-8")
                expected = json.loads(
                    path.with_suffix(".golden.json").read_text(encoding="utf-8")
                )
                self.assertEqual(osaka_ast(source), expected)


if __name__ == "__main__":
    unittest.main()