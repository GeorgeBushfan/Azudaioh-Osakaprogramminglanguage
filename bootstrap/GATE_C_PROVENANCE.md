# Gate C fixed-point verification record

- stage1 sha256: 75ff32fd4243d553d0020dda884b9a48ec8469c3fd18598bf09c99072cffe1bd
- stage2 sha256: c1b04df02fe0fe91130ce60602d0ffca485ee856f2d528e6cf3b16658da45583
- stage3 sha256: c1b04df02fe0fe91130ce60602d0ffca485ee856f2d528e6cf3b16658da45583
- fixed point: SHA256(stage2) == SHA256(stage3): verified
- source revision: 88ddaadfcf0fed625a9b26beaed5a5c8d90451ee
- language version: 1.1
- bytecode format: SBC v1
- stage 0 command: python3 run_gate_c.py
- pipeline: artifact phases (parser_parse_program, compile_ir,
  vf_verify, em_dump) driven and checkpointed by run_gate_c.py;
  function calls and inputs are identical to mn_compile_bundle
- bundle modules: support.saka, diagnostic.saka, token.saka, ast.saka, lexer.saka, parser.saka, lower.saka, bytecode.saka, compiler.saka, verifier.saka, emit_sbc.saka, main.saka, vm.saka
