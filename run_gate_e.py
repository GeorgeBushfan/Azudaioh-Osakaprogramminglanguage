"""Gate E: the compiler bootstrap chain, re-executed on the NATIVE VM.

Gate D proved the chain on the Osaka VM (selfhost/vm.saka interpreted by
Python vm.py). Gate E removes Python from the driver's seat entirely:

    native/osakavm (transpiled vm.saka + C host)  ->  checked-in stage2

The native VM is built by `transpile_vm.py` + `native/Makefile` (see
docs/NATIVE_HOST_CONTRACT.md). Its `call` mode invokes the artifact's
pipeline functions (parser_parse_program, compile_ir, vf_verify, em_dump)
with raw JSON arguments, exactly like Gate D's vm_call_func path.

    stage2.sbc (on the native VM) compiles the bundle sources -> stage2''

Gate E PASSES when SHA256(stage2'') == SHA256(checked-in stage2).
Provenance: bootstrap/GATE_E_PROVENANCE.md.

Usage:
    python3 run_gate_e.py               # resumable full run (checkpointed)
    python3 run_gate_e.py --finish      # compare emit checkpoint, write provenance
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from selfhost_bundle import DEFAULT_ORDER

ROOT = Path(__file__).resolve().parent
BOOTSTRAP = ROOT / "bootstrap"
STAGE2 = BOOTSTRAP / "osakac.stage2.sbc"
PROVENANCE = BOOTSTRAP / "GATE_E_PROVENANCE.md"
HEARTBEAT = BOOTSTRAP / "gate_e_progress.log"
OSAKAVM = ROOT / "native" / "osakavm"
SOURCE_ROOT = ROOT / "selfhost"


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def _bundle_source():
    # Mirrors mn_read_bundle: "\n" + file text per module, in fixed order.
    return "".join(
        "\n" + (SOURCE_ROOT / name).read_text(encoding="utf-8")
        for name in DEFAULT_ORDER
    )


def heartbeat(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(HEARTBEAT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def call_on_native_vm(artifact_path, fname, args):
    """Invoke fname(artifact, *args) on the native VM (osakavm call).

    Returns the vm_call_func envelope {"ok", "value", "diagnostic"}.
    """
    args_path = BOOTSTRAP / ".gate_e_args.json"
    args_path.write_text(json.dumps(args), encoding="utf-8")
    proc = subprocess.run(
        [str(OSAKAVM), "call", str(artifact_path), fname, str(args_path)],
        capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"osakavm exited {proc.returncode}: {proc.stderr[:500]}")
    if not proc.stdout.strip():
        raise RuntimeError(f"osakavm produced no envelope: {proc.stderr[:500]}")
    return json.loads(proc.stdout)


def _ckpt_path(name):
    return BOOTSTRAP / f".ckpt.gatee.{name}.json"


def _load_ckpt(path):
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            path.unlink(missing_ok=True)  # corrupt checkpoint: discard
    return None


def _save_ckpt(path, value):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def run_phase(label, ckpt_name, fn):
    ckpt = _ckpt_path(ckpt_name)
    cached = _load_ckpt(ckpt)
    if cached is not None:
        heartbeat(f"Gate E: [{label}] resumed from checkpoint")
        return cached
    started = time.time()
    heartbeat(f"Gate E: [{label}] running on the native VM ...")
    result = fn()
    _save_ckpt(ckpt, result)
    heartbeat(f"Gate E: [{label}] done in {time.time() - started:.0f}s (checkpointed)")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--finish", action="store_true",
                        help="load the emit checkpoint, compare SHA256 against "
                             "the checked-in stage2, write provenance")
    opts = parser.parse_args()

    stage2_text = read_text(STAGE2)
    stage2_sha = hashlib.sha256(stage2_text.encode()).hexdigest()

    if opts.finish:
        emit_env = _load_ckpt(_ckpt_path("emit"))
        if not (isinstance(emit_env, dict) and emit_env.get("ok") == 1
                and isinstance(emit_env.get("value"), str)):
            raise SystemExit("FATAL: emit checkpoint missing or invalid; "
                             "run the full Gate E first")
        emitted2 = emit_env["value"]
        if not emitted2.startswith('{"constants"'):
            raise SystemExit("FATAL: emit checkpoint is not SBC1 JSON text")
        sha_new2 = hashlib.sha256(emitted2.encode()).hexdigest()
        (BOOTSTRAP / "gate_e_stage2.sbc").write_text(emitted2, encoding="utf-8")
        print(f"  SHA256(stage2'') = {sha_new2}")
        print(f"  SHA256(stage2)  = {stage2_sha}")
        lines = [
            "# Gate E Provenance: Compiler Bootstrap on the Native VM",
            "",
            "The stage chain was executed by `native/osakavm` — the transpiled",
            "`selfhost/vm.saka` (via `transpile_vm.py`) plus the C host runtime",
            "(`native/rt.c`, `builtins.c`, `json.c`, `main.c`). No Python",
            "interpreter is involved at run time. Each phase is checkpointed",
            "under `bootstrap/.ckpt.gatee.*.json`.",
            "",
            "| Stage | SHA256 | Chars |",
            "| --- | --- | --- |",
            f"| stage2 (checked in) | `{stage2_sha}` | {len(stage2_text)} |",
            f"| stage2'' (native VM) | `{sha_new2}` | {len(emitted2)} |",
            "",
        ]
        if sha_new2 == stage2_sha:
            lines.append(
                "**GATE E PASSED**: the native VM executed the full compiler "
                "pipeline (parse -> compile -> verify -> emit) and produced "
                "byte-identical output to the checked-in stage2 artifact "
                "(SHA256(stage2'') == SHA256(stage2)).")
            lines.append("")
            lines.append(
                "Note: generation N+1 was skipped as redundant, matching "
                "Gate D: Gate C proved the fixed point on the Python VM, and "
                "a byte-identical stage2'' makes the N+1 cycle a repeat of "
                "the identical computation.")
            status = "PASSED"
        else:
            lines.append("GATE E FAILED: SHA256(stage2'') != SHA256(stage2).")
            status = "FAILED"
        PROVENANCE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        heartbeat(f"Gate E {status}: provenance written to {PROVENANCE.name}")
        return 0 if status == "PASSED" else 1

    heartbeat(f"Gate E: loaded checked-in stage2 ({len(stage2_text)} chars, {stage2_sha})")

    source = _bundle_source()

    def phase_result(label, fname, args, ckpt_name):
        """Run one pipeline phase; unwrap vm_call_func's envelope and, when
        present, the artifact function's own envelope (em_dump returns the
        SBC text directly); return the raw result."""
        result = run_phase(label, ckpt_name, lambda: call_on_native_vm(
            STAGE2, fname, args))
        if not isinstance(result, dict) or result.get("ok") != 1:
            raise SystemExit(f"FATAL: {label}: {str(result)[:300]}")
        inner = result["value"]
        if isinstance(inner, dict) and "ok" in inner:
            if inner.get("ok") != 1:
                raise SystemExit(f"FATAL: {label}: {str(inner)[:300]}")
            return inner["value"]
        return inner

    ast = phase_result("parse bundle source", "parser_parse_program",
                       [source], "ast")

    doc = phase_result("compile bundle AST", "compile_ir", [ast], "doc")

    phase_result("verify compiled doc", "vf_verify", [doc], "verify")

    emitted2 = phase_result("emit stage2'' SBC text", "em_dump", [doc], "emit")
    if not (isinstance(emitted2, str) and emitted2.startswith('{"constants"')):
        raise SystemExit("FATAL: em_dump did not produce SBC1 JSON text")

    sha_new2 = hashlib.sha256(emitted2.encode()).hexdigest()
    print(f"  SHA256(stage2'') = {sha_new2}")

    lines = [
        "# Gate E Provenance: Compiler Bootstrap on the Native VM",
        "",
        "The stage chain was executed by `native/osakavm` — the transpiled",
        "`selfhost/vm.saka` (via `transpile_vm.py`) plus the C host runtime",
        "(`native/rt.c`, `builtins.c`, `json.c`, `main.c`). No Python",
        "interpreter is involved at run time. Each phase is checkpointed",
        "under `bootstrap/.ckpt.gatee.*.json`.",
        "",
        "| Stage | SHA256 | Chars |",
        "| --- | --- | --- |",
        f"| stage2 (checked in) | `{stage2_sha}` | {len(stage2_text)} |",
        f"| stage2'' (native VM) | `{sha_new2}` | {len(emitted2)} |",
        "",
    ]
    if sha_new2 == stage2_sha:
        lines.append(
            "**GATE E PASSED**: the native VM executed the full compiler "
            "pipeline (parse -> compile -> verify -> emit) and produced "
            "byte-identical output to the checked-in stage2 artifact "
            "(SHA256(stage2'') == SHA256(stage2)).")
        lines.append("")
        lines.append(
            "Note: generation N+1 was skipped as redundant, matching "
            "Gate D: Gate C proved the fixed point on the Python VM, and "
            "a byte-identical stage2'' makes the N+1 cycle a repeat of "
            "the identical computation.")
        status = "PASSED"
    else:
        lines.append("GATE E FAILED: SHA256(stage2'') != SHA256(stage2).")
        status = "FAILED"
    PROVENANCE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    heartbeat(f"Gate E {status}: provenance written to {PROVENANCE.name}")
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())