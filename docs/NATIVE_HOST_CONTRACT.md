# Native Host Contract (Gate E)

This document is the normative specification for the Gate E native host: a
C program that loads an SBC1 document and executes it with the same
observable behavior as the Stage 0 Python VM (`vm.py`) and the self-hosted
Osaka VM (`selfhost/vm.saka`). The native host replaces the Python layer
with native implementations of the host-boundary builtins; the VM logic
itself is transpiled from `selfhost/vm.saka` (Gate D's proven 1:1 port of
`vm.py`).

Normative sources, in priority order:

1. `vm.py` — Stage 0 reference implementation (semantic oracle).
2. `selfhost/vm.saka` — the Osaka port the native VM executes as transpiled C.
3. `sbc.py` — SBC1 serialization and loader canonicalization.
4. `builtin_registry.py` — builtin arity table (single source of truth).

Per `docs/SELF_HOSTING.md` "Runtime equivalence", program stdout, warnings,
and unhandled-error text are semantics; execution traces, timestamps, and
internal state are not.

## 1. Value model

Every runtime value is a `[data, kind]` pair (C: a struct with a data union
and a kind tag).

- `kind` is one of the strings `"truth"` or `"grain"`.
- `data` is one of: 64-bit int, 64-bit IEEE-754 double, string (byte
  buffer), list (dynamic array of values), map (insertion-ordered hash map
  keyed by value), bool, or null.
- Bool and int are distinct data tags (Python's `bool` is a subtype of
  `int`; the distinction is observable via `__is_bool__` and
  `__json_type__`).
- Null is a distinct data tag. The profile has no null literal; the only
  way guest code obtains null is the return of `__push_scope__` (see §3.6)
  and list-growth fill (`INDEX_SET` past the end pads with null).

### 1.1 JSON type numbering (`__json_type__`)

The structural type probe returns an integer code. The check order matters
because Python bools are ints:

| code | type      | note                                   |
|------|-----------|----------------------------------------|
| 0    | int       | checked **after** bool                 |
| 1    | float     |                                        |
| 2    | str       |                                        |
| 3    | list      |                                        |
| 4    | map/dict  |                                        |
| 5    | bool      | checked **before** int                 |
| 6    | null      |                                        |

Any other data tag is a host error (`__json_type__ unsupported constant
type`) — unreachable in practice because the loader only admits scalars.

### 1.2 Kind propagation rules (implemented inside the transpiled VM)

- `vm_truth_and(a, b)`: `"truth"` iff both are `"truth"`, else `"grain"`.
- Ordered/boolean results carry the truth-and of operand kinds.
- `MAKE_LIST`/`MAKE_MAP`: kind is `"truth"` iff every element/value kind is
  `"truth"`.
- Storing grain into a container downgrades the variable's recorded kind
  (`var_kinds[base] = "grain"`).
- Parameters bind as `"grain"` on first store (detected via the
  `__vm_uninit__` sentinel pre-seeded into frame locals).

## 2. Native host entry points

The native binary (`native/osakavm`) provides the same two entry points the
Python driver uses:

### 2.1 `vm_run(document, argv)` — CLI mode

```
osakavm <document.sbc> [guest-args...]
```

Executes the document's `main` instruction list with `argv` (guest args
exclude the document path). On success prints the buffered stdout to the
process stdout. Exit status and stderr reporting mirror `run_vm.py`: an
unhandled runtime error prints the diagnostic message and exits non-zero.

### 2.2 `vm_call_func(document, fname, raw_args)` — driver mode

Used by gate drivers to invoke a single function with raw (JSON) arguments.
The document, function name, and argument list arrive as a JSON request on
stdin (or via a second file argument); the response is the envelope below
as JSON on stdout. Raw args are wrapped as canonical constants
(`{"data": raw, "kind": "truth"}`) and driven by synthetic bytecode:
`PUSH_CONST` each arg, `CALL_FUNC`, `HALT`.

### 2.3 Result envelope

Both entry points produce:

```json
{"ok": 1, "value": {"stdout": "...", "warnings": ["[Warning] ..."]}, "diagnostic": 0}
```

