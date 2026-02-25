import unittest
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from verifier import verify, VerificationError
from bytecode import BytecodeProgram, Value

class Instruction:
    """Helper class to create instruction objects"""
    def __init__(self, op, arg=None):
        self.op = op
        self.arg = arg
        
    def __repr__(self):
        return f"Instr({self.op}, {self.arg})"

# Helper function to create instruction lists
def make_instrs(*instructions):
    return [Instruction(op, arg) for op, arg in instructions]

class TestVerifier(unittest.TestCase):
    def test_valid_main_block(self):
        """Test valid main block with HALT at end"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(
                ("PUSH_CONST", 0),
                ("HALT", None)
            ),
            functions={}
        )
        bc.consts = [Value(5, "grain")]
        verify(bc, {})  # Should not raise

    def test_stack_underflow(self):
        """Test stack underflow detection"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(
                ("ADD", None),
                ("HALT", None)
            ),
            functions={}
        )
        with self.assertRaises(VerificationError):
            verify(bc, {})

    def test_missing_halt_in_main(self):
        """Test missing HALT at end of main block"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(("PUSH_CONST", 0)),
            functions={}
        )
        bc.consts = [Value(5, "grain")]
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {})
        self.assertIn("must end with HALT", str(ctx.exception))

    def test_ret_in_main_block(self):
        """Test RET not allowed in main block"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(
                ("PUSH_CONST", 0),
                ("RET", None)
            ),
            functions={}
        )
        bc.consts = [Value(5, "grain")]
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {})
        self.assertIn("RET not allowed in main block", str(ctx.exception))

    def test_valid_function_block(self):
        """Test valid function with RET at end"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(("HALT", None)),
            functions={
                "add": BytecodeProgram(
                    consts=[],
                    code=make_instrs(
                        ("LOAD_VAR", "a"),
                        ("LOAD_VAR", "b"), 
                        ("ADD", None),
                        ("RET", None)
                    ),
                    functions={}
                )
            }
        )
        verify(bc, {"add": 2})  # Should not raise

    def test_missing_ret_in_function(self):
        """Test function missing RET at end"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(("HALT", None)),
            functions={
                "add": BytecodeProgram(
                    consts=[],
                    code=make_instrs(
                        ("LOAD_VAR", "a"),
                        ("LOAD_VAR", "b"), 
                        ("ADD", None)
                    ),
                    functions={}
                )
            }
        )
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {"add": 2})
        self.assertIn("Function must end with RET", str(ctx.exception))

    def test_function_arity_mismatch(self):
        """Test function call with wrong number of arguments"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(
                ("CALL_FUNC", ("add", 1)),
                ("HALT", None)
            ),
            functions={
                "add": BytecodeProgram(
                    consts=[],
                    code=make_instrs(("RET", None)),
                    functions={}
                )
            }
        )
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {"add": 2})
        self.assertIn("expects 2 args, got 1", str(ctx.exception))

    def test_unknown_function_call(self):
        """Test call to undefined function"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(
                ("CALL_FUNC", ("unknown", 0)),
                ("HALT", None)
            ),
            functions={}
        )
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {})
        self.assertIn("Unknown function unknown", str(ctx.exception))

    def test_unknown_opcode(self):
        """Test invalid opcode detection"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(
                ("INVALID_OP", None),
                ("HALT", None)
            ),
            functions={}
        )
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {})
        self.assertIn("Unknown opcode INVALID_OP", str(ctx.exception))

    def test_complex_stack_balance(self):
        """Test complex stack balance scenario"""
        bc = BytecodeProgram(
            consts=[Value(5, "grain"), Value(10, "grain")],
            code=[],  # Added empty code list
            main=make_instrs(
                ("PUSH_CONST", 0),
                ("PUSH_CONST", 1),
                ("ADD", None),
                ("HALT", None)
            ),
            functions={}
        )
        verify(bc, {})  # Should not raise

    def test_stack_underflow_in_function(self):
        """Test stack underflow in function"""
        bc = BytecodeProgram(
            consts=[],
            code=[],  # Added empty code list
            main=make_instrs(("HALT", None)),
            functions={
                "underflow": BytecodeProgram(
                    consts=[],
                    code=make_instrs(
                        ("ADD", None),
                        ("RET", None)
                    ),
                    functions={}
                )
            }
        )
        with self.assertRaises(VerificationError) as ctx:
            verify(bc, {"underflow": 0})
        self.assertIn("Stack underflow", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()
