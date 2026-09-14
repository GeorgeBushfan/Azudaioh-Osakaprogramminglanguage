# Gate C fixed-point verification record

- stage1 sha256: 03d41e74f3392f781d1b2ec733eda36093de8a8b78979f2c24daa32803885718
- stage2 sha256: 07c2b0f18db27e7faac2b7fd82e8b131434ee9193625d5fc481897f07dc2d690
- stage3 sha256: 07c2b0f18db27e7faac2b7fd82e8b131434ee9193625d5fc481897f07dc2d690
- fixed point: SHA256(stage2) == SHA256(stage3): verified
- source revision: 47b3a5e8056cfa912e013d4c0f100c5204ec237d
- language version: 1.1
- bytecode format: SBC v1
- stage 0 command: python3 run_gate_c.py
- pipeline: artifact phases (parser_parse_program, compile_ir,
  vf_verify, em_dump) driven and checkpointed by run_gate_c.py;
  function calls and inputs are identical to mn_compile_bundle
- bundle modules: support.saka, diagnostic.saka, token.saka, ast.saka, lexer.saka, parser.saka, lower.saka, bytecode.saka, compiler.saka, verifier.saka, emit_sbc.saka, main.saka, vm.saka
