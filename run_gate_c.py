"""Gate C bootstrap chain driver.

Produces the compiler artifacts described in docs/SELF_HOSTING.md:

    Stage 0 (this Python compiler) compiles the full selfhost bundle
        -> bootstrap/osakac.stage1.sbc
    stage1.sbc (running on the Python VM) compiles the same sources
        -> bootstrap/osakac.stage2.sbc
    stage2.sbc compiles the same sources again
        -> bootstrap/osakac.stage3.sbc

Compiler self-hosting (Gate C) is proven when

    SHA256(osakac.stage2.sbc) == SHA256(osakac.stage3.sbc)

A provenance record is written to bootstrap/GATE_C_PROVENANCE.md.

Usage: python3 run_gate_c.py            # resumable: completed stages AND
                                        # completed pipeline phases are skipped

Resumability: each stage run drives the artifact's own pipeline phases
(lex+parse -> compile -> verify -> serialize) as separate VM invocations of
the loaded artifact's pure functions, checkpointing the (JSON-safe) result
of each phase to bootstrap/.ckpt.<stage>.*.json. A crash therefore loses at
most one phase, not the whole stage. The functions and inputs are exactly
those of the artifact's mn_compile_bundle/mn_main pipeline, so the produced
artifact is the one that pipeline defines.

Monitor with: ./gate_status.sh
"""
import contextlib
import faulthandler
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from bootstrap_profile import validate_bootstrap_ast
from bytecode import CALL_FUNC, HALT, PUSH_CONST, BytecodeProgram, Value
from compiler import Compiler
from runtime import Runtime
from sbc import LANGUAGE_VERSION, VERSION, dump, load
from selfhost_bundle import DEFAULT_ORDER, bundle_ast
from verifier import verify_program
from vm import VM

ROOT = Path(__file__).resolve().parent
BOOTSTRAP = ROOT / "bootstrap"
STAGE1 = BOOTSTRAP / "osakac.stage1.sbc"
STAGE2 = BOOTSTRAP / "osakac.stage2.sbc"
STAGE3 = BOOTSTRAP / "osakac.stage3.sbc"
PROVENANCE = BOOTSTRAP / "GATE_C_PROVENANCE.md"
SOURCE_ROOT = ROOT / "selfhost"


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _call_in_program(program, fname, args):
    """Invoke fname(*args) inside the loaded artifact program on a fresh VM.

    Mirrors selfhost_harness.invoke, but against the loaded .sbc artifact so
    the gate exercises the artifact's own code, not a Stage-0 recompile.
    """
    consts = list(program.consts)
    code = []
    for arg in args:
        consts.append(Value(arg, "truth"))
        code.append((PUSH_CONST, len(consts) - 1, -1))
    code.extend(((CALL_FUNC, (fname, len(args)), -1), (HALT, None, -1)))
    patched = BytecodeProgram(consts=consts, code=code, functions=program.functions)
    print(f"    [call {fname}] verifying patched program ...", flush=True)
    verify_program(patched)
    print(f"    [call {fname}] verify done; executing ...", flush=True)
    runtime = Runtime()
    runtime.capture_traces = False
    vm = VM(runtime)
    vm.run(patched)
    frame = vm.frames[-1] if vm.frames else None
    return frame.stack[-1].data if frame and frame.stack else None


def _ckpt_path(stage_key, name):
    return BOOTSTRAP / f".ckpt.{stage_key}.{name}.json"


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


def _bundle_source():
    # Mirrors mn_read_bundle: "\n" + file text per module, in fixed order.
    return "".join(
        "\n" + (SOURCE_ROOT / name).read_text(encoding="utf-8")
        for name in DEFAULT_ORDER
    )


def run_stage_phased(stage_path, output_path, stage_key):
    """Run one stage via the artifact's own pipeline, checkpointed per phase.

    Phases mirror mn_compile_bundle exactly:
        source -> parser_parse_program -> compile_ir -> vf_verify -> em_dump
    """
    program = load(str(stage_path))

    def phase(label, ckpt_name, fn):
        ckpt = _ckpt_path(stage_key, ckpt_name)
        cached = _load_ckpt(ckpt)
        if cached is not None:
            print(f"  [{label}] resumed from checkpoint", flush=True)
            return cached
        started = time.time()
        print(f"  [{label}] running ...", flush=True)
        result = fn()
        _save_ckpt(ckpt, result)
        print(f"  [{label}] done in {time.time() - started:.0f}s "
              f"(checkpointed)", flush=True)
        return result

    source = _bundle_source()

    parsed = phase("lex+parse", "ast", lambda: _call_in_program(
        program, "parser_parse_program", [source]))
    if not isinstance(parsed, dict) or parsed.get("ok") != 1:
        print(f"FATAL: {stage_path.name}: parse failed: {parsed!r}")
        raise SystemExit(1)

    compiled = phase("compile", "doc", lambda: _call_in_program(
        program, "compile_ir", [parsed["value"]]))
    if not isinstance(compiled, dict) or compiled.get("ok") != 1:
        print(f"FATAL: {stage_path.name}: compile failed: {compiled!r}")
        raise SystemExit(1)

    verified = _call_in_program(program, "vf_verify", [compiled["value"]])
    if not isinstance(verified, dict) or verified.get("ok") != 1:
        print(f"FATAL: {stage_path.name}: verify failed: {verified!r}")
        raise SystemExit(1)

    text = phase("serialize", "sbc_text", lambda: _call_in_program(
        program, "em_dump", [compiled["value"]]))
    output_path.write_text(text, encoding="utf-8")

    for name in ("ast", "doc", "sbc_text"):
        _ckpt_path(stage_key, name).unlink(missing_ok=True)
    return True


