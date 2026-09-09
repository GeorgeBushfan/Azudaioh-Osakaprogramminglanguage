"""Control-flow-aware verifier for Osaka bytecode."""

from collections import deque
from bytecode import *
from builtin_registry import BUILTIN_ARITIES


class VerificationError(Exception):
    pass


BINARY_OPS = {ADD, SUB, MUL, DIV, MOD, CMP_EQ, CMP_NE, CMP_LT, CMP_LE, CMP_GT, CMP_GE}
KNOWN_OPS = {PUSH_CONST, LOAD_VAR, STORE_VAR, DUP, POP, *BINARY_OPS,
             MAKE_LIST, MAKE_MAP, INDEX_GET, INDEX_SET, CALL_BUILTIN,
             CALL_FUNC, JMP, JMP_IF_FALSE, RET, HALT, TRACE_POINT,
             TRY_PUSH, TRY_POP}


def _instruction_tuple(instruction):
    if hasattr(instruction, "op"):
        return instruction.op, getattr(instruction, "arg", None), -1
    return (*instruction, -1) if len(instruction) == 2 else instruction


def _parts(function, fallback=0):
    if isinstance(function, FunctionBytecode):
        return function.program, len(function.params)
    return function, fallback


def _effect(op, arg, arities):
    if op in {PUSH_CONST, LOAD_VAR}: return 0, 1
    if op == STORE_VAR: return 1, -1
    if op == DUP: return 1, 1
    if op == POP: return 1, -1
    if op in BINARY_OPS: return 2, -1
    if op == MAKE_LIST: return arg, 1 - arg
    if op == MAKE_MAP: return 2 * arg, 1 - 2 * arg
    if op == INDEX_GET: return 2, -1
    if op == INDEX_SET: return 3, -3
    if op in {CALL_BUILTIN, CALL_FUNC}:
        if not isinstance(arg, tuple) or len(arg) != 2:
            raise VerificationError(f"Malformed call operand: {arg!r}")
        name, argc = arg
        if op == CALL_BUILTIN:
            if name not in BUILTIN_ARITIES:
                raise VerificationError(f"Unknown built-in {name}")
            if argc != BUILTIN_ARITIES[name]:
                raise VerificationError(
                    f"Built-in {name} expects {BUILTIN_ARITIES[name]} args, got {argc}"
                )
        else:
            if name not in arities:
                # File-module functions are linked at runtime after the
                # preceding import instruction has loaded alias.symbol.
                if "." in name:
                    return argc, 1 - argc
                raise VerificationError(f"Unknown function {name}")
            if argc != arities[name]:
                raise VerificationError(f"Function {name} expects {arities[name]} args, got {argc}")
        return argc, 1 - argc
    if op in {JMP_IF_FALSE, RET}: return 1, -1
    return 0, 0


def verify_block(code, consts, arities, is_function=False, entry_height=0, debug=False):
    code = [_instruction_tuple(item) for item in code]
    if not code:
        raise VerificationError("Empty bytecode block")
    heights, work, terminal = {0: entry_height}, deque([0]), False
    while work:
        ip = work.popleft()
        height = heights[ip]
        op, arg, _ = code[ip]
        if op not in KNOWN_OPS:
            raise VerificationError(f"Unknown opcode {op} at instruction {ip}")
        if op == PUSH_CONST and (not isinstance(arg, int) or not 0 <= arg < len(consts)):
            raise VerificationError(f"Invalid constant index {arg!r} at instruction {ip}")
        if op in {JMP, JMP_IF_FALSE, TRY_PUSH} and (not isinstance(arg, int) or not 0 <= arg < len(code)):
            raise VerificationError(f"Invalid jump target {arg!r} at instruction {ip}")
        needed, delta = _effect(op, arg, arities)
        if height < needed:
            raise VerificationError(f"Stack underflow at instruction {ip}: {op} needs {needed}, has {height}")
        out = height + delta
        if debug: print(f"[{ip}] {op} {arg!r}: {height} -> {out}")
        if op == RET:
            if not is_function: raise VerificationError("RET not allowed in main block")
            terminal, successors = True, []
        elif op == HALT:
            if is_function: raise VerificationError("HALT is not allowed in function bytecode")
            terminal, successors = True, []
        elif op == JMP:
            successors = [arg]
        elif op in {JMP_IF_FALSE, TRY_PUSH}:
            successors = [arg] + ([ip + 1] if ip + 1 < len(code) else [])
        else:
            successors = [ip + 1] if ip + 1 < len(code) else []
        for successor in successors:
            if successor not in heights:
                heights[successor] = out
                work.append(successor)
            elif heights[successor] != out:
                raise VerificationError(f"Inconsistent stack height at instruction {successor}: {heights[successor]} versus {out}")
    if not terminal:
        raise VerificationError("Function must end with RET" if is_function else "Main block must end with HALT")
    return True


def verify_program(program, debug=False, arity_overrides=None):
    functions = program.functions or {}
    arities = {name: _parts(function)[1] for name, function in functions.items()}
    arities.update(arity_overrides or {})
    main = program.code or getattr(program, "main", None) or []
    verify_block(main, program.consts, arities, debug=debug)
    for name, function in functions.items():
        function_program, arity = _parts(function, arities.get(name, 0))
        code = function_program.code or getattr(function_program, "main", None) or []
        # Function arguments are supplied by the caller and consumed by the
        # parameter STORE_VAR prologue, so they are the block's entry stack.
        verify_block(code, function_program.consts, arities, True, arity, debug)
    return True


def verify(program, function_table=None, debug=False):
    overrides = {name: (len(value.get("params", [])) if isinstance(value, dict) else int(value))
                 for name, value in (function_table or {}).items()}
    return verify_program(program, debug, overrides)


class BytecodeVerifier:
    def __init__(self, program, debug=False):
        self.program, self.debug = program, debug
    def verify(self):
        return verify_program(self.program, self.debug)