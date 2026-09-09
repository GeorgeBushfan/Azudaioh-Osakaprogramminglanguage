# Osaka Lang Specification (Working Spec)

This document defines the **current, implementation-aligned behavior** of Osaka Lang in this repository.

> Status: living spec. Where interpreter and VM differ, this spec explicitly calls it out.

---

## 1. Language Overview

Osaka Lang is implemented in Python with:

- Lexer (`lexer.py`)
- Parser/AST (`parser.py`, `ast_nodes.py`)
- AST Interpreter (`interpreter.py`)
- Bytecode compiler + VM (`compiler.py`, `bytecode.py`, `vm.py`)
- Equivalence tools (`equiv_lock.py`, `equiv_test.py`)

Primary source extension: `.saka`

---

## 2. Lexical Structure

### 2.1 Tokens

Current token classes include:

- Literals: `NUMBER`, `FLOAT`, `STRING`
- Identifiers: `IDENT`
- Delimiters/operators: `(` `)` `{` `}` `[` `]` `,` `:` `;` `=` `==` `+` `/` `<` `>`
- Comments: `// ...` (to end of line)

### 2.2 Keywords

Recognized keywords:

- Declarations: `grainsoftruth`, `truthaboutgrain`
- Flow: `if`, `else`, `while`, `try`, `catch`
- Functions: `function`, `func`, `return`
- Governance/meta: `Ah`, `Hecho`, `Ohmygah`, `youknowsealsright`, `Ivebeengot`, `Getittogether`, `Escalator`, `Elevator`
- Module: `import`

---

## 3. Syntax (Current)

### 3.1 Statements

- Variable declarations:
  - `grainsoftruth x = expr;`
  - `truthaboutgrain x = expr;`
- Assignment: `x = expr;`
- Index assignment: `x[i] = expr;`
- Function definitions:
  - `function f(a, b) { ... }`
  - `func f(a, b) { ... }`
- Return: `return expr;`
- Conditionals:
  - `if (cond) { ... }`
  - `if cond { ... }` (non-parenthesized is also accepted)
  - `else if (cond) { ... }` and `else if cond { ... }` (supported as chained conditionals)
  - optional `else { ... }`
- Loop:
  - `while (cond) { ... }`
  - `while cond { ... }` (also accepted)
  - `for (init; cond; step) { ... }` (C-style; parser desugars to `while`)
  - `break;` (exit nearest loop)
  - `continue;` (advance nearest loop)
- Error handling: `try { ... } catch { ... }`
- Block: `{ ... }`
- Calls: `name(args...);`
- Module imports:
  - `import moduleName;`
  - `import "relative/or/absolute/path.saka" as alias;`
- Exports:
  - `export truthaboutgrain x = expr;`
  - `export grainsoftruth x = expr;`
  - `export function f(...) { ... }` (parsed; callable import support pending)
- Declaration-style context statements:
  - `Escalator levelName;`
  - `Elevator levelName;`
- `Getittogether` is parsed as callable form: `Getittogether();`

### 3.2 Expressions