def _newest_source_mtime():
    newest = Path(__file__).stat().st_mtime
    for path in SOURCE_ROOT.glob("*.saka"):
        newest = max(newest, path.stat().st_mtime)
    return newest


class _Heartbeat:
    """Print a keepalive line every 60s so long stage runs are distinguishable
    from a dead process when the output is a log file. Includes RSS so a
    memory-growth failure mode is visible in the log itself."""

    def __init__(self, label):
        self.label = label
        self.started = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        pid = os.getpid()
        while not self._stop.wait(60):
            elapsed = time.time() - self.started
            rss_mb = 0
            try:
                out = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(pid)],
                    capture_output=True, text=True,
                ).stdout.strip()
                rss_mb = int(out or 0) // 1024
            except Exception:
                pass
            print(f"  ... still running: {self.label} "
                  f"({elapsed / 60:.0f}m elapsed, "
                  f"heartbeat {time.strftime('%H:%M:%S')}, "
                  f"rss {rss_mb} MB)", flush=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        return False


def _install_crash_diagnostics():
    """Make any external kill diagnosable: periodic Python tracebacks to a
    side file and a log line for termination signals."""
    signal_path = BOOTSTRAP / "GATE_C_LAST_STATE.log"

    def _log(message):
        with open(signal_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {message}\n")

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, lambda s, f, _sig=sig: (
                _log(f"received signal {_sig.name} — terminating"),
                sys.exit(128 + int(_sig)),
            ))
        except (ValueError, OSError, AttributeError):
            pass
    state_file = open(BOOTSTRAP / "GATE_C_TRACEBACKS.log", "w")
    # Also capture hard crashes (segfaults etc.) that leave no Python traceback.
    faulthandler.enable(file=state_file)
    faulthandler.dump_traceback_later(300, repeat=True, file=state_file)
    _log(f"gate driver started (pid {os.getpid()})")


def main():
    BOOTSTRAP.mkdir(exist_ok=True)
    _install_crash_diagnostics()

    # ---- Stage 1: the Python Stage 0 compiler builds the seed artifact. ----
    # Resumable: rebuilt only when missing or older than the newest tracked
    # selfhost source / this driver.
    source_newer = _newest_source_mtime()
    if STAGE1.is_file() and STAGE1.stat().st_mtime > source_newer:
        print(f"Stage 1: {STAGE1.name} up to date, skipping rebuild.")
    else:
        print("Stage 0: compiling the selfhost bundle (12 modules) ...")
        bundle = bundle_ast("selfhost", DEFAULT_ORDER)
        if not validate_bootstrap_ast(bundle):
            print("FATAL: bundle violates the bootstrap profile")
            return 1
        stage1_program = Compiler().compile_program(bundle)
        if not verify_program(stage1_program):
            print("FATAL: stage 1 artifact failed verification")
            return 1
        dump(stage1_program, str(STAGE1))
        print(f"  stage1: {STAGE1.stat().st_size} bytes, "
              f"{len(stage1_program.functions)} functions")

    # ---- Stage 2 and Stage 3: the artifacts compile the same sources. ----
    for index, (stage_in, stage_out) in enumerate(((STAGE1, STAGE2), (STAGE2, STAGE3)), start=2):
        if (stage_out.is_file()
                and stage_out.stat().st_mtime > stage_in.stat().st_mtime):
            print(f"{stage_out.name}: up to date, skipping run.")
            continue
        started = time.time()
        print(f"Running {stage_in.name} -> {stage_out.name} "
              "(artifact pipeline on the Python VM, checkpointed per phase) ...",
              flush=True)
        with _Heartbeat(stage_in.name):
            run_stage_phased(stage_in, stage_out, f"s{index}")
        print(f"  {stage_out.name}: {stage_out.stat().st_size} bytes "
              f"in {time.time() - started:.1f}s", flush=True)

    # ---- Fixed point: the formal Gate C criterion. ----
    h2, h3 = sha256_file(STAGE2), sha256_file(STAGE3)
    fixed_point = h2 == h3
    print(f"\n  SHA256(stage2) = {h2}")
    print(f"  SHA256(stage3) = {h3}")
    if not fixed_point:
        print("\nGATE C FAILED: stage2 and stage3 differ.")
        return 1
    print("\nGATE C PASSED: compiler fixed point reached "
          "(SHA256(stage2) == SHA256(stage3)).")

    # ---- Provenance record (docs/SELF_HOSTING.md artifact policy). ----
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, cwd=str(ROOT),
        ).stdout.strip()
    except Exception:
        revision = "unknown"
    PROVENANCE.write_text(
        "# Gate C fixed-point verification record\n\n"
        f"- stage1 sha256: {sha256_file(STAGE1)}\n"
        f"- stage2 sha256: {h2}\n"
        f"- stage3 sha256: {h3}\n"
        f"- fixed point: SHA256(stage2) == SHA256(stage3): verified\n"
        f"- source revision: {revision}\n"
        f"- language version: {LANGUAGE_VERSION}\n"
        f"- bytecode format: SBC v{VERSION}\n"
        "- stage 0 command: python3 run_gate_c.py\n"
        "- pipeline: artifact phases (parser_parse_program, compile_ir,\n"
        "  vf_verify, em_dump) driven and checkpointed by run_gate_c.py;\n"
        "  function calls and inputs are identical to mn_compile_bundle\n"
        f"- bundle modules: {', '.join(DEFAULT_ORDER)}\n",
        encoding="utf-8",
    )
    print(f"Provenance written to {PROVENANCE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())