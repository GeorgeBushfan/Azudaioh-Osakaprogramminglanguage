# Osaka Bytecode and SBC Container Contract

## Status

This document defines the portable boundary between Osaka compilers and Osaka
virtual machines. The first serialized format is named **SBC1** (Saka Bytecode
Container, version 1).

The Python `BytecodeProgram` objects are an in-memory representation of this
contract; Python dataclasses and tuples are not themselves part of the format.

## Design requirements

SBC1 must be:

- deterministic for identical compiler inputs and options;
- independent of Python object layout;
- safe to validate before execution;
- explicit about value kinds and operand types;
- forward-versioned so incompatible VMs reject it cleanly;
- sufficiently simple to load from Osaka and a small native runtime.

## Value model

Every runtime value consists of:

```text
(data, kind)
```

`kind` is exactly one of:

- `truth`
- `grain`

SBC1 constant data may be:

- null;
- boolean;
- integer;
- finite floating-point number;
- Unicode string.

Lists and maps are constructed by instructions rather than stored as mutable
constant-pool objects. A VM must not expose a mutable constant-pool object
directly to a running program.

## Call convention

Source arguments are evaluated **left to right**. For a call `f(a, b, c)`, the
compiler pushes the resulting values in source order:

```text
... a b c
```

`CALL_FUNC ("f", 3)` and `CALL_BUILTIN ("f", 3)` pop three values and present
them to the callee as `[a, b, c]`. Implementations must not use per-built-in
argument reversal or compensation.

Every call produces exactly one stack value. Statement-position calls are
followed by `POP`, unless the call terminates control flow.

Function parameters are initialized in declaration order. Under the current
source-language contract, unannotated parameters are bound with `grain` kind.

## Scope contract

- The main frame begins with one global scope.
- Entering a lexical `Block` pushes one scope and leaving it pops that scope.
- Assigning an existing name updates the nearest enclosing scope containing
  that name.
- Assigning a new name creates it in the current scope.
- Function locals are distinct from caller locals.
- Every normal scope-enter operation has one corresponding scope-exit
  operation on every reachable control-flow path.

Future bytecode revisions may replace scope built-ins with dedicated opcodes;
SBC1 keeps the current operations but gives them normative semantics.

## Control-flow contract

`JMP_IF_FALSE` pops one value. Its kind must already be `truth`; otherwise the
VM raises a normalized control-flow-kind error. It then jumps when the data is
falsey according to Osaka boolean conversion.

The compiler must not silently promote a `grain` condition. Explicit source
promotion such as `Americaya` remains valid.

Jump targets are zero-based instruction indexes in the current function block.
Targets may point one instruction past the final non-terminating instruction
only when the block has a well-defined implicit return; canonical SBC1
functions and main blocks use explicit terminators instead.

## Opcode table

Stack effects use `N` for an instruction operand and `argc` for a call operand.

| Opcode | Operand | Stack effect | Meaning |
|---|---|---:|---|
| `PUSH_CONST` | constant index | `+1` | Push a fresh value copied from the pool. |
| `LOAD_VAR` | name | `+1` | Push the nearest visible variable. |
| `STORE_VAR` | name | `-1` | Store the top value. |
| `DUP` | null | `+1` | Duplicate the top value record. |
| `POP` | null | `-1` | Discard the top value. |
| `ADD` | null | `-1` | Pop two operands and push sum/concatenation. |
| `SUB` | null | `-1` | Numeric subtraction. |
| `MUL` | null | `-1` | Numeric multiplication. |
| `DIV` | null | `-1` | Numeric division; zero divisor is an error. |
| `MOD` | null | `-1` | Numeric remainder; zero divisor is an error. |
| `MAKE_LIST` | item count `N` | `1-N` | Construct a list from values in source order. |
| `MAKE_MAP` | pair count `N` | `1-2N` | Construct a map from key/value pairs. |
| `INDEX_GET` | null | `-1` | Pop container/index and push selected value. |
| `INDEX_SET` | base name | `-3` | Mutate named container; no result value. |
| `CALL_BUILTIN` | `[name, argc]` | `1-argc` | Invoke a registered built-in. |
| `CALL_FUNC` | `[name, argc]` | `1-argc` | Invoke a bytecode function. |
| `CMP_EQ` | null | `-1` | Equality comparison. |
| `CMP_NE` | null | `-1` | Inequality comparison. |
| `CMP_LT` | null | `-1` | Less-than comparison. |
| `CMP_LE` | null | `-1` | Less-than-or-equal comparison. |
| `CMP_GT` | null | `-1` | Greater-than comparison. |
| `CMP_GE` | null | `-1` | Greater-than-or-equal comparison. |
| `JMP` | target | `0` | Unconditional branch. |
| `JMP_IF_FALSE` | target | `-1` | Strict truth-kind conditional branch. |
| `TRY_PUSH` | catch target | `0` | Push catch handler for the current frame. |
| `TRY_POP` | null | `0` | Pop current catch handler. |
| `TRACE_POINT` | null | `0` | Optional semantic checkpoint. |
| `RET` | null | `-1` | Return top value; functions emit a default first. |
| `HALT` | null | `0` | Terminate the main block. |

Internal Stage 0 built-ins whose names begin with `__` are reserved. Source
programs must not rely on them as public API.

## SBC1 logical schema

The initial transport is canonical UTF-8 JSON. The logical shape is:

```json
{
  "format": "SBC",
  "version": 1,
  "language_version": "1.1",
  "constants": [
    {"kind": "truth", "data": "hello"}
  ],
  "main": [
    {"op": "PUSH_CONST", "arg": 0, "line": 1},
    {"op": "CALL_BUILTIN", "arg": ["Say", 1], "line": 1},
    {"op": "POP", "arg": null, "line": 1},
    {"op": "HALT", "arg": null, "line": -1}
  ],
  "functions": {
    "name": {
      "params": ["x"],
      "constants": [],
      "code": []
    }
  }
}
```

`code` and `main` are aliases only in the Python in-memory model. SBC1 stores
`main` once.

Each instruction object contains exactly `op`, `arg`, and `line`. Each constant
object contains exactly `data` and `kind`. Function records contain exactly
`params`, `constants`, and `code`. Unknown fields are rejected in SBC1 so that
format evolution remains explicit.

## Canonical serialization

The canonical JSON encoding uses:

- UTF-8 without a byte-order mark;
- object keys sorted lexicographically;
- separators `,` and `:` without insignificant whitespace;
- one final newline;
- JSON escaping for control characters;
- finite numbers only;
- function names sorted lexicographically when serialized;
- constants retained in compiler-assigned index order;
- no timestamp, host path, object address, or random metadata.

The SHA-256 fixed-point comparison is performed over these canonical bytes.

## Verification requirements

Before execution, a conforming loader verifies:

1. top-level format and supported version;
2. exact value-kind names and serializable constant types;
3. known opcodes and operand shape;
4. in-range constant indexes and jump targets;
5. known built-in and function names with correct arity;
6. stack safety on every reachable control-flow path;
7. equal stack height at control-flow merge points;
8. valid try-handler structure;
9. main terminates with `HALT`;
10. every function return path reaches `RET` with a return value.

Unreachable code may be rejected in strict mode. It may never be used to hide
an invalid reachable instruction.

## Compatibility policy

- Adding metadata that old loaders may ignore does not require a format bump.
- Adding or changing an opcode, value representation, stack effect, or call
  convention requires a new bytecode version.
- Loaders reject newer unknown versions; they must not guess.
- Compilers may offer an explicit target version but must never silently emit a
  different version from the requested one.
