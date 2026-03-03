import sys
import os
import json
from pathlib import Path
from runtime import Runtime
from interpreter import Interpreter
from compiler import Compiler
from vm import VM
from parser import Parser
from lexer import lex


def enforce_equivalence_lock(ast_traces, vm_traces, strict=False):
    """Lightweight lock used by saka.py CLI.

    In non-strict mode, report differences but do not raise.
    In strict mode, raise RuntimeError on first mismatch summary.
    """
    failures = []

    if len(ast_traces) != len(vm_traces):
        failures.append(
            f"Trace count mismatch: AST={len(ast_traces)} VM={len(vm_traces)}"
        )

    for i, (a, v) in enumerate(zip(ast_traces, vm_traces)):
        if a.get("line") != v.get("line"):
            failures.append(
                f"Trace {i} line mismatch: AST={a.get('line')} VM={v.get('line')}"
            )

    if failures:
        message = "Equivalence lock mismatch:\n" + "\n".join(failures[:10])
        if strict:
            raise RuntimeError(message)
        print(message)
        return False

    return True

def run_ast_interpreter(source_path):
    """Run the AST interpreter and return its runtime state."""
    with open(source_path, 'r') as f:
        source = f.read()
    
    tokens = lex(source)
    parser = Parser(tokens)
    program = parser.parse()
    
    rt = Runtime()
    rt.current_file = str(Path(source_path).resolve())
    interpreter = Interpreter(rt)
    interpreter.run(program)
    
    return {
        "traces": rt.execution_traces,
        "stdout": rt.stdout.getvalue(),
        "stderr": rt.stderr.getvalue(),
        "warnings": rt.warnings
    }

def run_bytecode_vm(source_path):
    """Run the bytecode VM and return its runtime state."""
    with open(source_path, 'r') as f:
        source = f.read()
    
    tokens = lex(source)
    parser = Parser(tokens)
    program = parser.parse()
    compiler = Compiler()
    bytecode = compiler.compile(program)
    
    rt = Runtime()
    rt.current_file = str(Path(source_path).resolve())
    vm = VM(rt)
    vm.run(bytecode)
    
    return {
        "traces": rt.execution_traces,
        "stdout": rt.stdout.getvalue(),
        "stderr": rt.stderr.getvalue(),
        "warnings": rt.warnings,
        "errors": rt.errors
    }

def compare_results(ast_result, vm_result, source_path):
    """Compare AST interpreter and VM results, reporting any differences."""
    failures = []
    
    # Compare stdout
    if ast_result["stdout"] != vm_result["stdout"]:
        failures.append(f"STDOUT mismatch in {source_path}")
        failures.append(f"  AST: {ast_result['stdout']}")
        failures.append(f"  VM:  {vm_result['stdout']}")
    
    # Compare stderr
    if ast_result["stderr"] != vm_result["stderr"]:
        failures.append(f"STDERR mismatch in {source_path}")
        failures.append(f"  AST: {ast_result['stderr']}")
        failures.append(f"  VM:  {vm_result['stderr']}")
    
    # Compare warnings
    if ast_result["warnings"] != vm_result["warnings"]:
        failures.append(f"Warnings mismatch in {source_path}")
        failures.append(f"  AST: {ast_result['warnings']}")
        failures.append(f"  VM:  {vm_result['warnings']}")
    
    # Compare execution traces
    ast_traces = ast_result["traces"]
    vm_traces = vm_result["traces"]
    
    if len(ast_traces) != len(vm_traces):
        failures.append(f"Trace count mismatch: AST={len(ast_traces)} VM={len(vm_traces)}")
    
    for i, (ast_trace, vm_trace) in enumerate(zip(ast_traces, vm_traces)):
        # Compare line numbers
        if ast_trace.get("line") != vm_trace.get("line"):
            failures.append(f"Trace {i} line mismatch: AST={ast_trace.get('line')} VM={vm_trace.get('line')}")
        
        # Compare variables
        # Compare variables if both traces have variables
        if "variables" in ast_trace and "variables" in vm_trace:
            ast_vars = ast_trace.get("variables", {})
            vm_vars = vm_trace.get("variables", {})
            
            if ast_vars.keys() != vm_vars.keys():
                missing_in_vm = set(ast_vars.keys()) - set(vm_vars.keys())
                missing_in_ast = set(vm_vars.keys()) - set(ast_vars.keys())
                if missing_in_vm:
                    failures.append(f"Trace {i}: Variables missing in VM: {', '.join(missing_in_vm)}")
                if missing_in_ast:
                    failures.append(f"Trace {i}: Variables missing in AST: {', '.join(missing_in_ast)}")
            
            for var in ast_vars:
                if var in vm_vars:
                    ast_val = ast_vars[var]
                    vm_val = vm_vars[var]
                    
                    # Compare values
                    if ast_val != vm_val:
                        failures.append(f"Trace {i} variable '{var}' value mismatch")
                        failures.append(f"  AST: {ast_val}")
                        failures.append(f"  VM:  {vm_val}")
    
    return failures

def run_equivalence_tests(test_files):
    """Run equivalence tests on all specified files."""
    all_failures = []
    
    for test_file in test_files:
        print(f"Testing {test_file}...")
        
        ast_result = run_ast_interpreter(test_file)
        vm_result = run_bytecode_vm(test_file)
        
        failures = compare_results(ast_result, vm_result, test_file)
        
        if failures:
            print(f"❌ FAIL: {len(failures)} differences found")
            all_failures.extend(failures)
        else:
            print("✅ PASS: AST and VM outputs match exactly")
    
    return all_failures

def main():
    # Get test files from command line or use default
    if len(sys.argv) > 1:
        test_files = sys.argv[1:]
    else:
        # Default to equivalence tests
        test_files = [
            "tests/equivalence/01_variable_assignment.saka",
            "tests/equivalence/02_control_flow.saka",
            "tests/equivalence/03_function_calls.saka",
            "tests/equivalence/04_error_handling.saka",
            "tests/equivalence/05_io_operations.saka"
        ]
    
    failures = run_equivalence_tests(test_files)
    
    if failures:
        print("\nEQUIVALENCE FAILURES DETECTED:")
        for failure in failures:
            print(failure)
        sys.exit(1)
    else:
        print("\nALL EQUIVALENCE TESTS PASSED!")
        sys.exit(0)

if __name__ == "__main__":
    main()