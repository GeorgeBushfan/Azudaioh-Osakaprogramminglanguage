import os
from compiler import Compiler
from parser import Parser
from lexer import lex
from verifier import verify, VerificationError
from bytecode import Instruction   # adjust import if needed


def run_verification_tests():
    test_dir = "tests/verification"
    total = 0
    passed = 0

    print("\nRunning verification tests...\n")

    # ------------------------
    # VALID TESTS
    # ------------------------
    for fname in os.listdir(f"{test_dir}/valid"):
        path = f"{test_dir}/valid/{fname}"
        try:
            tokens = lex(open(path).read())
            ast = Parser(tokens).parse()
            compiler = Compiler()
            bytecode = compiler.compile_program(ast)

            verify(bytecode, compiler.function_table)

            print(f" PASS: {path}")
            passed += 1

        except Exception as e:
            print(f" FAIL: {path}\n    {e}")

        total += 1

    # ------------------------
    # INVALID TESTS
    # ------------------------
    for fname in os.listdir(f"{test_dir}/invalid"):
        path = f"{test_dir}/invalid/{fname}"
        try:
            tokens = lex(open(path).read())
            ast = Parser(tokens).parse()
            compiler = Compiler()
            bytecode = compiler.compile_program(ast)

            verify(bytecode, compiler.function_table)

            print(f" FAIL: {path} (no error raised)")

        except VerificationError:
            print(f" PASS: {path}")
            passed += 1

        except Exception as e:
            print(f" FAIL: {path} (unexpected error)\n    {e}")

        total += 1

    print(f"\nResults: {passed}/{total} tests passed")


if __name__ == "__main__":
    run_verification_tests()
