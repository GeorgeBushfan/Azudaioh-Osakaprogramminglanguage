"""Single source of truth for Osaka built-in names and arities."""


PUBLIC_BUILTIN_ARITIES = {
    "Ah": 1,
    "Americaya": 1,
    "AppendFile": 2,
    "Args": 0,
    "DeleteFile": 1,
    "FileExists": 1,
    "Getittogether": 0,
    "Hecho": 1,
    "Ivebeengot": 1,
    "Math.E": 0,
    "Math.PI": 0,
    "Math.abs": 1,
    "Math.ceil": 1,
    "Math.clamp": 3,
    "Math.cos": 1,
    "Math.cosh": 1,
    "Math.floor": 1,
    "Math.max": 2,
    "Math.min": 2,
    "Math.pow": 2,
    "Math.random": 0,
    "Math.round": 1,
    "Math.sign": 1,
    "Math.sin": 1,
    "Math.sinh": 1,
    "Math.sqrt": 1,
    "Math.tan": 1,
    "Math.tanh": 1,
    "Math.trunc": 1,
    "Ohmygah": 1,
    "Panic": 1,
    "ReadFile": 1,
    "SataAndagi": 0,
    "Say": 1,
    "WriteFile": 2,
    "contains": 2,
    "keys": 1,
    "len": 1,
    "pop": 1,
    "push": 2,
    "slice": 3,
    "std.contains": 2,
    "std.keys": 1,
    "std.len": 1,
    "std.pop": 1,
    "std.push": 2,
    "std.slice": 3,
    "std.values": 1,
    "values": 1,
    "youknowsealsright": 1,
}

BOOTSTRAP_PUBLIC_BUILTINS = {
    "Americaya", "AppendFile", "Args", "DeleteFile", "FileExists", "Panic", "ReadFile",
    "Say", "WriteFile", "contains", "keys", "len", "pop", "push", "slice",
    "values",
}

# Internal builtins callable from bootstrap-profile source. The self-hosted
# compiler uses __is_float__ to classify numeric literal kinds, mirroring
# Stage 0's isinstance(value, float) check. __json_type__ and __float_repr__
# support the self-hosted SBC1 emitter's value dispatch and float formatting.
BOOTSTRAP_INTERNAL_BUILTINS = {"__is_float__", "__float_repr__", "__json_type__"}

INTERNAL_BUILTIN_ARITIES = {
    "__bool_and__": 2,
    "__bool_not__": 1,
    "__float_repr__": 1,
    "__is_float__": 1,
    "__json_type__": 1,
    "__bool_or__": 2,
    "__capture_trace__": 0,
    "__export_symbol__": 1,
    "__force_kind_grain__": 1,
    "__force_kind_truth__": 1,
    "__import_file_module__": 2,
    "__import_module__": 1,
    "__pop_scope__": 0,
    "__push_scope__": 0,
    "__to_bool_preserve_kind__": 1,
    "__to_truthaboutgrain__": 1,
}

BUILTIN_ARITIES = {**PUBLIC_BUILTIN_ARITIES, **INTERNAL_BUILTIN_ARITIES}
POLICY_NAME_BUILTINS = {"Ah", "Hecho", "Ivebeengot", "youknowsealsright"}


def is_builtin(name):
    return name in BUILTIN_ARITIES


def validate_builtin_arity(name, actual):
    if name not in BUILTIN_ARITIES:
        raise RuntimeError(f"Unknown built-in {name}")
    expected = BUILTIN_ARITIES[name]
    if actual != expected:
        raise RuntimeError(f"{name}() expects {expected} arguments, got {actual}")
    return True