or, on an unhandled runtime error:

```json
{"ok": 0, "value": {"stdout": "...", "warnings": [...]},
 "diagnostic": {"code": "RUNTIME_ERROR", "message": "...", "where": "..."}}
```

`where` is `"<fname> ip <ip> line <line>"` of the innermost frame at first
error, or `"?"` when no frames remain. `message` text must match Stage 0's
`RuntimeError` strings exactly (see §3, §4, §5).

## 3. Host-boundary builtins

These are the builtins the transpiled VM calls out to the native host for.
Each subsection gives the exact contract. Error text is normative.

### 3.1 `__json_type__(x)` → int, arity 1

Returns the JSON type code of `x.data` per §1.1. Always returns kind
`"truth"` (the wrapper in vm.saka constructs the pair).

### 3.2 `__is_bool__(x)` → bool, arity 1

True iff `x.data` has the bool tag (not merely 0/1). Kind `"truth"`.

### 3.3 `__is_float__(x)` → bool, arity 1

True iff `x.data` has the float tag. Kind `"truth"`.

### 3.4 `__float_repr__(x)` → string, arity 1

Returns Python `repr(float)` of `x.data` — the shortest round-trip
decimal representation, matching `json.dumps` float output:

- integral floats in range [-1e16, 1e16) render with `.0` (e.g. `2.0`);
- exponents use Python's `repr` switch to `e` notation (e.g. `1e+16`,
  `1.5e-07`);
- special handling: `inf` → `"inf"`, `"-inf"`, `nan` → `"nan"` (the loader
  rejects non-finite constants, but computed values can produce them).

Error if `x.data` is not a float: `__float_repr__ expects a float`.
Kind `"truth"`.

The C implementation must reproduce Python's `repr` algorithm exactly
(shortest-repr via David Gay / Grisu-style shortest digits, then Python's
repr formatting rules). Differential tests must cover: 0.0, -0.0, 1.0,
0.1, 1/3, 1e16, 1e17, 1e-5, 1e-4, 123456789012345678.0, values requiring
17 significant digits, and round-trip checks against Python for a large
random sample.

### 3.5 `__math_call__(name, pairs)` → pair, arity 2