- Literals: numbers, strings
- Variables
- Arithmetic: `+`, `-`, `*`, `/`, `%`
- Comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`
- Boolean operators: `not`, `and`, `or`
- Lists: `[a, b, c]`
- Maps: `{"k": v}` (keys must be string literals)
- Index access: `x[i]`, `m["k"]`
- Function call expressions: `f(...)`

---

## 4. Type/Kind Model

Osaka Lang tracks **kind** (not static type):

- `truthaboutgrain` => kind `truth`
- `grainsoftruth` => kind `grain`

General propagation behavior:

- Arithmetic/comparison results are `truth` only if both operands are `truth`; otherwise `grain`
- `float` numeric literals currently evaluate as `grain`; integer literals as `truth`
- List/map literals are `truth` only if all contained evaluated elements are `truth`

### 4.1 Truth/Grain Propagation Rules (Canonical)

This section is the quick-reference for kind propagation.

| Construct | Result kind |
|---|---|
| `truthaboutgrain x = expr` | `truth` (forced by declaration) |
| `grainsoftruth x = expr` | `grain` (forced by declaration) |
| `x = expr` (existing variable) | keeps existing variable kind |
| `x = expr` (new variable) | expression kind |
| Integer literal | `truth` |
| Float literal | `grain` |
| String literal | `truth` |
| Variable read | stored variable kind |
| `a + b`, `a - b`, `a * b`, `a / b`, `a % b` | `truth` iff both operands are `truth`; else `grain` |
| `a == b`, `a != b`, `a < b`, `a <= b`, `a > b`, `a >= b` | `truth` iff both operands are `truth`; else `grain` |
| `not x` | preserves `x` kind |
| `x and y`, `x or y` | short-circuit; final kind is `truth` iff both sides are `truth`; else `grain` |
| List literal `[e1, e2, ...]` | `truth` iff all element kinds are `truth`; else `grain` |
| Map literal `{"k": v, ...}` | `truth` iff all value kinds are `truth`; else `grain` |
| Index read `container[i]` | container kind |
| Index write of `grain` value | may downgrade container variable kind to `grain` |
| Function parameters (inside function scope) | bound as `grain` by current runtime rule |
| Function return | return expression kind |
| `Americaya(x)` | `truth` (promotion) |

Notes:

- Kind propagation is separate from mutation/governance diagnostics (`Ah`, `Hecho`, `Ivebeengot`, etc.).
- `Say(grain)` warns, but does not itself change kind propagation.

---

## 5. Control Flow Semantics

Boolean expression precedence:

1. `not`
2. `and`
3. `or`

`and`/`or` are short-circuiting (right-hand side is evaluated only when needed).

### 5.1 Intended Rule

`if` and `while` conditions must be `truthaboutgrain` (`kind == "truth"`).

### 5.2 Interpreter Behavior

Interpreter enforces this strictly:

- `if` with `grain` condition -> runtime error: `if-condition must be truthaboutgrain`
- `while` with `grain` condition -> runtime error: `while-condition must be truthaboutgrain`

### 5.3 VM Behavior Caveat

VM code path currently applies `__to_truthaboutgrain__` conversion in compiled control-flow paths before `JMP_IF_FALSE` checks.

Implication:

- VM is close to intended semantics, but may not match strict interpreter behavior in every edge case.
- Use equivalence tests as the parity gate while aligning backend behavior.

---

## 6. Variables, Mutation, and Governance

Runtime policy sets:

- `acknowledged` via `Ah(x)`
- `assumed` via `youknowsealsright(x)`
- `legacy` via `Ivebeengot(x)`
- `completed` via `Hecho(x)`

Current policy behavior:

- Mutation of initialized variables without `Ah`/assumed can emit warning
- `Ivebeengot` blocks reassignment/modification
- `Hecho` blocks reassignment/modification
- `Hecho` on grain variables produces advisory behavior (not hard promote)

---

## 7. Functions

- Defined with `function` or `func`
- Support positional parameters
- `return expr` required for meaningful return values
- Call arity enforced at runtime/verification layers

Current interpreter rule:

- Function parameters are bound as `grain` in function scope unless promoted.

---

## 8. Built-ins (Current)

Core built-ins:

- `Say(x)`
- `len(x)`
- `keys(map)`
- `values(map)`
- `contains(map, key)`
- `slice(list_or_string, start, end)`
- `push(list, value)`
- `pop(list)`
- `Args()` — returns the arguments supplied after `--` to the running Osaka
  program as a truth-kind list of strings.
- `Panic(message)` — terminates the current run with a normalized runtime
  error. Compiler tools use it to report fatal diagnostics and a non-zero exit.

Math object built-ins:

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

Governance/meta built-ins:

- `Ah(x)`
- `Hecho(x)`
- `youknowsealsright(x)`
- `Ivebeengot(x)`
- `Getittogether()`
- `SataAndagi()`
- `Americaya(x)`
- `Ohmygah(x)` (soft warning path)

Module namespace behavior:

- `import std;` is supported.
- Namespaced std calls supported: `std.len`, `std.keys`, `std.values`, `std.contains`, `std.slice`, `std.push`, `std.pop`.
- `Say` remains a core builtin: use `Say(...)`, not `std.Say(...)`.

File-module behavior:

- Path imports are supported via `import "..." as alias;`.
- Runtime resolves relative paths from the currently executing source file.
- Module loading is cached per absolute path.
- Circular imports are detected and raise runtime errors.
- `export` currently supports value exports end-to-end (interpreter + VM/compiler).
- Exported functions are callable through file-module imports.
- Imported module values are currently referenced via `alias.name()` call-expression form.

---

## 9. Runtime and Output

- Program-visible output is accumulated in runtime stdout buffer (used by CLI/harness)
- Warnings are tracked and also surfaced in diagnostics output
- Execution traces are captured for equivalence tooling

---

## 10. CLI Contract (Current)

Entry point: `osaka` (or `python3 saka.py`)

Supported flags:

- `--repl`
- `--no-lock`
- `--vm-only`
- `--interpreter-only`
- `--lock-strict`
- `--warnings-as-errors` (flag exists; behavior may be partial/not fully wired)
- `--debug`
- `--show-lexer-tokens`

If a run produces no output, CLI may print:

- `Tip: Use --debug for traces`

---

## 11. Bytecode/VM Contract (Current)

Core opcodes include:

- Stack/vars: `PUSH_CONST`, `LOAD_VAR`, `STORE_VAR`
- Arithmetic: `ADD`, `DIV`
- Collections: `MAKE_LIST`, `MAKE_MAP`, `INDEX_GET`, `INDEX_SET`
- Calls/control: `CALL_BUILTIN`, `CALL_FUNC`, `RET`, `JMP`, `JMP_IF_FALSE`, `HALT`
- Compare: `CMP_EQ`, `CMP_LT`, `CMP_GT`
- Tracing/try-catch: `TRACE_POINT`, `TRY_PUSH`, `TRY_POP`

Verifier (`verifier.py`) currently enforces:

- known opcodes
- stack discipline (no underflow)
- builtin/function arity checks
- main block must end with `HALT`
- function blocks must end with `RET`

---

## 12. Compatibility and Conformance

Because this project has dual execution paths, correctness target is:

1. Interpreter semantics (reference behavior)
2. VM/compiler parity with interpreter
3. Equivalence lock pass on canonical tests

Recommended conformance commands:

```bash
python3 run_tests.py
python3 -m unittest tests/test_verifier.py
python3 equiv_lock.py tests/equivalence/06_sataandagi.saka
```

---

## 13. Spec Maintenance Notes

When semantics change:

1. Update this `SPEC.md`
2. Update `README.md` user-facing behavior notes
3. Update interpreter + compiler/VM implementations
4. Update verifier arity/stack expectations if needed
5. Add/adjust tests (especially `tests/equivalence/`)
