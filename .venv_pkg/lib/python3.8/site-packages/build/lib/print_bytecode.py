from compiler import Compiler
from parser import Parser
from lexer import lex
import sys

# Read and parse the test file
with open('tests/test_fn.saka') as f:
    source = f.read()
tokens = lex(source)
parser = Parser(tokens)
program = parser.parse()

# Compile the program
compiler = Compiler()
bc = compiler.compile_program(program)

# Print the bytecode
print("Bytecode Instructions:")
for i, (op, arg) in enumerate(bc.code):
    print(f"{i}: {op} {arg}")

print("\nFunction Table:")
for name, fn in bc.functions.items():
    print(f"{name}: {len(fn.params)} parameters")
