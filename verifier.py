class VerificationError(Exception):
    pass


STACK_EFFECTS = {
    "PUSH_CONST": lambda _: +1,
    "LOAD_VAR": lambda _: +1,
    "STORE_VAR": lambda _: -1,
    "DUP": lambda _: +1,
    "POP": lambda _: -1,
    "ADD": lambda _: -1,           # pop 2, push 1
    "SUB": lambda _: -1,
    "MUL": lambda _: -1,
    "DIV": lambda _: -1,
    "MOD": lambda _: -1,
    "CMP_EQ": lambda _: -1,
    "CMP_NE": lambda _: -1,
    "CMP_LT": lambda _: -1,
    "CMP_LE": lambda _: -1,
    "CMP_GT": lambda _: -1,
    "CMP_GE": lambda _: -1,
    "CALL_FUNC": lambda arg: -arg[1] + 1,
    "CALL_BUILTIN": lambda arg: -arg[1] + 1,
    "RET": lambda _: -1,
    "HALT": lambda _: 0,
}

BUILTIN_ARITY = {
    "Say": 1,
    "len": 1,
    "keys": 1,
    "values": 1,
    "contains": 2,
    "slice": 3,
    "push": 2,
    "pop": 1,
    "ReadFile": 1,
    "WriteFile": 2,
    "AppendFile": 2,
    "FileExists": 1,
    "DeleteFile": 1,
    "Ah": 1,
    "Hecho": 1,
    "youknowsealsright": 1,
    "Ivebeengot": 1,
    "Getittogether": 0,
    "SataAndagi": 0,
    "Americaya": 1,
    "Math.abs": 1,
    "Math.min": 2,
    "Math.max": 2,
    "Math.pow": 2,
    "Math.floor": 1,
    "Math.ceil": 1,
    "Math.sqrt": 1,
    "Math.round": 1,
    "Math.trunc": 1,
    "Math.sign": 1,
    "Math.clamp": 3,
    "Math.PI": 0,
    "Math.E": 0,
    "Math.random": 0,
    "Math.sin": 1,
    "Math.cos": 1,
    "Math.tan": 1,
    "Math.sinh": 1,
    "Math.cosh": 1,
    "Math.tanh": 1,
    "add": 2,
    "__force_kind_grain__": 1,
    "__force_kind_truth__": 1,
    "__to_truthaboutgrain__": 1,
    "__push_scope__": 0,
    "__pop_scope__": 0,
    "__capture_trace__": 0,
    "__to_bool_preserve_kind__": 1,
    "__bool_and__": 2,
    "__bool_or__": 2,
    "__bool_not__": 1,
    "__import_module__": 1,
    "__import_file_module__": 2,
    "__export_symbol__": 1,
    "std.len": 1,
    "std.keys": 1,
    "std.values": 1,
    "std.contains": 2,
    "std.slice": 3,
    "std.push": 2,
    "std.pop": 1,
}


def _read_instr(instr):
    if hasattr(instr, "op"):
        return instr.op, instr.arg
    # tuple form: (op, arg, line)
    if isinstance(instr, tuple):
        if len(instr) >= 2:
            return instr[0], instr[1]
    raise VerificationError(f"Invalid instruction format: {instr!r}")


def _function_arity(function_table, fname):
    entry = function_table[fname]
    if isinstance(entry, int):
        return entry
    if isinstance(entry, dict):
        if "params" in entry and isinstance(entry["params"], list):
            return len(entry["params"])
    raise VerificationError(f"Invalid function table entry for {fname}: {entry!r}")

def verify(bytecode, function_table):
    verify_block(bytecode.main, function_table, is_main=True)

    for fname, fn_code in bytecode.functions.items():
        verify_block(fn_code.code, function_table, is_main=False)

def verify_block(code, function_table, is_main):
    stack_height = 0
    print(f"Verifying {'main' if is_main else 'function'} block")
    
    for ip, instr in enumerate(code):
        op, arg = _read_instr(instr)
        
        print(f"[{ip}] {op} {arg or ''} (stack: {stack_height})")

        # 1. opcode validity
        if op not in STACK_EFFECTS:
            raise VerificationError(f"Unknown opcode {op} at {ip}")

        # 2. stack discipline
        delta = STACK_EFFECTS[op](arg)
        stack_height += delta
        print(f"  -> Stack delta: {delta:+}, new height: {stack_height}")
        
        if stack_height < 0:
            raise VerificationError(
                f"Stack underflow at instruction {ip} ({op})"
            )

        # 3. CALL_FUNC arity checks
        if op == "CALL_FUNC":
            fname, argc = arg
            if fname not in function_table:
                raise VerificationError(f"Unknown function {fname}")
            expected = _function_arity(function_table, fname)
            if argc != expected:
                raise VerificationError(
                    f"Function {fname} expects {expected} args, got {argc}"
                )

        # 3b. CALL_BUILTIN arity checks
        if op == "CALL_BUILTIN":
            bname, argc = arg
            if bname not in BUILTIN_ARITY:
                raise VerificationError(f"Unknown builtin {bname}")
            expected = BUILTIN_ARITY[bname]
            if argc != expected:
                raise VerificationError(
                    f"Builtin {bname} expects {expected} args, got {argc}"
                )

        # 4. RET placement rules
        if op == "RET" and is_main:
            raise VerificationError("RET not allowed in main block")

    # 5. end-of-block rules
    if is_main:
        last_op, _ = _read_instr(code[-1])
        if last_op != "HALT":
            raise VerificationError("Main block must end with HALT")
        else:
            print("Main block ends with HALT - valid")
    else:
        last_op, _ = _read_instr(code[-1])
        if last_op != "RET":
            raise VerificationError("Function must end with RET")
        else:
            print("Function ends with RET - valid")

    print(f"Final stack height: {stack_height}")
    print("Block verification successful")
