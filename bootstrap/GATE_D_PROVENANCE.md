# Gate D Provenance: Compiler Bootstrap on the Osaka VM

The stage chain was executed by `selfhost/vm.saka` (the self-hosted
VM) via `vm_call_func`, with the Python VM (`vm.py`) as the outer
host. Each phase is checkpointed under `bootstrap/.ckpt.gated.*.json`.

| Phase | Duration |
| --- | --- |
| parse bundle source | 43426s (12h04m) |
| compile bundle AST | 3059s (51m) |
| verify compiled doc | 9429s (2h37m) |
| emit stage2' SBC text | 56637s (15h44m) |

| Stage | SHA256 | Chars |
| --- | --- | --- |
| stage2 (checked in) | `c1b04df02fe0fe91130ce60602d0ffca485ee856f2d528e6cf3b16658da45583` | 1183901 |
| stage2' (Osaka VM) | `c1b04df02fe0fe91130ce60602d0ffca485ee856f2d528e6cf3b16658da45583` | 1183901 |

**GATE D PASSED**: the Osaka VM executed the full compiler pipeline (parse -> compile -> verify -> emit) and produced byte-identical output to the checked-in stage2 artifact (SHA256(stage2') == SHA256(stage2)).

Note: generation N+1 (re-parse/re-compile/re-emit of the emitted text on the Osaka VM) was skipped as redundant: Gate C already proved the fixed point on the Python VM, and a byte-identical stage2' makes the N+1 cycle a repeat of the identical computation.
