"""Canonical SBC1 bytecode serialization for Osaka.

The writer emits the schema documented in ``docs/BYTECODE.md``. The loader
also accepts the short-lived pre-contract Stage 0 schema so local artifacts can
be migrated, but canonical output never uses that legacy shape.
"""

import json
import math

from bytecode import INDEX_SET, STORE_VAR, BytecodeProgram, FunctionBytecode, Value


FORMAT = "SBC"
VERSION = 1
LANGUAGE_VERSION = "1.1"
MAGIC = FORMAT  # Compatibility alias for early callers.


def _encode_value(value):
    data = value.data
    if isinstance(data, float) and not math.isfinite(data):
        raise ValueError("SBC1 constants must use finite floating-point values")
    if data is not None and not isinstance(data, (bool, int, float, str)):
        raise ValueError(f"SBC1 constant is not serializable: {type(data).__name__}")
    if value.kind not in {"truth", "grain"}:
        raise ValueError(f"Invalid SBC1 value kind: {value.kind}")
    return {"data": data, "kind": value.kind}


def _decode_value(record):
    if not isinstance(record, dict) or set(record) != {"data", "kind"}:
        raise ValueError("SBC1 constants must contain exactly data and kind")
    data, kind = record["data"], record["kind"]
    if kind not in {"truth", "grain"}:
        raise ValueError(f"Invalid SBC1 value kind: {kind}")
    if isinstance(data, float) and not math.isfinite(data):
        raise ValueError("SBC1 constants must use finite floating-point values")
    if data is not None and not isinstance(data, (bool, int, float, str)):
        raise ValueError(f"Unsupported SBC1 constant type: {type(data).__name__}")
    return Value(data, kind)


def _derive_locals(code, params):
    """Reconstruct a function's local-variable list from its bytecode.

    The compiler computes locals as params ∪ assigned variables (see
    Compiler._collect_local_vars). SBC1 does not serialize that list, so the
    loader re-derives it: every variable the function stores (STORE_VAR) or
    indexes into (INDEX_SET) is a local. Without this, loaded artifacts run
    without frame-local isolation and nested calls corrupt each other's
    scope-chain variables (observed as an infinite loop in the self-hosted
    lexer — the reason no gate stage ever completed).
    """
    names = list(params)
    seen = set(params)
    for op, arg, _line in code:
        if op in (STORE_VAR, INDEX_SET) and isinstance(arg, str) and arg not in seen:
            seen.add(arg)
            names.append(arg)
    return names


def _decode_legacy_value(record):
    if record.get("type") not in {"null", "bool", "int", "float", "string"}:
        raise ValueError(f"Unsupported legacy SBC1 constant type: {record.get('type')}")
    return _decode_value({"data": record.get("value"), "kind": record.get("kind")})


def _encode_instruction(instruction):
    op, arg, line = instruction
    return {"arg": list(arg) if isinstance(arg, tuple) else arg, "line": line, "op": op}


def _decode_instruction(record):
    if not isinstance(record, dict) or set(record) != {"arg", "line", "op"}:
        raise ValueError("SBC1 instructions must contain exactly op, arg, and line")
    op, arg, line = record["op"], record["arg"], record["line"]
    if not isinstance(op, str) or not isinstance(line, int):
        raise ValueError("SBC1 instruction op must be a string and line must be an integer")
    return op, tuple(arg) if isinstance(arg, list) else arg, line


def _decode_legacy_instruction(record):
    if not isinstance(record, list) or len(record) != 3:
        raise ValueError("Legacy SBC1 instructions must be [opcode, operand, line]")
    op, arg, line = record
    return op, tuple(arg) if isinstance(arg, list) else arg, line


def program_to_dict(program):
    functions = {}
    for name in sorted((program.functions or {}).keys()):
        function = program.functions[name]
        functions[name] = {
            "code": [_encode_instruction(item) for item in function.program.code],
            "constants": [_encode_value(value) for value in function.program.consts],
            "params": list(function.params),
        }
    return {
        "constants": [_encode_value(value) for value in program.consts],
        "format": FORMAT,
        "functions": functions,
        "language_version": LANGUAGE_VERSION,
        "main": [_encode_instruction(item) for item in program.code],
        "version": VERSION,
    }


def _program_from_canonical(document):
    required = {"constants", "format", "functions", "language_version", "main", "version"}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("SBC1 document has missing or unknown top-level fields")
    if document["format"] != FORMAT or document["version"] != VERSION:
        raise ValueError("Unsupported SBC format or version")
    if document["language_version"] != LANGUAGE_VERSION:
        raise ValueError(f"Unsupported Osaka language version: {document['language_version']}")
    if not isinstance(document["constants"], list) or not isinstance(document["main"], list):
        raise ValueError("SBC1 constants and main must be arrays")
    if not isinstance(document["functions"], dict):
        raise ValueError("SBC1 functions must be an object")

    functions = {}
    for name, record in document["functions"].items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise ValueError("SBC1 function names and records are invalid")
        if set(record) != {"code", "constants", "params"}:
            raise ValueError(f"SBC1 function {name!r} has invalid fields")
        if not isinstance(record["params"], list) or not all(isinstance(p, str) for p in record["params"]):
            raise ValueError(f"SBC1 function {name!r} has invalid parameters")
        code = [_decode_instruction(item) for item in record["code"]]
        params = list(record["params"])
        function_program = BytecodeProgram(
            consts=[_decode_value(value) for value in record["constants"]],
            code=code,
            functions={},
        )
        functions[name] = FunctionBytecode(
            params, function_program,
            locals=_derive_locals(code, params),
        )
    code = [_decode_instruction(item) for item in document["main"]]
    return BytecodeProgram(
        consts=[_decode_value(value) for value in document["constants"]],
        code=code,
        functions=functions,
        main=code,
    )


def _program_from_legacy(document):
    if not isinstance(document, dict) or document.get("magic") != "OSAKA-SBC" or document.get("version") != VERSION:
        raise ValueError("Unsupported or corrupt SBC file")
    functions = {}
    for name, record in document.get("functions", {}).items():
        code = [_decode_legacy_instruction(item) for item in record.get("code", [])]
        params = list(record.get("params", []))
        function_program = BytecodeProgram(
            consts=[_decode_legacy_value(value) for value in record.get("consts", [])],
            code=code,
            functions={},
        )
        functions[name] = FunctionBytecode(
            params, function_program,
            locals=_derive_locals(code, params),
        )
    code = [_decode_legacy_instruction(item) for item in document.get("code", [])]
    return BytecodeProgram(
        consts=[_decode_legacy_value(value) for value in document.get("consts", [])],
        code=code,
        functions=functions,
        main=code,
    )


def program_from_dict(document):
    if isinstance(document, dict) and document.get("format") == FORMAT:
        return _program_from_canonical(document)
    return _program_from_legacy(document)


def dumps(program):
    return json.dumps(program_to_dict(program), ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")) + "\n"


def loads(text):
    return program_from_dict(json.loads(text))


def dump(program, path):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(program))


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return loads(handle.read())