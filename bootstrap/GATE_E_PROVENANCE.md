# Gate E Provenance: Compiler Bootstrap on the Native VM

The stage chain was executed by `native/osakavm` — the transpiled
`selfhost/vm.saka` (via `transpile_vm.py`) plus the C host runtime
(`native/rt.c`, `builtins.c`, `json.c`, `main.c`). No Python
interpreter is involved at run time. Each phase is checkpointed
under `bootstrap/.ckpt.gatee.*.json`.

| Stage | SHA256 | Chars |
| --- | --- | --- |
| stage2 (checked in) | `c1b04df02fe0fe91130ce60602d0ffca485ee856f2d528e6cf3b16658da45583` | 1183901 |
| stage2'' (native VM) | `c1b04df02fe0fe91130ce60602d0ffca485ee856f2d528e6cf3b16658da45583` | 1183901 |

**GATE E PASSED**: the native VM executed the full compiler pipeline (parse -> compile -> verify -> emit) and produced byte-identical output to the checked-in stage2 artifact (SHA256(stage2'') == SHA256(stage2)).

Note: generation N+1 was skipped as redundant, matching Gate D: Gate C proved the fixed point on the Python VM, and a byte-identical stage2'' makes the N+1 cycle a repeat of the identical computation.
