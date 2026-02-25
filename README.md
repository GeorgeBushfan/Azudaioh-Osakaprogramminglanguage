# OsakaProgrammingLanguage (Osaka Lang)

Osaka Lang is a custom programming language implemented in Python, with:

- an **AST interpreter**
- a **bytecode compiler + VM**
- an **Equivalence Lock** to compare interpreter and VM behavior

You can run it from source (`python3 saka.py`) or as an installed CLI (`osaka`).

---

## Quick Start

```bash
python3 saka.py examples/hello.saka
python3 saka.py --help
```

### Main CLI flags

- `--no-lock` — disable equivalence lock
- `--vm-only` — execute only VM path
- `--interpreter-only` — execute only interpreter path
- `--lock-strict` — fail on lock mismatches
- `--debug` — enable debug traces
- `--show-lexer-tokens` — print lexer token stream
- `--repl` — launch interactive Osaka REPL

Example:

```bash
python3 saka.py --show-lexer-tokens tests/equivalence/06_sataandagi.saka
```

By default, program output from Say(...) is printed to stdout. If nothing is printed, you’ll see:
```text
Tip: Use --debug for traces
```

---

## Install CLI (`osaka`)

Install from PyPI (recommended):

```bash
python3 -m pip install --upgrade OsakaProgrammingLanguage
```

Then verify:

```bash
osaka --help
```

For local development:

```bash
python3 -m pip install --upgrade "/absolute/path/to/Osaka Lang"
```

Then:

```bash
osaka --help
osaka your_program.saka
osaka --repl
```

---

## Language Guide

### 1) Declarations and kinds

Osaka Lang tracks value kinds:

- `grainsoftruth` → `grain` (uncertain)
- `truthaboutgrain` → `truth` (authoritative)

```saka
grainsoftruth g = 11;
truthaboutgrain t = Americaya(g);
Say(t);
```

### 2) Control flow

`if` and `while` are intended to require **truthaboutgrain** conditions at runtime.

```saka
truthaboutgrain ok = 1;
if (ok == 1) {
    Say("ready");
} else if (ok == 2) {
    Say("fallback");
} else {
    Say("nope");
}

`for` loops are supported in C-style form and desugar to `while` internally:

```saka
for (i = 0; i < 10; i = i + 1) {
    Say(i);
}
```
```

Interpreter behavior is strict: if a condition evaluates to `grain`, it raises an error (for example: `while-condition must be truthaboutgrain`).

VM behavior is currently close but not perfectly identical in every edge case, so treat strict truth/grain enforcement as the language target and verify parity using the equivalence tests.

Boolean operators are supported in expressions and conditions:

- `not x`
- `x and y`
- `x or y`

Precedence (high → low): `not`, `and`, `or`.

`and` and `or` use **short-circuit evaluation**.

Loop control statements are supported:

- `break;` exits the nearest loop
- `continue;` jumps to the next iteration of the nearest loop

Arithmetic operators supported: `+`, `-`, `*`, `/`, `%`.

Comparison operators supported: `==`, `!=`, `<`, `<=`, `>`, `>=`.

### 3) Functions

Both `function` and `func` are accepted.

```saka
func add(a, b) {
    return a + b;
}

truthaboutgrain x = add(2, 3);
Say(x);
```

Current interpreter rule: function parameters are bound as `grain` inside function scope unless promoted.

### 4) Collections

- Lists: `[1, 2, 3]`
- Maps: `{"name": "SATA", "level": 6}`
- Indexing: `arr[i]`, `m["key"]`
- Index assignment: `arr[i] = value;`

```saka
truthaboutgrain nums = [1, 2];
push(nums, 3);
Say(nums);

truthaboutgrain profile = {"name": "SATA"};
Say(profile["name"]);
```

### 5) Mutation & governance built-ins

- `Ah(x)` — acknowledges variable for mutation
- `youknowsealsright(x)` — marks variable as assumed (suppresses mutation warning path)
- `Ivebeengot(x)` — legacy protection (blocks later mutation)
- `Hecho(x)` — freezes a variable when allowed

