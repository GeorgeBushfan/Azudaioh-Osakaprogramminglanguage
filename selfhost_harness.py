"""Compile bundled Osaka modules and invoke one function on the Python VM."""

from bytecode import BytecodeProgram, HALT, PUSH_CONST, CALL_FUNC, Value
from bootstrap_profile import validate_bootstrap_ast
from compiler import Compiler
from runtime import Runtime
from selfhost_bundle import bundle_ast
from verifier import verify_program
from vm import VM


def invoke(function_name, args, order):
    ast = bundle_ast("selfhost", order)
    validate_bootstrap_ast(ast)
    compiled = Compiler().compile_program(ast)
    consts = []
    code = []
    for arg in args:
        consts.append(Value(arg, "truth"))
        code.append((PUSH_CONST, len(consts) - 1, -1))
    code.extend(((CALL_FUNC, (function_name, len(args)), -1), (HALT, None, -1)))
    program = BytecodeProgram(consts=consts, code=code, functions=compiled.functions)
    verify_program(program)
    runtime = Runtime()
    vm = VM(runtime)
    vm.run(program)
    frame = vm.frames[-1] if vm.frames else None
    result = frame.stack[-1].data if frame and frame.stack else None
    return result