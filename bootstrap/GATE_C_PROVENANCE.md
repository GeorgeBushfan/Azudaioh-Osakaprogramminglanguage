# Gate C fixed-point verification record

- stage1 sha256: 2a6eded48a10877f6b51c9d19835686dd4181e7f98d9422ce43ed2b6b9b1cc4f
- stage2 sha256: 8fa28eafb21addb14fce625ac239cb0c93af9ff4d0e1200caaa57e438f20428b
- stage3 sha256: 8fa28eafb21addb14fce625ac239cb0c93af9ff4d0e1200caaa57e438f20428b
- fixed point: SHA256(stage2) == SHA256(stage3): verified
- source revision: 1638022cd6811e22c96a497449870fb9fab24bd6
- language version: 1.1
- bytecode format: SBC v1
- stage 0 command: python3 run_gate_c.py
- pipeline: artifact phases (parser_parse_program, compile_ir,
  vf_verify, em_dump) driven and checkpointed by run_gate_c.py;
  function calls and inputs are identical to mn_compile_bundle
- bundle modules: support.saka, diagnostic.saka, token.saka, ast.saka, lexer.saka, parser.saka, lower.saka, bytecode.saka, compiler.saka, verifier.saka, emit_sbc.saka, main.saka, vm.saka
