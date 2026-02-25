# run_tests.py

import subprocess
import sys
import os
from pathlib import Path
from interpreter import Interpreter
from vm import VM
from compiler import Compiler
import argparse
import equiv_lock  # Import our equivalence lock

# Default test directory
TEST_DIR = Path("tests/equivalence")

def parse_expected_diagnostics(test_file):
    """Parse // EXPECTED-DIAG comments for warning/error counts"""
    expected_warnings = 0
    with open(test_file, 'r') as f:
        for line in f:
            if "EXPECTED-DIAG:" in line:
                parts = line.split(':')[1].split()
                for i, part in enumerate(parts):
                    if part == 'warnings':
                        expected_warnings = int(parts[i-1])
    return expected_warnings

def extract_actual_warnings(output):
    """Parse actual warning count from stdout"""
    for line in output.splitlines():
        if line.startswith("Diagnostics: "):
            return int(line.split()[1])
    return 0

def run_test(test_file, debug=False):
    expect_fail = "fail" in test_file.name.lower()
    expected_warnings = parse_expected_diagnostics(test_file)

    print(f"Running {test_file.name}...", end=" ")
    
    # Create the command with debug flag if needed
    cmd = [sys.executable, "saka.py"]
    if debug:
        cmd.append("--debug")
    cmd.append(str(test_file))
    
    if debug:
        print(f"\nCommand: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    failed = result.returncode != 0
    actual_warnings = extract_actual_warnings(result.stdout)
    diag_match = (actual_warnings == expected_warnings)
    
    # Enhanced reporting
    if expect_fail and failed:
        status = "expected failure"
    elif not expect_fail and not failed and diag_match:
        status = "pass"
    elif not expect_fail and not failed:
        status = "pass (with diagnostic mismatch)"
    else:
        status = "FAIL"
        
    # Status reporting without emojis
    print(status)

    # Show diagnostic comparison
    if expected_warnings > 0 or actual_warnings > 0:
        print(f"  Diagnostics: Expected {expected_warnings} warnings, got {actual_warnings}")
        if actual_warnings != expected_warnings:
            print(f"  Mismatch: expected {expected_warnings}, actual {actual_warnings}")
    
        print("  Running equivalence verification...")
        
    # Run through equivalence lock test
    try:
        ast_result = equiv_lock.run_ast_interpreter(test_file)
        vm_result = equiv_lock.run_bytecode_vm(test_file)
        failures = equiv_lock.compare_results(ast_result, vm_result, test_file)
        
        if failures:
            print(f"   EQUIVALENCE FAILURE: {len(failures)} differences detected")
            for failure in failures:
                print(f"    {failure}")
            return False
        else:
            print("  Equivalence lock passed")
    except Exception as e:
        print(f"   Unexpected error during equivalence check: {e}")
        return False

    # Always show output in debug mode
    if debug or not (expect_fail == failed and diag_match):
        if result.stdout:
            print("STDOUT:")
            print(result.stdout)
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
    
    return expect_fail == failed and diag_match


def main():
    parser = argparse.ArgumentParser(description='Run equivalence tests for Osaka Lang')
    parser.add_argument('--filter', type=str, help='Filter tests by name substring')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--dir', type=str, default=str(TEST_DIR), 
                        help='Test directory path (default: tests/equivalence)')
    args = parser.parse_args()

    test_dir = Path(args.dir)
    if not test_dir.exists():
        print(f"Test directory not found: {test_dir}")
        sys.exit(1)

    tests = sorted(test_dir.glob("*.saka"))
    
    if args.filter:
        tests = [t for t in tests if args.filter in t.name]
        if not tests:
            print(f"No tests found matching filter: {args.filter}")
            sys.exit(1)

    if not tests:
        print("No tests found.")
        sys.exit(1)

    passed = 0
    print(f"Running {len(tests)} tests in {test_dir}...")

    for test in tests:
        if run_test(test, debug=args.debug):
            passed += 1

    total = len(tests)
    print("\n==========================")
    print(f"Passed {passed}/{total} tests")

    if passed != total:
        sys.exit(1)

    print("All tests passed")


if __name__ == "__main__":
    main()