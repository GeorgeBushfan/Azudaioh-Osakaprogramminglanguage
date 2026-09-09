# Osaka Self-Hosting Architecture

## Status

Progress: **Gate C is PASSED.** The compiler fixed point is reached and
verified: `SHA256(osakac.stage2.sbc) == SHA256(osakac.stage3.sbc)`, recorded
in `bootstrap/GATE_C_PROVENANCE.md` with the artifacts under `bootstrap/`.
This is the definition of compiler self-hosting.

Gates A and B are complete. The self-hosted compiler backend
(`bytecode.saka` + `compiler.saka`) passes per-construct equivalence;
`verifier.saka` matches Stage 0 verifier verdicts on positives,
self-compiled output, and malformed documents; and `emit_sbc.saka`
serializes the SBC1 document byte-identically to `sbc.dumps` — verified on
the full bootstrap document (`test_selfhost_emit_sbc_matches_stage0_bytes`).

`main.saka` provides the compiler CLI (`osakac <source_root>
<output_path>`): it reads the bundle modules in a hardcoded order mirroring
`selfhost_bundle.DEFAULT_ORDER`, runs lex → parse → compile → verify →
serialize entirely in Osaka. `run_gate_c.py` drives the bootstrap chain
(stage1 built by Stage 0; stage2 and stage3 produced by the artifacts
themselves) and compares `SHA256(stage2)` with `SHA256(stage3)`. The gate
driver checkpoints each pipeline phase to `bootstrap/.ckpt.<stage>.*.json`
so a crash loses at most one phase, and writes the provenance record on
success.

Gate D (runtime parity) is in progress: `selfhost/vm.saka` is a full 1:1
Osaka port of Stage 0's `vm.py` (all SBC1 opcodes, frames/scopes,
try-handler unwinding, kind tracking, policy warnings, and the reflection/
file-I/O host boundary). `tests/test_selfhost_vm.py` runs a 28-case
differential suite — every guest program is executed on both the Python VM
and the Osaka VM and stdout, warnings, and unhandled-error text must match
exactly. All 28 cases pass. Remaining for Gate D: the conformance corpus on
the Osaka VM, `run_gate_d.py` (fixed point with stage runs executed on the
Osaka VM itself), and the provenance record.

This document defines the bootstrap architecture and the criteria used by this
repository to describe Osaka as self-hosting. It is normative for self-hosting
work. `SPEC.md` remains the source-language specification and
`docs/BYTECODE.md` defines the VM boundary.

## Goals

Osaka will be bootstrapped in three independently testable stages:

1. **Self-hosted compiler** — the lexer, parser, lowering pass, verifier, and
   compiler are written in Osaka and execute on the Python VM.
2. **Self-hosted implementation** — an Osaka-written VM executes Osaka
   bytecode, initially while that VM itself runs on the Python VM.
3. **Python-independent toolchain** — a small native host loads the stable
   bytecode format and runs the self-hosted compiler without Python.

The stages are intentionally separate. Replacing the compiler and VM at the
same time would remove the current Python implementation as a useful semantic
oracle and make bootstrap failures difficult to isolate.

## Trust and bootstrap chain

The Python implementation is **Stage 0**. It is the reference implementation
used to produce the first self-hosted compiler artifact:

```text
Python Stage 0 compiler
        │ compiles selfhost/main.saka
        ▼
osakac.stage1.sbc
        │ compiles the same selfhost sources
        ▼
osakac.stage2.sbc
        │ compiles the same selfhost sources
        ▼
osakac.stage3.sbc
```

The compiler has reached a fixed point when the canonical Stage 2 and Stage 3
containers are byte-for-byte identical:

```text
SHA256(osakac.stage2.sbc) == SHA256(osakac.stage3.sbc)
```

Stage 1 is not required to match Stage 2 because it is produced by a different
compiler implementation. Bootstrap artifacts must not contain timestamps,
machine-specific absolute paths, random identifiers, or iteration-order-
dependent output.

## Authoritative boundaries

Self-hosting work must preserve the following boundaries:

- `SPEC.md` defines source-language syntax and semantics.
- `docs/BYTECODE.md` defines the portable compiler/VM interface.
- `runtime.Runtime` is the single Python runtime-state implementation.
- The AST interpreter is the Stage 0 semantic oracle until a behavior is
  explicitly changed in `SPEC.md`.
- Canonical token, AST, bytecode, diagnostic, and execution-result forms are
  implementation-neutral data. They must not expose Python class names or
  object identity.

## Bootstrap language profile

The first Osaka compiler should use a deliberately small source-language
profile. Required constructs are:

- integers, strings, lists, and string-keyed maps;
- variable declarations and assignment;
- list/map indexing and mutation;
- arithmetic, comparisons, and short-circuit boolean operators;
- `if`, `while`, `break`, and `continue`;
- named functions, positional arguments, and explicit returns;
- file modules and exported functions;
- deterministic file I/O and command-line argument access.

The bootstrap compiler must not depend on classes, inheritance, closures,
reflection, macros, concurrency, or native code generation.