`name` is the operation name without the `Math.` prefix (e.g. `"sqrt"`);
`pairs` is a list of `[data, kind]` guest pairs. The host applies the exact
`Math.*` semantics from `vm.py` (§4) and returns `[result_data, result_kind]`
wrapped as a pair with kind `"truth"` (the outer wrapper kind; the inner
pair carries the operation's own kind rule).

### 3.6 `__push_scope__()` / `__pop_scope__()` — arity 0 each

- `__push_scope__` pushes a fresh scope map and returns the **host null
  value** (kind `"truth"`). This is the profile's only null primitive:
  `vm_make_null()` captures it for `state["null"]`, which supplies default
  return values and list-growth fill.
- `__pop_scope__` pops the top scope if the scope stack has more than one
  scope; otherwise a no-op. Returns null.

The native host must therefore expose a null value through push_scope's
return, exactly as the Python host returns `Value(None, "truth")`.

### 3.7 File I/O builtins

Path resolution: relative paths resolve against the process working
directory (the native host has no module system; `rt.current_file` is
unset). Absolute paths are used as-is (normalized).

| builtin       | arity | behavior | returns |
|---------------|-------|----------|---------|
| `ReadFile`    | 1     | read whole file as UTF-8 text | `[content, path.kind]` |
| `WriteFile`   | 2     | create parent dirs if missing; truncate-write `str(content)` | `[1, truth_and(path.kind, content.kind)]` |
| `AppendFile`  | 2     | create parent dirs if missing; append `str(content)` | `[1, truth_and(path.kind, content.kind)]` |
| `FileExists`  | 1     | existence probe | `[1 or 0, path.kind]` |
| `DeleteFile`  | 1     | delete if exists; missing file is not an error | `[1, path.kind]` |

Error texts (unhandled runtime errors):

- `ReadFile failed: {oserror}`
- `WriteFile failed: {oserror}`
- `AppendFile failed: {oserror}`
- `DeleteFile failed: {oserror}`

The `{oserror}` text is Python's `str(OSError)`; for the common cases the
native host must match it (e.g. `[Errno 2] No such file or directory:
'<resolved path>'`). Differential tests cover missing-file reads and
unwritable paths.

## 4. Math.* semantics (applied by the host via `__math_call__`)

All operations take/return `[data, kind]` values; unless noted, the result
kind is the operand's kind (unary) or the truth-and of operand kinds
(binary). Numeric domain: ints and floats; int results where noted.

| op | arity | semantics | error text |
|----|-------|-----------|------------|
| `Math.abs` | 1 | `abs(x)`; int→int, float→float | `Math.abs() expects 1 argument` |
| `Math.min` | 2 | `min(a, b)` | `Math.min() expects 2 arguments` |
| `Math.max` | 2 | `max(a, b)` | `Math.max() expects 2 arguments` |
| `Math.pow` | 2 | Python `pow(a, b)` (int**int→int when exact, else float) | `Math.pow() expects 2 arguments` |
| `Math.floor` | 1 | `math.floor(x)` → int | `Math.floor() expects 1 argument` |
| `Math.ceil` | 1 | `math.ceil(x)` → int | `Math.ceil() expects 1 argument` |
| `Math.trunc` | 1 | `math.trunc(x)` → int | `Math.trunc() expects 1 argument` |
| `Math.round` | 1 | `math.floor(x + 0.5)` → int (JS-like ties toward +inf) | `Math.round() expects 1 argument` |
| `Math.sign` | 1 | `1 / -1 / 0` (int) | `Math.sign() expects 1 argument` |
| `Math.clamp` | 3 | `min(max(x, lo), hi)`; kind = truth-and of all three | `Math.clamp() expects 3 arguments`; `Math.clamp() requires min <= max` |
| `Math.sqrt` | 1 | `math.sqrt(x)`; negative → error | `Math.sqrt() expects 1 argument`; `Math.sqrt() domain error` |
| `Math.PI` | 0 | `math.pi` (float, kind truth) | `Math.PI does not take arguments` |
| `Math.E` | 0 | `math.e` (float, kind truth) | `Math.E does not take arguments` |
| `Math.random` | 0 | deterministic LCG, see below | `Math.random() expects 0 arguments` |
| `Math.sin/cos/tan/sinh/cosh/tanh` | 1 | libm equivalents of `math.*` | `<op>() expects 1 argument` |

Deterministic random (must match bit-for-bit across hosts):

```
seed_0 = 123456789
seed_{n+1} = (1103515245 * seed_n + 12345) mod 2^31
random_n = seed_{n+1} / 2^31        (float)
```

The seed persists across calls within one `vm_run`/`vm_call_func` execution
and is part of observable semantics (deterministic random sequence).

## 5. Builtin arity table

The native host embeds the full `BUILTIN_ARITIES` table from
`builtin_registry.py` (public + internal). Dispatch order in the transpiled
VM (`vm_call_builtin`) is normative:

1. Unknown name → error `Unknown built-in <name>`.
2. Arity mismatch → error `<name>() expects <N> arguments, got <M>`.
3. `std.*` aliases require the import marker (`state["imports"]["std"]`),
   else error `std module not imported (use 'import std;')`; unknown member
   falls through to the final unknown-builtin error.
4. `__import_module__`: only `"std"` is allowed, else
   `Unknown module <str(module)>`.
5. `__import_file_module__` → implemented guest-side per §9 (Stage-1 module
   loading). Before Stage-1 it raised `module system not supported by the
   self-hosted VM yet`.
6. `__export_symbol__` → implemented guest-side per §9. Outside a module
   run it raises `export can only be used inside module files`.
7. All remaining builtins per vm.saka's dispatch (kind forcing, bool ops,
   scope ops, `SataAndagi`, `Americaya`, `Getittogether`, `Ohmygah`,
   `Args`, `Panic`, `Say`, `len`, file I/O, `keys`/`values`/`contains`/
   `slice`, `Math.*` prefix delegation, `push`/`pop`, policy builtins,
   `add`).