Typical behavior:

- mutating an initialized variable without `Ah`/assumption can warn
- mutating `Hecho`/`Ivebeengot` variables is blocked
- `Hecho` on `grainsoftruth` emits advisory info in current runtime behavior

### 6) Runtime/system built-ins

- `Getittogether();` — stabilizes unresolved grain state
- `SataAndagi()` — returns runtime info map as truth
- `Americaya(x)` — promotes value to truth (returns promoted value)

```saka
grainsoftruth raw = 42;
Getittogether();
truthaboutgrain stable = Americaya(raw);
Say(stable);
```

### 6.1) Math Object (MVP)

Object-style math calls are supported:

- `Math.abs(x)`
- `Math.min(a, b)`
- `Math.max(a, b)`
- `Math.pow(a, b)`
- `Math.floor(x)`
- `Math.ceil(x)`
- `Math.PI`
- `Math.E`
- `Math.sqrt(x)`
- `Math.round(x)`
- `Math.trunc(x)`
- `Math.sign(x)`
- `Math.clamp(x, min, max)`
- `Math.random()`
- `Math.sin(x)`
- `Math.cos(x)`
- `Math.tan(x)`
- `Math.sinh(x)`
- `Math.cosh(x)`
- `Math.tanh(x)`

### 6.2) Module imports (`import std;`)

Osaka supports importing the standard library module namespace:

```saka
import std;

truthaboutgrain nums = [1, 2];
Ah(nums);
std.push(nums, 3);
Say(std.len(nums));
```

Rules:

- `import std;` is currently the only supported module import.
- Namespaced std calls supported: `std.len`, `std.keys`, `std.values`, `std.contains`, `std.slice`, `std.push`, `std.pop`.
- `Say` remains a core builtin and is used as `Say(...)` (not `std.Say(...)`).

### 7) Context progression statements

Parser accepts these as statement-style declarations:

```saka
Escalator reviewLevel2;
Elevator policyLevel;
```

> Note: current parser rules treat `Getittogether` as a callable form with parentheses,
> while `Escalator`/`Elevator` are declaration-style statements.

---

## Standard Library (currently implemented)

- `Say(x)`
- `len(x)`
- `push(list, value)`
- `pop(list)`
- `contains(map, key)`
- `keys(map)`
- `values(map)`
- `slice(list_or_string, start, end)`
- `import std;` with namespaced calls:
  - `std.len(x)`
  - `std.keys(map)`
  - `std.values(map)`
  - `std.contains(map, key)`
  - `std.slice(list_or_string, start, end)`
  - `std.push(list, value)`
  - `std.pop(list)`
- `SataAndagi()`
- `Americaya(x)`

---

## Testing

Run a program through the main CLI:

```bash
python3 saka.py examples/hello.saka
```

Run direct interpreter-vs-VM equivalence harness:

```bash
python3 equiv_lock.py tests/equivalence/06_sataandagi.saka
```

Full test runner (project utility):

```bash
python3 run_tests.py
```

Single equivalence test:

```bash
python3 run_tests.py --filter 06_sataandagi.saka
```

Verifier unit tests:

```bash
python3 -m unittest tests/test_verifier.py
```

---

## Implementation Map (contributors)

- `lexer.py` — tokenization / keywords
- `parser.py` — AST construction
- `ast_nodes.py` — AST node types
- `interpreter.py` — interpreter semantics
- `compiler.py`, `bytecode.py`, `vm.py` — compile + VM execution
- `verifier.py` — bytecode / signature checks
- `equiv_lock.py`, `equiv_test.py` — interpreter vs VM comparison
- `arg_parser.py`, `saka.py` — CLI

When changing language semantics:

1. Update interpreter behavior.
2. Mirror behavior in compiler/VM.
3. Update verifier rules if builtin signatures or op behavior changed.
4. Add/adjust tests (especially `tests/equivalence/`).
5. Re-check equivalence output before shipping.
````