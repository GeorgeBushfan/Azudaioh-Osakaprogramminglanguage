# Gate C fixed-point verification record

- stage1 sha256: 461d635f98f5afdab6f841bc8627dc8eafaa9f84ddb5d1c389d2fc2df550ec3c
- stage2 sha256: a6f26fd48cd628e97a1b52c58e8a87534161af0fac9d3bd7aaf4ac39904ffc4b
- stage3 sha256: a6f26fd48cd628e97a1b52c58e8a87534161af0fac9d3bd7aaf4ac39904ffc4b
- fixed point: SHA256(stage2) == SHA256(stage3): verified
- source revision: 788d529bbba325fcfc8223d4c984bce8679f6fe8
- language version: 1.1
- bytecode format: SBC v1
- stage 0 command: python3 run_gate_c.py
- pipeline: artifact phases (parser_parse_program, compile_ir,
  vf_verify, em_dump) driven and checkpointed by run_gate_c.py;
  function calls and inputs are identical to mn_compile_bundle
- bundle modules: support.saka, diagnostic.saka, token.saka, ast.saka, lexer.saka, parser.saka, lower.saka, bytecode.saka, compiler.saka, verifier.saka, emit_sbc.saka, main.saka