8. Fall-through → `Unknown builtin <name>`.

Notable exact behaviors the transpiled VM inherits (listed here because
they are easy to get wrong in C):

- `add` builtin computes `args[1].data + args[0].data` (reversed operand
  order, matching Stage 0) and errors with the ADD class-name message.
- `push`/`pop` errors are line-prefixed: `Line <n>: push() requires list`,
  `Line <n>: pop() on empty list`.
- `Say` on a grain value warns `Line <n>: Say() used on grainsoftruth
  (output may be unreliable)`.
- `Panic` message: `Panic: <str(value)>`.
- Policy builtins (`Ah`, `Hecho`, `youknowsealsright`, `Ivebeengot`)
  require a string argument, else `<name> requires variable name`.
- `JMP_IF_FALSE` on a grain condition → `Control flow condition must be
  truthaboutgrain (function=<fname>, line=<n>, value=<repr>)`.
- Comparison type error: `'<', '<=', '>', '>=' not supported between
  instances of '<type>' and '<type>'` (Python `type(x).__name__` texts).
- ADD type error uses Python `repr(type(x))` texts: `ADD operation
  unsupported for types: <class 'int'> and <class 'str'>`.

## 6. SBC1 loader (native JSON parse + canonicalization)

The native loader parses the SBC1 JSON document and applies the same
validation and canonicalization as `sbc._program_from_canonical`. The
legacy pre-contract schema is **not** supported by the native host
(canonical output never uses it; it exists only as a Stage 0 local
migration path).

### 6.1 Document schema (strict)

Top-level keys must be exactly `{constants, format, functions,
language_version, main, version}`:

- `format == "SBC"`, `version == 1`, `language_version == "1.1"`;
- `constants` and `main` are arrays;
- `functions` is an object (string keys).

Violations → `SBC1 document has missing or unknown top-level fields`,
`Unsupported SBC format or version`,
`Unsupported Osaka language version: <v>`, `SBC1 constants and main must
be arrays`, `SBC1 functions must be an object`.

### 6.2 Function records (strict)

Keys exactly `{code, constants, params}`; `params` is a list of strings.
Errors: `SBC1 function names and records are invalid`,
`SBC1 function <name> has invalid fields`,
`SBC1 function <name> has invalid parameters`.

### 6.3 Instructions (strict)

