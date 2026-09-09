# Gate C fixed-point verification record

- stage1 sha256: 11ebce91a888626ec6a22690cabc50e4806b0e12911bc0316cfe17de7f6302da
- stage2 sha256: 87ae5cef82aebecdf469c06d014df9137590854ea1a6270b3559be08febc009b
- stage3 sha256: 87ae5cef82aebecdf469c06d014df9137590854ea1a6270b3559be08febc009b
- fixed point: SHA256(stage2) == SHA256(stage3): verified
- source revision: 920c8d024a89a71e4200b8f5b94860bdac3b85aa
- language version: 1.1
- bytecode format: SBC v1
- stage 0 command: python3 run_gate_c.py
- pipeline: artifact phases (parser_parse_program, compile_ir,
  vf_verify, em_dump) driven and checkpointed by run_gate_c.py;
  function calls and inputs are identical to mn_compile_bundle
- bundle modules: support.saka, diagnostic.saka, token.saka, ast.saka, lexer.saka, parser.saka, lower.saka, bytecode.saka, compiler.saka, verifier.saka, emit_sbc.saka, main.saka, vm.saka
