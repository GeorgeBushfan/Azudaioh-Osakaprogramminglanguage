# compiler.py
from typing import List, Tuple, Any
from bytecode import (
    BytecodeProgram, Value, 
    PUSH_CONST, LOAD_VAR, STORE_VAR, DUP, POP, ADD, SUB, MUL, DIV, MOD,
    MAKE_LIST, MAKE_MAP, INDEX_GET, INDEX_SET,
    CALL_BUILTIN, HALT,
    JMP, JMP_IF_FALSE,
    CMP_EQ, CMP_NE, CMP_LT, CMP_LE, CMP_GT, CMP_GE,
    CALL_FUNC, RET, TRACE_POINT, TRY_PUSH, TRY_POP,
    FunctionBytecode,
)

# Import your AST node classes
from ast_nodes import (
    Number, String, Variable, BinaryOp,
    ListLiteral, MapLiteral, IndexAccess,
    Assign, Call, IndexAssign, CallExpr,
    If, While, Block, Compare, FunctionDef, Return, TryCatch, UnaryOp,
    Break, Continue, Import, Export,
)
from builtin_registry import POLICY_NAME_BUILTINS, is_builtin, validate_builtin_arity

class Compiler:
    def __init__(self, debug=False):
        self.debug = debug
        self.consts: List[Value] = []
        self.code: List[Tuple[str, Any]] = []
        self.functions = {}   # name -> FunctionBytecode
        self.function_table = {}  # Add function table for verification
        self.loop_stack = []
        self.scope_depth = 0



    def add_const(self, data, kind="truth") -> int:
        self.consts.append(Value(data, kind))
        return len(self.consts) - 1

    def emit(self, op, arg=None, line=-1):
        self.code.append((op, arg, line))

    def compile(self, program) -> BytecodeProgram:
        return self.compile_program(program)
        
    def compile_program(self, program) -> BytecodeProgram:
        # First pass: compile all function definitions
        self.consts = []
        self.code = []
        self.functions = {}
        self.function_table = {}
        self.loop_stack = []
        self.scope_depth = 0
        # Register every function signature before compiling any body so that
        # forward calls and mutual recursion are checked with full knowledge
        # of the statically bundled source unit.
        for stmt in program:
            if isinstance(stmt, FunctionDef):
                self.function_table[stmt.name] = {
                    "params": list(stmt.params),
                    "return_type": None,
                }
        for stmt in program:
            if isinstance(stmt, FunctionDef):
                self.compile_function_def(stmt)
                
        # Second pass: compile other statements into one contiguous main stream
        for stmt in program:
            line = getattr(stmt, "line", -1)
            if isinstance(stmt, FunctionDef):
                # The interpreter registers definitions at runtime and emits
                # its normal before/execute/after trace triplet.  Functions
                # are linked statically in SBC, so preserve those observable
                # trace points as no-ops in main.
                if line != -1:
                    self.emit(TRACE_POINT, None, line)
                    self.emit(TRACE_POINT, None, line)
                    self.emit(TRACE_POINT, None, line)
                continue
            if line != -1:
                self.emit(TRACE_POINT, None, line)
            self.compile_stmt(stmt)
            if line != -1:
                self.emit(TRACE_POINT, None, line)
        self.emit(HALT, None, -1)
        main_code = self.code.copy()
                
        # Create BytecodeProgram with main code as executable code
        return BytecodeProgram(
            consts=self.consts,
            code=main_code,
            functions=self.functions,
            main=main_code
        )
        
    def _collect_local_vars(self, node):
        """Collect all variable names declared in a statement tree."""
        locals_set = set()
        
        if isinstance(node, Assign):
            locals_set.add(node.name)
        elif isinstance(node, Block):
            for stmt in node.statements:
                locals_set.update(self._collect_local_vars(stmt))
        elif isinstance(node, If):
            locals_set.update(self._collect_local_vars(node.body))
            if getattr(node, "else_body", None):
                locals_set.update(self._collect_local_vars(node.else_body))
        elif isinstance(node, While):
            locals_set.update(self._collect_local_vars(node.body))
        elif isinstance(node, TryCatch):
            locals_set.update(self._collect_local_vars(node.try_body))
            if node.catch_body:
                locals_set.update(self._collect_local_vars(node.catch_body))
        
        return locals_set
    
    def compile_function_def(self, node):
        if self.debug:
            print(f"Compiling function: {node.name}")
        # Create sub-compiler for function body
        sub_compiler = Compiler(debug=self.debug)
        # Functions in a source unit share one static namespace. Seeding the
        # sub-compiler with every signature avoids treating forward calls as
        # unverifiable while the containing program still owns the bodies.
        sub_compiler.functions = dict(self.functions)
        for function_name, signature in self.function_table.items():
            if function_name not in sub_compiler.functions:
                sub_compiler.functions[function_name] = FunctionBytecode(
                    params=list(signature["params"]),
                    program=BytecodeProgram([], [], {}),
                )
        
        # Collect all local variables from function body (parameters + declarations)
        # All must be pre-seeded in frame.locals to prevent nested function calls
        # from corrupting caller variables through shared scope chains
        all_locals = set(node.params)
        all_locals.update(self._collect_local_vars(node.body))
        all_locals = list(all_locals)
        
        # Declare parameters as local variables
        for param in reversed(node.params):
            sub_compiler.emit(STORE_VAR, param)
            
        # Compile function body
        sub_compiler.compile_stmt(node.body)
        
        # Add return if missing
        if not sub_compiler.code or sub_compiler.code[-1][0] != RET:
            none_index = sub_compiler.add_const(None, "truth")
            sub_compiler.emit(PUSH_CONST, none_index)
            sub_compiler.emit(RET, None)
            
        # Create function bytecode
        func_program = BytecodeProgram(
            sub_compiler.consts, 
            sub_compiler.code, 
            {}
        )
        
        # Add to functions dictionary with parameters list
        # Only parameters are pre-populated in frame.locals to provide isolation
        self.functions[node.name] = FunctionBytecode(
            params=node.params,
            program=func_program,
            locals=all_locals
        )
        
        # Add to function table for verification
        self.function_table[node.name] = {
            "params": node.params,
            "return_type": None  # Placeholder for actual type system
        }
        
        if self.debug:
            print(f"Added function: {node.name} with {len(node.params)} parameters and {len(all_locals)} local vars")
    
    def emit_placeholder(self, op):
            """Emit jump with placeholder operand, return index to patch later."""
            self.code.append((op, None, -1))
            return len(self.code) - 1

    def patch(self, instr_index, target_ip):
            op, _, line = self.code[instr_index]
            self.code[instr_index] = (op, target_ip, line)

    def emit_scope_unwind(self, target_depth):
        """Emit runtime scope exits without changing compile-time nesting."""
        for _ in range(self.scope_depth - target_depth):
            self.emit(CALL_BUILTIN, ("__pop_scope__", 0))
            self.emit(POP, None)


    # ---------- statements ----------
    def compile_stmt(self, node):
        # Trace point for interpreter's execute() pre-node capture
        line = getattr(node, "line", -1)
        if line != -1:
            self.emit(TRACE_POINT, None, line)
        if isinstance(node, Assign):
            # Compile the expression
            self.compile_expr(node.expr)
            # Assignment kind: if decl_type indicates grain/truth, force kind here
            if node.decl_type == "grain":
                # force top-of-stack kind to grain
                self.emit(CALL_BUILTIN, ("__force_kind_grain__", 1))
            elif node.decl_type == "truth":
                self.emit(CALL_BUILTIN, ("__force_kind_truth__", 1))
            self.emit(STORE_VAR, node.name)
            return
        
        if isinstance(node, Assign):
            # Compile function body in its own compiler instance
            sub = Compiler()
            sub.compile_stmt(node.body)
            sub.emit(RET, None)  # implicit ret if missing; can make error later

            fn_prog = BytecodeProgram(sub.consts, sub.code, sub.functions)
            self.functions[node.name] = FunctionBytecode(node.params, fn_prog)
            return
        
        if isinstance(node, Return):
            self.compile_expr(node.expr)
            self.emit_scope_unwind(0)
            self.emit(RET, None)
            return

        if isinstance(node, Import):
            if getattr(node, "is_path", False):
                mod_idx = self.add_const(node.module, "truth")
                alias = node.alias or node.module.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                alias_idx = self.add_const(alias, "truth")
                self.emit(PUSH_CONST, mod_idx, getattr(node, "line", -1))
                self.emit(PUSH_CONST, alias_idx, getattr(node, "line", -1))
                self.emit(CALL_BUILTIN, ("__import_file_module__", 2), getattr(node, "line", -1))
            else:
                const_index = self.add_const(node.module, "truth")
                self.emit(PUSH_CONST, const_index, getattr(node, "line", -1))
                self.emit(CALL_BUILTIN, ("__import_module__", 1), getattr(node, "line", -1))
            self.emit(POP, None, getattr(node, "line", -1))
            return

        if isinstance(node, Export):
            inner = node.node
            if isinstance(inner, Assign):
                self.compile_stmt(inner)
                name_idx = self.add_const(inner.name, "truth")
                self.emit(PUSH_CONST, name_idx, getattr(node, "line", -1))
                self.emit(CALL_BUILTIN, ("__export_symbol__", 1), getattr(node, "line", -1))
                self.emit(POP, None, getattr(node, "line", -1))
                return
            if isinstance(inner, FunctionDef):
                self.compile_function_def(inner)
                name_idx = self.add_const(inner.name, "truth")
                self.emit(PUSH_CONST, name_idx, getattr(node, "line", -1))
                self.emit(CALL_BUILTIN, ("__export_symbol__", 1), getattr(node, "line", -1))
                self.emit(POP, None, getattr(node, "line", -1))
                return
            raise RuntimeError("Compiler: export currently supports only variable assignments/declarations")
            return

        if isinstance(node, Break):
            if not self.loop_stack:
                raise RuntimeError("break used outside loop")
            self.emit_scope_unwind(self.loop_stack[-1]["scope_depth"])
            jmp_ix = self.emit_placeholder(JMP)
            self.loop_stack[-1]["break_sites"].append(jmp_ix)
            return

        if isinstance(node, Continue):
            if not self.loop_stack:
                raise RuntimeError("continue used outside loop")
            self.emit_scope_unwind(self.loop_stack[-1]["scope_depth"])
            self.emit(JMP, self.loop_stack[-1]["continue_target"])
            return


        if isinstance(node, IndexAssign):
            # container must be Variable by your parser rule
            base = node.container.name
            self.emit(LOAD_VAR, base)
            self.compile_expr(node.index)
            self.compile_expr(node.value)
            self.emit(INDEX_SET, base)
            return
        
        if isinstance(node, Block):
            self.emit(CALL_BUILTIN, ("__push_scope__", 0), getattr(node, "line", -1))
            self.emit(POP, None, getattr(node, "line", -1))
            self.scope_depth += 1
            for s in node.statements:
                self.compile_stmt(s)
            self.scope_depth -= 1
            self.emit(CALL_BUILTIN, ("__pop_scope__", 0), getattr(node, "line", -1))
            self.emit(POP, None, getattr(node, "line", -1))
            return
        
        if isinstance(node, If):
            # condition -> stack
            self.compile_expr(node.condition)

            # if false, jump to end of body
            jmp_false_ix = self.emit_placeholder(JMP_IF_FALSE)

            # body
            self.compile_stmt(node.body)

            # Optional else body
            if getattr(node, "else_body", None) is not None:
                jmp_end_ix = self.emit_placeholder(JMP)
                self.patch(jmp_false_ix, len(self.code))
                self.compile_stmt(node.else_body)
                self.patch(jmp_end_ix, len(self.code))
            else:
                # patch jump target to here
                self.patch(jmp_false_ix, len(self.code))
            return
        
        if isinstance(node, While):
            loop_start = len(self.code)

            loop_ctx = {
                "break_sites": [],
                "continue_target": loop_start,
                "scope_depth": self.scope_depth,
            }
            self.loop_stack.append(loop_ctx)

            self.compile_expr(node.condition)
            jmp_false_ix = self.emit_placeholder(JMP_IF_FALSE)

            self.compile_stmt(node.body)

            # jump back to start
            self.emit(JMP, loop_start)

            # patch exit
            loop_exit = len(self.code)
            self.patch(jmp_false_ix, loop_exit)
            for br_ix in loop_ctx["break_sites"]:
                self.patch(br_ix, loop_exit)
            self.loop_stack.pop()
            return

        if isinstance(node, TryCatch):
            # TRY_PUSH points to catch block start
            try_push_ix = self.emit_placeholder(TRY_PUSH)
            self.compile_stmt(node.try_body)
            self.emit(TRY_POP, None)
            jmp_end_ix = self.emit_placeholder(JMP)

            catch_start = len(self.code)
            self.patch(try_push_ix, catch_start)
            self.compile_stmt(node.catch_body)
            self.patch(jmp_end_ix, len(self.code))
            return

        # Handle expression statements
        try:
            self.compile_expr(node)
            return
        except RuntimeError:
            pass  # Not an expression, continue to error below



        if isinstance(node, FunctionDef):
            # Compile function body
            saved_code = self.code
            self.code = []
            
            # Declare parameters as local variables
            for param in node.params:
                self.emit(STORE_VAR, param)
                
            # Compile function body
            self.compile_stmt(node.body)
            
            # Create function bytecode
            func_code = self.code.copy()
            self.code = saved_code  # Restore original code
            
            # Add to functions dictionary
            self.functions[node.name] = FunctionBytecode(
                params=node.params,
                program=BytecodeProgram(consts=self.consts.copy(), code=func_code)
            )
            return

        if isinstance(node, Call):
            if not is_builtin(node.name):
                # Allow user-defined function calls in statement position.
                # Return value (if any) is ignored by the source program.
                for a in node.args:
                    self.compile_expr(a)
                self.emit(CALL_FUNC, (node.name, len(node.args)), node.line)
                self.emit(POP, None, node.line)
                return

            validate_builtin_arity(node.name, len(node.args))
            
            # Compile arguments
            for arg in node.args:
                if node.name in POLICY_NAME_BUILTINS and isinstance(arg, Variable):
                    const_index = self.add_const(arg.name, "truth")
                    self.emit(PUSH_CONST, const_index, node.line)
                else:
                    self.compile_expr(arg)
            
            # Emit CALL_BUILTIN for built-in statement functions
            self.emit(CALL_BUILTIN, (node.name, len(node.args)), node.line)
            self.emit(POP, None, node.line)
            return

        raise RuntimeError(f"Compiler: unsupported stmt {type(node)}")

    # ---------- expressions ----------
    def compile_expr(self, node):
        if isinstance(node, CallExpr):
            if len(node.args) == 0 and "." in node.name and not node.name.startswith("Math.") and not node.name.startswith("std."):
                # Namespaced module value read: alias.symbol
                self.emit(LOAD_VAR, node.name, node.line)
                return

            # Validate argument count for user-defined functions
            if not is_builtin(node.name) and node.name in self.functions:
                expected_args = len(self.functions[node.name].params)
                if len(node.args) != expected_args:
                    raise RuntimeError(f"Function '{node.name}' expects {expected_args} arguments, got {len(node.args)}")

            # Evaluate and push arguments left-to-right in source order.
            for a in node.args:
                self.compile_expr(a)

            if self.debug:
                print(f"Emitting call to {node.name} with {len(node.args)} arguments")
            
            if is_builtin(node.name):
                validate_builtin_arity(node.name, len(node.args))
                self.emit(CALL_BUILTIN, (node.name, len(node.args)), node.line)
            else:
                self.emit(CALL_FUNC, (node.name, len(node.args)), node.line)
            return


        if isinstance(node, Number):
            kind = "grain" if isinstance(node.value, float) else "truth"
            ci = self.add_const(node.value, kind)
            self.emit(PUSH_CONST, ci, node.line)
            return

        if isinstance(node, String):
            ci = self.add_const(node.value, "truth")
            self.emit(PUSH_CONST, ci, node.line)
            return

        if isinstance(node, Variable):
            self.emit(LOAD_VAR, node.name, node.line)
            return

        if isinstance(node, BinaryOp):
            if node.op == "and":
                # Short-circuit AND
                self.compile_expr(node.left)
                self.emit(CALL_BUILTIN, ("__to_bool_preserve_kind__", 1), node.line)
                self.emit(DUP, None, node.line)
                self.emit(CALL_BUILTIN, ("__to_truthaboutgrain__", 1), node.line)
                jmp_false_ix = self.emit_placeholder(JMP_IF_FALSE)

                # Left is truthy path: evaluate RHS and combine
                self.compile_expr(node.right)
                self.emit(CALL_BUILTIN, ("__to_bool_preserve_kind__", 1), node.line)
                self.emit(CALL_BUILTIN, ("__bool_and__", 2), node.line)
                jmp_end_ix = self.emit_placeholder(JMP)

                # Left is falsy path: keep left bool/kind as result
                self.patch(jmp_false_ix, len(self.code))
                self.patch(jmp_end_ix, len(self.code))
                return

            if node.op == "or":
                # Short-circuit OR
                self.compile_expr(node.left)
                self.emit(CALL_BUILTIN, ("__to_bool_preserve_kind__", 1), node.line)
                self.emit(DUP, None, node.line)
                self.emit(CALL_BUILTIN, ("__to_truthaboutgrain__", 1), node.line)
                jmp_false_ix = self.emit_placeholder(JMP_IF_FALSE)

                # Left is truthy path: keep left bool/kind as result
                jmp_end_ix = self.emit_placeholder(JMP)

                # Left is falsy path: evaluate RHS and combine
                self.patch(jmp_false_ix, len(self.code))
                self.compile_expr(node.right)
                self.emit(CALL_BUILTIN, ("__to_bool_preserve_kind__", 1), node.line)
                self.emit(CALL_BUILTIN, ("__bool_or__", 2), node.line)

                self.patch(jmp_end_ix, len(self.code))
                return

            self.compile_expr(node.left)
            self.compile_expr(node.right)
            if node.op == "+":
                self.emit(ADD, None, node.line)
                return
            if node.op == "-":
                self.emit(SUB, None, node.line)
                return
            if node.op == "*":
                self.emit(MUL, None, node.line)
                return
            if node.op == "/":
                self.emit(DIV, None, node.line)
                return
            if node.op == "%":
                self.emit(MOD, None, node.line)
                return
            raise RuntimeError(f"Compiler: unsupported op {node.op}")

        if isinstance(node, UnaryOp):
            if node.op == "not":
                self.compile_expr(node.operand)
                self.emit(CALL_BUILTIN, ("__to_bool_preserve_kind__", 1), node.line)
                self.emit(CALL_BUILTIN, ("__bool_not__", 1), node.line)
                return
            raise RuntimeError(f"Compiler: unsupported unary op {node.op}")

        if isinstance(node, Compare):
            self.compile_expr(node.left)
            self.compile_expr(node.right)
            if node.op == "==":
                self.emit(CMP_EQ, None, node.line)
            elif node.op == "!=":
                self.emit(CMP_NE, None, node.line)
            elif node.op == "<":
                self.emit(CMP_LT, None, node.line)
            elif node.op == "<=":
                self.emit(CMP_LE, None, node.line)
            elif node.op == ">":
                self.emit(CMP_GT, None, node.line)
            elif node.op == ">=":
                self.emit(CMP_GE, None, node.line)
            else:
                raise RuntimeError(f"Compiler: unsupported compare {node.op}")
            return

        if isinstance(node, ListLiteral):
            for e in node.elements:
                self.compile_expr(e)
            self.emit(MAKE_LIST, len(node.elements), node.line)
            return

        if isinstance(node, MapLiteral):
            # Push key then value for each pair
            for key_node, val_node in node.pairs:
                # keys are String nodes already in your parser
                self.compile_expr(key_node)
                self.compile_expr(val_node)
            self.emit(MAKE_MAP, len(node.pairs), node.line)
            return

        if isinstance(node, IndexAccess):
            self.compile_expr(node.container)
            self.compile_expr(node.index)
            self.emit(INDEX_GET, None, node.line)
            return

        raise RuntimeError(f"Compiler: unsupported expr {type(node)}")
