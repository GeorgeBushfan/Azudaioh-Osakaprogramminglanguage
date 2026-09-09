# bytecode.py
from dataclasses import dataclass, field
from typing import Any, List, Tuple, Optional, Dict, Union

# --- Runtime value in VM ---
@dataclass
class Value:
    data: Any
    kind: str  # "truth" or "grain"

    def __repr__(self) -> str:
        return f"Value({self.data!r}, {self.kind})"

@dataclass   
class FunctionBytecode:
    params: List[str]
    program: "BytecodeProgram"
    locals: List[str] = field(default_factory=list)  # All function-local variable names



# --- Opcodes ---
PUSH_CONST   = "PUSH_CONST"    # operand: const index
LOAD_VAR     = "LOAD_VAR"      # operand: var name
STORE_VAR    = "STORE_VAR"     # operand: var name
DUP          = "DUP"           # operand: None (duplicate top of stack)
POP          = "POP"           # operand: None (drop top of stack)
ADD          = "ADD"           # operand: None
SUB          = "SUB"           # operand: None
MUL          = "MUL"           # operand: None
DIV          = "DIV"           # operand: None
MOD          = "MOD"           # operand: None
MAKE_LIST    = "MAKE_LIST"     # operand: n items
MAKE_MAP     = "MAKE_MAP"      # operand: n pairs (key, value) already pushed
INDEX_GET    = "INDEX_GET"     # operand: None
INDEX_SET    = "INDEX_SET"     # operand: base var name (for policy)
CALL_BUILTIN = "CALL_BUILTIN"  # operand: (name, argc)
HALT         = "HALT"          # operand: None
# Control flow
JMP          = "JMP"           # operand: target ip
JMP_IF_FALSE = "JMP_IF_FALSE"  # operand: target ip (pops condition)

# Comparisons
CMP_EQ       = "CMP_EQ"
CMP_NE       = "CMP_NE"
CMP_LT       = "CMP_LT"
CMP_LE       = "CMP_LE"
CMP_GT       = "CMP_GT"
CMP_GE       = "CMP_GE"

CALL_FUNC = "CALL_FUNC"   # operand: (name, argc)
RET       = "RET"         # operand: None

# Trace point for capturing state
TRACE_POINT = "TRACE_POINT"
TRY_PUSH    = "TRY_PUSH"      # operand: catch target ip
TRY_POP     = "TRY_POP"       # operand: None



Instruction = Tuple[str, Optional[Any], int]  # (opcode, argument, line_number)

@dataclass
class BytecodeProgram:
    consts: List[Value]
    code: List[Instruction]  # List of (opcode, argument, line_number)
    functions: Dict[str, FunctionBytecode] = None
    main: List[Instruction] = field(default_factory=list)
