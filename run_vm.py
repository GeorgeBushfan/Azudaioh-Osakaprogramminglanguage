# run_vm.py
from pathlib import Path
from lexer import lex
from parser import Parser
from runtime import Runtime

from compiler import Compiler
from vm import VM
from verifier import verify

def run_file(path: str, debug: bool = False):
    src = open(path, "r", encoding="utf-8").read()

    tokens = lex(src)
    ast = Parser(tokens).parse()

    rt = Runtime()
    rt.current_file = str(Path(path).resolve())

    # ensure Stage 6D runtime fields exist (if not already)
    rt.initialised = getattr(rt, "initialised", set())
    rt.acknowledged = getattr(rt, "acknowledged", set())
    rt.assumed = getattr(rt, "assumed", set())
    rt.legacy = getattr(rt, "legacy", set())
    rt.completed = getattr(rt, "completed", set())
    rt.var_kinds = getattr(rt, "var_kinds", {})

    bc = Compiler(debug=debug).compile(ast)
    verify(bc, Compiler.function_table)
    VM(rt, debug=debug).run(bc)

    if not debug:
        print(f"\nDiagnostics: {rt.shame} warning(s)")

if __name__ == "__main__":
    import sys
    debug = "--debug" in sys.argv
    file_arg = sys.argv[2] if debug and len(sys.argv) > 2 else sys.argv[1]
    run_file(file_arg, debug=debug)
