"""Deterministic Osaka bytecode disassembler."""


def _print_code(code, indent):
    for index, (op, arg, line) in enumerate(code):
        suffix = f" ; line {line}" if line != -1 else ""
        operand = "" if arg is None else str(arg)
        print(f"{indent}{index:04d}  {op:<14} {operand}{suffix}")


def print_bytecode(program):
    print("CONSTANTS:")
    for index, value in enumerate(program.consts):
        print(f"  {index:04d}  {value.kind:<5}  {value.data!r}")
    print("\nMAIN:")
    _print_code(program.code, "  ")
    for name in sorted((program.functions or {}).keys()):
        function = program.functions[name]
        print(f"\nFUNCTION {name}({', '.join(function.params)}):")
        print("  CONSTANTS:")
        for index, value in enumerate(function.program.consts):
            print(f"    {index:04d}  {value.kind:<5}  {value.data!r}")
        print("  CODE:")
        _print_code(function.program.code, "    ")


if __name__ == "__main__":
    import argparse
    from sbc import load
    parser = argparse.ArgumentParser(description="Disassemble an Osaka SBC1 file")
    parser.add_argument("file")
    print_bytecode(load(parser.parse_args().file))