Each instruction is a dict with keys exactly `{op, arg, line}`; `op` is a
string, `line` an int. `arg` may be a scalar, a 2-element list (decoded to
a pair — the C representation of Python's tuple), or null. Error:
`SBC1 instructions must contain exactly op, arg, and line`.

### 6.4 Constants (strict)

Each constant is a dict with keys exactly `{data, kind}`:

- `kind` ∈ `{"truth", "grain"}`, else `Invalid SBC1 value kind: <kind>`;
- `data` is null, bool, int, float (finite), or string, else
  `Unsupported SBC1 constant type: <type>`;
- non-finite floats → `SBC1 constants must use finite floating-point
  values`.

JSON parsing must therefore preserve the int/float/bool/null distinction
(`1` vs `1.0` vs `true` vs `null`) — the C JSON parser tags numbers as int
or double by the presence of `.`/`e`/`E` in the literal, matching
`json.loads`.

### 6.5 Locals derivation

Per function, locals = params (in order) followed by every `STORE_VAR` /
`INDEX_SET` string argument not already seen, in first-appearance order
(`sbc._derive_locals`). The transpiled VM re-derives locals the same way
(`vm_derive_locals`), so the loader's derived list is used for frame
pre-seeding order (insertion order is observable through
`keys(locals)`-driven iteration in `vm_new_frame`).

### 6.6 Golden test

A test compiles the bootstrap bundle with Stage 0, serializes with
`sbc.dumps`, loads it with the native loader, and compares the canonical
re-serialization (or a structural dump) against `sbc.load`'s result. Any
divergence in arg tuples, function tables, or const pools fails the gate.

## 7. Runtime structures the C host must provide

- **Insertion-ordered hash maps.** Python dicts preserve insertion order;
  `keys()`, `values()`, repr/str of maps, and `MAKE_MAP` iteration depend
  on it. The C map must iterate in insertion order (hash table + ordered
  entry list). Re-assigning an existing key does not change its position.
- **Dynamic arrays** with `push`, `pop`, `slice` (copy), in-place indexed
  assignment, and growth-with-null padding for `INDEX_SET` past the end.
- **Byte buffers** for strings; concatenation is O(n) copy; `slice` is a
  copy. Strings are UTF-8 byte sequences; length is byte length (matching
  Python `len()` on the ASCII-dominated bootstrap sources — note: Python
  lengths are code points; the bootstrap sources and corpus are ASCII, and
  any non-ASCII divergence would surface in the differential suite).
- **Value equality** (`CMP_EQ`/`CMP_NE`, map keys, `contains`): deep
  equality for lists/maps, value equality for scalars, tag-aware (bool ≠
  int ≠ float numerically-equal values for `__is_bool__` purposes, but
  `True == 1` compares equal as Python does).
- **Memory management**: reference-counted or arena allocation; the VM is
  short-lived per run, so an arena (free-all at exit) is acceptable if the
  fixed-point run fits in memory.

## 8. Non-semantics (explicitly out of contract)

- Execution traces (`TRACE_POINT`, `__capture_trace__`) are no-ops.
- `SataAndagi` info map: `{"name": "SATA", "version": "1.0", "context": 0,
  "warnings": 0}` (the transpiled VM hardcodes these; `rt` attributes are
  not observable).
- Buffering granularity of stdout: the envelope carries the full stdout
  text; when the process prints it, byte-identity of the stream is
  required, flush timing is not.

## 9. Stage-1: runtime module loading (SBC module artifacts)

Stage-1 adds runtime `import "<path>" as <alias>;` support to the
self-hosted/native VM. Design decision: **the VM never compiles source**.
Modules are pre-compiled SBC1 artifacts; runtime loading reuses the exact
loader machinery of §6 via one new host builtin. All other module logic
(resolution, caching, export collection, injection) is guest-side code in
`selfhost/vm.saka`, so it is verified by the differential suite and flows
through the seed fixed point like the rest of the VM.

Normative reference for semantics: `vm.py` `_load_file_module`,
`__import_file_module__`, `__export_symbol__` (lines 84–141, 271–321) and
`runtime.resolve_module_path`. Error text is byte-normative.

### 9.1 New host builtin: `__sbc_load__(path)` → map, arity 1

`path` is a **resolved** path string (the VM does path resolution itself,
see §9.2, so the host stays free of module state). The host must:

1. Read the file at `path` as UTF-8 text. On I/O failure:
   `ReadFile failed: {oserror}` (same text as `ReadFile`, §3.7).
2. JSON-parse and canonicalize the document exactly per §6 (same schema
   checks, same error texts, same locals derivation).
3. Return the canonical bundle as a guest map value with the same shape
   the VM's own document state uses: `{"constants": [...], "functions":
   {name: {"code": [...], "constants": [...], "params": [...]}},
   "main": [...]}`. Kind `"truth"`.

The Python host implements `__sbc_load__` as a thin wrapper over
`sbc.load(path)`; the native host reuses the §6 loader already in
`native/main.c`. The §6.6 golden test extends to module artifacts.

### 9.2 Path resolution (guest-side, matches `runtime.resolve_module_path`)

- Absolute paths: used as-is (normalized).
- Relative paths: resolved against the directory of the **currently
  executing document** (`state["current_file"]`); for the top-level
  document this is the process working directory. Each module run sets
  `current_file` to the module's resolved path, so nested imports resolve
  relative to the importing module.

### 9.3 Module artifact convention

An import names a `.saka` source path (source of truth for the import
statement); the VM loads the **compiled artifact** at the same path with
the `.sbc` extension substituted (`./modules/mod_values.saka` →
`./modules/mod_values.sbc`). The artifact must have been produced by
`bin/osakac --module` (M7). If the `.sbc` artifact is missing, the error
is `Module file not found: <resolved .sbc path>`.

### 9.4 `__import_file_module__(path, alias)` — guest-side semantics

Executed by vm.saka (transpiled to C by Gate E's transpiler):

1. `resolved` = §9.2 resolution of `path`, then §9.3 artifact mapping.
2. If `resolved` is in `state["module_cache"]` → use the cached module
   object (cache identity is the resolved artifact path; insertion order
   of the cache map is not observable).
3. If `resolved` is in `state["module_loading"]` → unhandled error
   `Circular module import detected: <resolved>`.
4. If the artifact file does not exist → unhandled error
   `Module file not found: <resolved>`.
5. Add `resolved` to `module_loading`; `bundle = __sbc_load__(resolved)`.
6. Run the module bundle in a **fresh VM state** with:
   - `collecting_exports = true`, fresh `current_module_exports` map;
   - `current_file = resolved` (for nested imports);
   - **shared** stdout/stderr/warnings channels with the importing run
     (module execution output is observable);
   - the module bundle's own function table and constants;
   - the importer's math RNG seed passed in, and the module's final seed
     passed back out (the deterministic random sequence spans module
     runs, matching `vm.py`'s seed hand-off).
7. On completion, build the module object
   `{path, exports, functions}` (functions = the bundle's function
   table), store it in `module_cache` under `resolved`, remove `resolved`
   from `module_loading`.
8. Inject exports in insertion order of the exports map. For each
   `export_name`:
   - `fq_name = alias + "." + export_name`;
   - **value export** (`{"type": "value", "value": data, "kind": kind}`):
     store `[data, kind]` into the importer's current global scope under
     `fq_name`; record `var_kinds[fq_name] = kind` and mark initialised;
   - **function export** (`{"type": "function", "name": fn_name}`): look
     up `fn_name` in the module object's function table; if absent →
     unhandled error `Exported function not found in module:
     <export_name>`; else bind the function record into the **current
     frame's function table** under `fq_name`;
   - any other type → unhandled error `Unknown export type for
     <export_name>`.
9. Return the null pair (kind `"truth"`).

### 9.5 `__export_symbol__(name)` — guest-side, arity 1

Only valid while a module run is collecting exports (the module VM state
has `collecting_exports` set); otherwise unhandled error `export can only
be used inside module files`. If `name` is in the module run's function
table, record `{"type": "function", "name": name}`; otherwise record the
variable's current value as `{"type": "value", "value": data, "kind":
kind}` (variable lookup uses the module VM's normal scope-chain rules;
an unbound name follows the usual used-before-assignment warning path).

### 9.6 VM state additions

`state` gains: `module_cache` (insertion-ordered map, resolved path →
module object), `module_loading` (list used as a set), `collecting_exports`
(bool), `current_module_exports` (map or absent), `current_file` (string).
The cache and loading set are shared across nested module runs (a diamond
import loads once; the second importer receives the cached object).

### 9.7 Observable-semantics notes

- Module-run stdout interleaves with the importer's in execution order
  (shared channels), matching the Python VM.
- Warnings emitted during a module run appear in the run's warnings list.
- An unhandled error inside a module run propagates as an unhandled error
  of the whole run (same diagnostic envelope; `where` reflects the
  innermost module frame).
- `__export_symbol__` outside a module run keeps the Stage-0 error text
  (`export can only be used inside module files`) — this is the only
  error text retained from the Stage-1 stubs.

### 9.8 Tests

- Golden: `sbc.load` vs native `__sbc_load__` on each module artifact
  (extends §6.6).
- Differential: value exports, function exports, mixed, nested imports,
  diamond import (cache), circular import (error text), missing artifact
  (error text), export of undefined name, `__export_symbol__` outside a
  module (error text), module stdout interleaving, RNG seed continuity.
- Conformance: `tests/equivalence/14_file_module_values.saka` and
  `16_file_module_functions.saka` are un-excluded once the harness
  pre-compiles their modules to `.sbc` artifacts.