Stage 1 is compiled from a deterministic, statically ordered bundle. The
compiler artifact performs no dynamic Osaka module loading: all bootstrap
functions are linked into one SBC function table by `selfhost_bundle.py`.
`bootstrap_profile.py` rejects imports, exports, try/catch, context declarations,
namespaced calls, and unresolved calls from this first profile.

The profile's host surface is: `Args`, `Panic`, `Americaya`, `ReadFile`, `WriteFile`,
`AppendFile`, `FileExists`, `DeleteFile`, `Say`, `len`, `keys`, `values`,
`contains`, `slice`, `push`, and `pop`. `builtin_registry.py` is the Stage 0
source of truth for all public/internal built-in arities.

Compiler data structures use tagged maps. For example:

```saka
truthaboutgrain node = {
    "tag": "binary",
    "op": "+",
    "left": leftNode,
    "right": rightNode,
    "line": 7
};
```

## Planned self-hosted modules

```text
selfhost/
  support.saka     collection/string/span helpers used by every compiler phase
  diagnostic.saka  canonical lexer/parser/compiler diagnostic records
  token.saka       token constructors and token-kind constants
  lexer.saka       cursor-based scanner
  ast.saka         canonical tagged-map AST constructors
  parser.saka      recursive-descent parser
  lower.saka       syntax sugar to core-AST lowering
  compiler.saka    two-pass bytecode compiler
  emit_sbc.saka    deterministic SBC serialization
  verifier.saka    emitted-bytecode checks
  main.saka        compiler command-line entry point
  vm.saka          SBC virtual machine (implemented after compiler fixed point)
```

Generated compiler artifacts and their provenance belong under `bootstrap/`,
not under `selfhost/`.

## Conformance strategy

### Frontend equivalence

The Stage 0 and self-hosted frontends are compared using canonical forms:

- token streams include kind, decoded value, lexeme span, line, and column;
- AST nodes are recursively ordered maps/lists with stable fields;
- lexical and syntax failures compare diagnostic code and source span rather
  than implementation-specific exception wording.

Canonical frontend positions use zero-based Unicode-character offsets,
one-based lines and columns, and exclusive end positions. Canonical token
streams include an explicit `EOF` record. The Stage 0 adapter for these records
is `frontend_contract.py`; Stage 0 tuple tokens and Python AST classes are not a
bootstrap interface.

### Compiler equivalence

The Python and Osaka compilers may use different internal algorithms. Their
outputs are equivalent when they:

- pass the same bytecode verifier;
- produce the same observable behavior on the conformance corpus;
- preserve documented kind, scope, call, module, and error semantics; and
- are deterministic for identical source and compiler options.

Exact bytecode identity between Python and Osaka compilers is useful but is not
required. Exact identity is required between successive self-compilation
generations after the fixed point.

### Runtime equivalence

Runtime comparison uses semantic checkpoints rather than raw trace count:

- program stdout and diagnostics;
- normalized runtime errors and exit status;
- exported module values;
- deterministic random sequence;
- selected variable state at explicit checkpoints.

Instrumentation detail, timestamps, object addresses, and internal frame count
are not language semantics.

## Required validation gates

### Gate A — Stable Stage 0

- Every supported construct has one documented interpreter/VM behavior.
- Positive, negative, verifier, and equivalence suites run from one command.
- Compiled programs have verified control flow and balanced stack behavior.
- Bytecode serialization is deterministic and round-trips without semantic
  change.

### Gate B — Self-hosted frontend

- The Osaka lexer and parser match canonical Stage 0 outputs over all tracked
  `.saka` files and dedicated malformed-input tests.

### Gate C — Self-hosted compiler

- The Osaka compiler compiles the complete bootstrap source set.
- Its programs pass the conformance suite on the Python VM.
- Stage 2 and Stage 3 compiler artifacts have identical SHA-256 hashes.

This is the definition of compiler self-hosting.

### Gate D — Osaka VM

- The Osaka VM passes the bytecode conformance suite.
- It can run the self-hosted compiler and preserve the compiler fixed point.

This is the definition of a self-hosted implementation whose outer host is
still Python.

### Gate E — Native host

- A clean supported machine can build or install the native VM without Python.
- The native VM runs the checked-in seed compiler and reproduces the fixed
  point.
- The native and Python VMs pass the same bytecode conformance suite.

This is the definition of a Python-independent Osaka toolchain.

## Bootstrap artifact policy

Each checked-in seed compiler must include:

- the canonical `.sbc` artifact;
- SHA-256 checksum;
- source revision used to build it;
- Stage 0 command used to build it;
- language and bytecode format versions;
- a successful fixed-point verification record.

Seed updates require review like source changes. A seed may not be replaced by
an artifact that cannot be reproduced from tracked source.

## Non-goals for the first bootstrap

- Native machine-code generation.
- Optimizing compilation.
- A garbage collector more sophisticated than required by the native VM.
- Removing the Python reference implementation.
- Reproducing accidental Python implementation behavior that is not part of
  `SPEC.md`.
