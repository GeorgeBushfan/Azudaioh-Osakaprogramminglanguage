
import importlib.util
import pathlib
import sys


def _ensure_local_parser_module():
    """Ensure our local parser.py is used instead of stdlib parser extension."""
    if "parser" in sys.modules and getattr(sys.modules["parser"], "__file__", "").endswith("parser.py"):
        return

    here = pathlib.Path(__file__).resolve().parent
    parser_path = here / "parser.py"
    if not parser_path.exists():
        return

    spec = importlib.util.spec_from_file_location("parser", str(parser_path))
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["parser"] = module
        spec.loader.exec_module(module)


_ensure_local_parser_module()

from interpreter import Interpreter
from runtime import Runtime
from arg_parser import create_arg_parser
from equiv_lock import enforce_equivalence_lock
from equiv_test import parse_ast, run_interpreter, run_vm


def _print_program_output(output: str, show_empty_tip: bool = False):
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    elif show_empty_tip:
        print("Tip: Use --debug for traces")


def _run_repl(debug: bool = False, show_tokens: bool = False):
    print("Osaka REPL (type :help for commands, :quit to exit)")

    rt = Runtime()
    if debug:
        rt.debug = True
    interp = Interpreter(rt)

    buffer = ""

    while True:
        prompt = "osaka> " if not buffer else "...   "
        try:
            line = input(prompt)
        except EOFError:
            print()
            break

        stripped = line.strip()
        if not buffer and stripped in (":quit", ":exit"):
            break
        if not buffer and stripped == ":help":
            print(":help  Show this help")
            print(":quit  Exit REPL")
            print(":exit  Exit REPL")
            print("Enter Osaka statements; multi-line blocks are supported.")
            continue

        buffer = f"{buffer}\n{line}" if buffer else line

        try:
            ast = parse_ast(buffer, show_tokens=show_tokens, debug=debug)
        except SyntaxError as e:
            # Continue collecting lines if input looks incomplete.
            msg = str(e)
            if "Unexpected end of input" in msg:
                continue
            print(f"SyntaxError: {e}")
            buffer = ""
            continue
        except Exception as e:
            print(f"Error: {e}")
            buffer = ""
            continue

        before = rt.stdout.getvalue()
        try:
            interp.run(ast)
        except Exception as e:
            print(f"RuntimeError: {e}")
        after = rt.stdout.getvalue()
        delta = after[len(before):]
        _print_program_output(delta, show_empty_tip=False)
        buffer = ""


def main(argv=None):
    parser = create_arg_parser()
    args = parser.parse_args(argv)

    if args.repl:
        if args.vm_only:
            print("Error: --repl currently supports interpreter mode only (remove --vm-only).")
            return 1
        _run_repl(debug=args.debug, show_tokens=args.show_lexer_tokens)
        return 0

    if not args.source_file:
        print("Error: source_file is required unless --repl is used")
        return 1

    try:
        source = open(args.source_file).read()
    except FileNotFoundError:
        print(f"Error: File '{args.source_file}' not found")
        return 1

    # Parse AST
    ast = parse_ast(source, show_tokens=args.show_lexer_tokens, debug=args.debug)

    # Execute based on CLI flags
    if args.vm_only:
        result = run_vm(ast, debug=args.debug)
        _print_program_output(result.stdout, show_empty_tip=True)
        return 0
    elif args.interpreter_only:
        result = run_interpreter(ast, debug=args.debug)
        _print_program_output(result.stdout, show_empty_tip=True)
        return 0

    # Run equivalence check unless disabled
    if not args.no_lock:
        interp_result = run_interpreter(ast, debug=args.debug)
        vm_result = run_vm(ast, debug=args.debug)
        enforce_equivalence_lock(
            interp_result.traces,
            vm_result.traces,
            strict=args.lock_strict
        )

    # Handle debug flag from argparse
    debug_mode = args.debug

    # Execute normally with interpreter
    rt = Runtime()
    if debug_mode:
        print("Running interpreter with debug mode...")
        rt.debug = True
    Interpreter(rt).run(ast)

    _print_program_output(rt.stdout.getvalue(), show_empty_tip=True)

    # Print debug information if requested
    if debug_mode:
        print("\nExecution Traces:")
        for i, trace in enumerate(rt.execution_traces):
            func_name = trace.get('function_name', '<global>')
            expr = trace.get('expression', '')
            print(f"Trace {i}: Line {trace['line']}, Function: {func_name}")
            print(f"  Expression: {expr}")
            print(f"  Variables: {list(trace['variables'].keys())}")
        print(f"Total traces: {len(rt.execution_traces)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
