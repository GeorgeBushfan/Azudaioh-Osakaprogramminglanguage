# interpreter.py

from ast_nodes import *
import io
import sys
import math
import os

from lexer import lex
from parser import Parser
from runtime import Runtime as CoreRuntime


class ReturnSignal(Exception):
    def __init__(self, value, kind):
        self.value = value
        self.kind = kind


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


class Runtime:
    def __init__(self):
        self.vars = {}
        self.var_kinds = {}
        self.functions = {}
        self.acknowledged = set()
        self.completed = set()
        self.assumed = set()
        self.legacy = set()
        self.warnings = []  # list of warning messages
        self.errors = []    # list of error messages
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.has_unresolved_grain = False
        self.context_level = 0
        self.execution_traces = []  # store execution traces
        self.initialised = set()    # track initialized variables

    def push_scope(self):
        # Create a new scope by saving current state
        self.vars = self.vars.copy()
        self.var_kinds = self.var_kinds.copy()

    def pop_scope(self):
        # Revert to previous scope (simplified for example)
        pass

    def set_var(self, name, value):
        self.vars[name] = value

    def get_var(self, name):
        return self.vars.get(name, 0)  # default to 0

    def warn(self, message):
        self.warnings.append(message)
        print(f"Warning: {message}", file=sys.stderr)

    def info(self, message):
        print(f"Info: {message}", file=sys.stdout)

    def capture_trace(self, line):
        """Capture current execution state as a trace"""
        trace = {
            "line": line,
            "variables": {k: (v, self.var_kinds.get(k, "grain")) 
                         for k, v in self.vars.items()},
            "warnings": self.warnings.copy(),
            "errors": self.errors.copy(),
            "stdout": self.stdout.getvalue(),
            "stderr": self.stderr.getvalue()
        }
        self.execution_traces.append(trace)


class Interpreter:
    def __init__(self, runtime):
        self.rt = runtime
        if not hasattr(self.rt, "imports"):
            self.rt.imports = set()
        if not hasattr(self.rt, "module_cache"):
            self.rt.module_cache = {}
        if not hasattr(self.rt, "module_loading"):
            self.rt.module_loading = set()
        if not hasattr(self.rt, "current_file"):
            self.rt.current_file = None
        if not hasattr(self.rt, "collecting_exports"):
            self.rt.collecting_exports = False
        if not hasattr(self.rt, "current_module_exports"):
            self.rt.current_module_exports = None
        if not hasattr(self.rt, "imported_module_functions"):
            self.rt.imported_module_functions = {}

    def _resolve_file_path(self, path_value: str) -> str:
        if not isinstance(path_value, str):
            path_value = str(path_value)
        if hasattr(self.rt, "resolve_module_path"):
            return self.rt.resolve_module_path(path_value)
        if os.path.isabs(path_value):
            return os.path.normpath(path_value)
        base = os.path.dirname(self.rt.current_file) if getattr(self.rt, "current_file", None) else os.getcwd()
        return os.path.normpath(os.path.join(base, path_value))

    def _load_file_module(self, module_path: str):
        resolved = self.rt.resolve_module_path(module_path)

        if resolved in self.rt.module_cache:
            return self.rt.module_cache[resolved]
        if resolved in self.rt.module_loading:
            raise RuntimeError(f"Circular module import detected: {resolved}")
        if not os.path.exists(resolved):
            raise RuntimeError(f"Module file not found: {resolved}")

        self.rt.module_loading.add(resolved)
        try:
            with open(resolved, "r", encoding="utf-8") as f:
                source = f.read()

            tokens = lex(source)
            ast = Parser(tokens).parse()

            module_rt = CoreRuntime()
            module_rt.module_cache = self.rt.module_cache
            module_rt.module_loading = self.rt.module_loading
            module_rt.current_file = resolved
            module_rt.collecting_exports = True
            module_rt.current_module_exports = {}
            module_rt.suppress_diagnostics = True
            module_rt.debug = getattr(self.rt, "debug", False)
            module_rt._math_seed = getattr(self.rt, "_math_seed", 123456789)

            # Share outputs/diagnostics channels with importer run.
            module_rt.stdout = self.rt.stdout
            module_rt.stderr = self.rt.stderr
            module_rt.warnings = self.rt.warnings
            module_rt.errors = self.rt.errors

            Interpreter(module_rt).run(ast)

            self.rt._math_seed = getattr(module_rt, "_math_seed", self.rt._math_seed)

            module_obj = {
                "path": resolved,
                "exports": module_rt.current_module_exports or {},
                "functions": dict(module_rt.functions),
            }
            self.rt.module_cache[resolved] = module_obj
            return module_obj
        finally:
            self.rt.module_loading.discard(resolved)

    def _resolve_std_name(self, name: str) -> str:
        std_aliases = {
            "std.len": "len",
            "std.keys": "keys",
            "std.values": "values",
            "std.contains": "contains",
            "std.slice": "slice",
            "std.push": "push",
            "std.pop": "pop",
        }
        if name.startswith("std."):
            if "std" not in self.rt.imports:
                raise RuntimeError("std module not imported (use 'import std;')")
            if name not in std_aliases:
                raise RuntimeError(f"Unknown std member {name}")
            return std_aliases[name]
        return name

    def _next_random(self):
        # Deterministic RNG for interpreter/VM equivalence.
        seed = getattr(self.rt, "_math_seed", 123456789)
        seed = (1103515245 * seed + 12345) % (2 ** 31)
        self.rt._math_seed = seed
        return seed / float(2 ** 31)

    def run(self, program):
        # Clear any previous traces
        self.rt.execution_traces = []
        
        for stmt in program:
            # Capture trace before executing statement
            if hasattr(stmt, 'line'):
                current_function = None
                for scope in reversed(self.rt.scopes):
                    if 'function_name' in scope:
                        current_function = scope['function_name']
                        break
                    
                self.rt.capture_trace(
                    line_num=stmt.line,
                    function_name=current_function,
                    expression=stmt.__class__.__name__
                )
            
            try:
                self.execute(stmt)
            except BreakSignal:
                raise RuntimeError("break used outside loop")
            except ContinueSignal:
                raise RuntimeError("continue used outside loop")
            
            # Capture trace after executing statement
            if hasattr(stmt, 'line'):
                current_function = None
                for scope in reversed(self.rt.scopes):
                    if 'function_name' in scope:
                        current_function = scope['function_name']
                        break
                    
                self.rt.capture_trace(
                    line_num=stmt.line,
                    function_name=current_function,
                    expression=stmt.__class__.__name__
                )

        if not getattr(self.rt, "suppress_diagnostics", False):
            print(f"\nDiagnostics: {len(self.rt.warnings)} warning(s)")

    # ---------- Top-level dispatcher ----------

    def execute(self, node):
        # Capture trace before executing node
        if hasattr(node, 'line'):
            self.rt.capture_trace(node.line)
        
        # Function definition: just register it
        if isinstance(node, FunctionDef):
            self.rt.functions[node.name] = node
            return

        # Return inside a function
        if isinstance(node, Return):
            value, kind = self.eval(node.expr)
            raise ReturnSignal(value, kind)

        if isinstance(node, Break):
            raise BreakSignal()

        if isinstance(node, Continue):
            raise ContinueSignal()

        if isinstance(node, Import):
            if getattr(node, "is_path", False):
                module_obj = self._load_file_module(node.module)
                alias = node.alias
                if not alias:
                    stem = node.module.rsplit("/", 1)[-1]
                    alias = stem.rsplit(".", 1)[0]

                for export_name, export_data in module_obj["exports"].items():
                    if export_data.get("type") == "value":
                        fq_name = f"{alias}.{export_name}"
                        self.rt.set_var(fq_name, export_data["value"])
                        self.rt.var_kinds[fq_name] = export_data.get("kind", "grain")
                        self.rt.initialised.add(fq_name)
                    elif export_data.get("type") == "function":
                        fq_name = f"{alias}.{export_name}"
                        fn_name = export_data.get("name", export_name)
                        fn = module_obj.get("functions", {}).get(fn_name)
                        if fn is None:
                            raise RuntimeError(f"Exported function not found in module: {export_name}")
                        self.rt.functions[fq_name] = fn
                        self.rt.imported_module_functions[fq_name] = {
                            "function": fn,
                            "module_functions": module_obj.get("functions", {}),
                        }
                    else:
                        raise RuntimeError(f"Unknown export type for {export_name}")
                return

            if node.module != "std":
                raise RuntimeError(f"Unknown module {node.module}")
            self.rt.imports.add("std")
            return

        if isinstance(node, Export):
            if not self.rt.collecting_exports or self.rt.current_module_exports is None:
                raise RuntimeError("export can only be used inside module files")

            inner = node.node
            if isinstance(inner, FunctionDef):
                self.execute(inner)
                self.rt.current_module_exports[inner.name] = {
                    "type": "function",
                    "name": inner.name,
                }
                return

            if isinstance(inner, Assign):
                self.execute(inner)
                value = self.rt.get_var(inner.name)
                kind = self.rt.var_kinds.get(inner.name, "grain")
                self.rt.current_module_exports[inner.name] = {
                    "type": "value",
                    "value": value,
                    "kind": kind,
                }
                return

            raise RuntimeError("export currently supports variable declarations/assignments and functions")

        if isinstance(node, Block):
            self.exec_block(node)
        elif isinstance(node, If):
            self.execute_if(node)
        elif isinstance(node, While):
            self.execute_while(node)
        elif isinstance(node, TryCatch):
            self.execute_try_catch(node)
        elif isinstance(node, Assign):
            self.exec_assign(node)
        elif isinstance(node, Call):
            self.call(node)
        elif isinstance(node, Declaration):
            self.exec_declaration(node)
        elif isinstance(node, IndexAssign):
            self.exec_index_assign(node)
        else:
            raise RuntimeError(f"Unknown node type: {type(node)}")

    # ---------- Declarations for Escalator/Elevator (yours) ----------

    def exec_declaration(self, node):
        # Handle Escalator/Elevator declarations
        if node.decl_type == "Escalator":
            self.rt.context_level += 1
            self.rt.info(
                f"Escalator declaration: {node.name} (level {self.rt.context_level})"
            )
        elif node.decl_type == "Elevator":
            self.rt.context_level = max(self.rt.context_level, 5)
            self.rt.info(
                f"Elevator declaration: {node.name} (level {self.rt.context_level})"
            )

    # ---------- Assignment & blocks ----------

    def exec_assign(self, node: Assign):
        name = node.name

        # Legacy protection
        if name in self.rt.legacy:
            self.rt.warn(f"{name} is Ivebeengot (legacy) and cannot be reassigned")
            return

        # Hecho freeze: once completed, cannot change
        if name in self.rt.completed:
            self.rt.warn(f"{name} is Hecho and cannot be reassigned")
            return

        # Ah() is now just a soft requirement
        is_first_assignment = name not in self.rt.initialised

        if not is_first_assignment:
            if name not in self.rt.acknowledged and name not in self.rt.assumed:
                warned = getattr(self.rt, "mutation_warned", set())
                if name not in warned:
                    self.rt.warn(f"{name} mutated without Ah()")
                    warned.add(name)
                    self.rt.mutation_warned = warned

        value, expr_kind = self.eval(node.expr)

        # Handle declaration kind / truth model
        if node.decl_type == "grain":
            kind = "grain"
            self.rt.has_unresolved_grain = True
        elif node.decl_type == "truth":
            kind = "truth"
            self.rt.acknowledged.add(name)  # Auto-acknowledge truth declarations
        else:
            # Plain assignment: keep existing kind if present, else from expression
            existing_kind = self.rt.var_kinds.get(name)
            kind = existing_kind if existing_kind is not None else expr_kind

        self.rt.var_kinds[name] = kind
        self.rt.set_var(name, value)
        self.rt.initialised.add(name)

    def exec_block(self, block: Block):
        self.rt.push_scope()
        try:
            for stmt in block.statements:
                self.execute(stmt)
        finally:
            self.rt.pop_scope()

    # ---------- Control flow: if / while ----------

    def execute_if(self, node: If):
        value, kind = self.eval(node.condition)
        if kind != "truth":
            raise RuntimeError("if-condition must be truthaboutgrain")
        if value:
            self.exec_block(node.body)
        elif getattr(node, "else_body", None) is not None:
            if isinstance(node.else_body, If):
                # Support parser sugar: else if (...) { ... }
                self.execute_if(node.else_body)
            else:
                self.exec_block(node.else_body)

    def execute_while(self, node: While):
        while True:
            value, kind = self.eval(node.condition)
            if kind != "truth":
                raise RuntimeError("while-condition must be truthaboutgrain")
            if not value:
                break
            try:
                self.exec_block(node.body)
            except ContinueSignal:
                continue
            except BreakSignal:
                break

    def execute_try_catch(self, node: TryCatch):
        try:
            self.exec_block(node.try_body)
        except Exception:
            self.exec_block(node.catch_body)

    def exec_index_assign(self, node: IndexAssign):
        # Evaluate container
        container_node = node.container

        if not isinstance(container_node, Variable):
            raise RuntimeError("Can only assign into named collections")

        name = container_node.name

        # Hecho protection
        if name in self.rt.completed:
            self.rt.warn(f"{name} is Hecho and cannot be modified")
            return

        # Ah warning
        if name in self.rt.initialised:
            if name not in self.rt.acknowledged and name not in self.rt.assumed:
                warned = getattr(self.rt, "mutation_warned", set())
                if name not in warned:
                    self.rt.warn(f"{name} mutated without Ah()")
                    warned.add(name)
                    self.rt.mutation_warned = warned

        container_val, container_kind = self.eval(container_node)
        index_val, _ = self.eval(node.index)
        value_val, value_kind = self.eval(node.value)

        try:
            container_val[index_val] = value_val
        except Exception:
            raise RuntimeError("Invalid index assignment")

        # Grain propagation
        if value_kind == "grain":
            self.rt.var_kinds[name] = "grain"

    # ---------- Evaluation (always returns (value, kind)) ----------
    def eval_compare(self, node: Compare):
        left_val, left_kind = self.eval(node.left)
        right_val, right_kind = self.eval(node.right)

        result = {
            "==": left_val == right_val,
            "!=": left_val != right_val,
            "<": left_val < right_val,
            "<=": left_val <= right_val,
            ">": left_val > right_val,
            ">=": left_val >= right_val,
        }[node.op]

        # truth only if both sides are truth
        kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
        return result, kind

    def eval(self, node):
        if isinstance(node, CallExpr):
            return self.call_function(node)

        if isinstance(node, UnaryOp):
            if node.op == "not":
                val, kind = self.eval(node.operand)
                return (not bool(val)), kind
            raise RuntimeError(f"Unsupported unary operator {node.op}")
        
        if isinstance(node, ListLiteral):
            vals = []
            kinds = []

            for elem in node.elements:
                v, k = self.eval(elem)
                vals.append(v)
                kinds.append(k)

            # A list is truth only if ALL elements are truth  
            kind = "truth" if all(k == "truth" for k in kinds) else "grain"
            return vals, kind
        
        if isinstance(node, MapLiteral):
            result = {}
            kinds = []

            for key_node, val_node in node.pairs:
                key, _ = self.eval(key_node)
                val, kind = self.eval(val_node)
                result[key] = val
                kinds.append(kind)

            map_kind = "truth" if all(k == "truth" for k in kinds) else "grain"
            return result, map_kind
        
        if isinstance(node, IndexAccess):
            container_val, container_kind = self.eval(node.container)
            index_val, _ = self.eval(node.index)

            try:
                result = container_val[index_val]
            except Exception:
                raise RuntimeError("Invalid map/list access")

            # element inherits container truth
            return result, container_kind

        if isinstance(node, String):
            return node.value, "truth"

        if isinstance(node, Number):
            # literals are authoritative by default
            if isinstance(node.value, float):
                return node.value, "grain"
            return node.value, "truth"

        if isinstance(node, Variable):
            try:
                value = self.rt.get_var(node.name)
            except RuntimeError:
                self.rt.warn(
                    f"{node.name} used before assignment (defaulting to 0)"
                )
                value = 0
            kind = self.rt.var_kinds.get(node.name, "grain")
            return value, kind

        if isinstance(node, Compare):
            return self.eval_compare(node)

        if isinstance(node, BinaryOp):
            if node.op == "and":
                left_val, left_kind = self.eval(node.left)
                left_truthy = bool(left_val)
                if not left_truthy:
                    # Short-circuit: skip RHS
                    return False, left_kind
                right_val, right_kind = self.eval(node.right)
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return bool(right_val), kind

            if node.op == "or":
                left_val, left_kind = self.eval(node.left)
                left_truthy = bool(left_val)
                if left_truthy:
                    # Short-circuit: skip RHS
                    return True, left_kind
                right_val, right_kind = self.eval(node.right)
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return bool(right_val), kind

            left_val, left_kind = self.eval(node.left)
            right_val, right_kind = self.eval(node.right)
            if node.op == "+":
                if isinstance(left_val, str) or isinstance(right_val, str):
                    result = str(left_val) + str(right_val)
                    # Truth propagation: concatenation is truth only if BOTH sides are truth
                    kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                    return result, kind
                result = left_val + right_val
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return result, kind
            if node.op == "-":
                result = left_val - right_val
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return result, kind
            if node.op == "*":
                result = left_val * right_val
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return result, kind
            if node.op == "/":
                if right_val == 0:
                    raise RuntimeError("division by zero")
                result = left_val / right_val
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return result, kind
            if node.op == "%":
                if right_val == 0:
                    raise RuntimeError("modulo by zero")
                result = left_val % right_val
                kind = "truth" if left_kind == "truth" and right_kind == "truth" else "grain"
                return result, kind
                    
            else:
                raise RuntimeError(f"Unsupported operator {node.op}")

        raise RuntimeError(f"Cannot eval node type: {type(node)}")

    # ---------- Built-in ceremonial calls (Ah, Hecho, etc.) ----------

    def _eval_math_builtin(self, name, arg_nodes, line=-1):
        args = [self.eval(arg) for arg in arg_nodes]
        values = [v for v, _ in args]
        kinds = [k for _, k in args]
        result_kind = "truth" if all(k == "truth" for k in kinds) else "grain"

        if name == "Math.PI":
            if len(values) != 0:
                raise RuntimeError("Math.PI does not take arguments")
            return math.pi, "truth"
        if name == "Math.E":
            if len(values) != 0:
                raise RuntimeError("Math.E does not take arguments")
            return math.e, "truth"

        if name == "Math.abs":
            if len(values) != 1:
                raise RuntimeError("Math.abs() expects 1 argument")
            return abs(values[0]), result_kind
        if name == "Math.min":
            if len(values) != 2:
                raise RuntimeError("Math.min() expects 2 arguments")
            return min(values[0], values[1]), result_kind
        if name == "Math.max":
            if len(values) != 2:
                raise RuntimeError("Math.max() expects 2 arguments")
            return max(values[0], values[1]), result_kind
        if name == "Math.pow":
            if len(values) != 2:
                raise RuntimeError("Math.pow() expects 2 arguments")
            return pow(values[0], values[1]), result_kind
        if name == "Math.floor":
            if len(values) != 1:
                raise RuntimeError("Math.floor() expects 1 argument")
            return math.floor(values[0]), result_kind
        if name == "Math.ceil":
            if len(values) != 1:
                raise RuntimeError("Math.ceil() expects 1 argument")
            return math.ceil(values[0]), result_kind
        if name == "Math.sqrt":
            if len(values) != 1:
                raise RuntimeError("Math.sqrt() expects 1 argument")
            if values[0] < 0:
                raise RuntimeError("Math.sqrt() domain error")
            return math.sqrt(values[0]), result_kind
        if name == "Math.round":
            if len(values) != 1:
                raise RuntimeError("Math.round() expects 1 argument")
            # JS-like tie handling: ties go toward +infinity
            x = values[0]
            return math.floor(x + 0.5), result_kind
        if name == "Math.trunc":
            if len(values) != 1:
                raise RuntimeError("Math.trunc() expects 1 argument")
            return math.trunc(values[0]), result_kind
        if name == "Math.sign":
            if len(values) != 1:
                raise RuntimeError("Math.sign() expects 1 argument")
            x = values[0]
            return (1 if x > 0 else (-1 if x < 0 else 0)), result_kind
        if name == "Math.clamp":
            if len(values) != 3:
                raise RuntimeError("Math.clamp() expects 3 arguments")
            x, lo, hi = values
            if lo > hi:
                raise RuntimeError("Math.clamp() requires min <= max")
            return min(max(x, lo), hi), result_kind
        if name == "Math.random":
            if len(values) != 0:
                raise RuntimeError("Math.random() expects 0 arguments")
            return self._next_random(), "truth"
        if name == "Math.sin":
            if len(values) != 1:
                raise RuntimeError("Math.sin() expects 1 argument")
            return math.sin(values[0]), result_kind
        if name == "Math.cos":
            if len(values) != 1:
                raise RuntimeError("Math.cos() expects 1 argument")
            return math.cos(values[0]), result_kind
        if name == "Math.tan":
            if len(values) != 1:
                raise RuntimeError("Math.tan() expects 1 argument")
            return math.tan(values[0]), result_kind
        if name == "Math.sinh":
            if len(values) != 1:
                raise RuntimeError("Math.sinh() expects 1 argument")
            return math.sinh(values[0]), result_kind
        if name == "Math.cosh":
            if len(values) != 1:
                raise RuntimeError("Math.cosh() expects 1 argument")
            return math.cosh(values[0]), result_kind
        if name == "Math.tanh":
            if len(values) != 1:
                raise RuntimeError("Math.tanh() expects 1 argument")
            return math.tanh(values[0]), result_kind

        raise RuntimeError(f"Unknown math function {name}")

    def call(self, node: Call):
        name = self._resolve_std_name(node.name)
        # Ensure args is always treated as a list
        arg_list = node.args if isinstance(node.args, list) else [node.args]

        if name.startswith("Math."):
            self._eval_math_builtin(name, arg_list, getattr(node, "line", -1))
            return

        if name == "SataAndagi":
            self.rt.booted = True
            info = {
                "name": "SATA",
                "version": getattr(self.rt, "version", "1.0"),
                "context": getattr(self.rt, "context_level", 0),
                "warnings": getattr(self.rt, "shame", 0),
            }
            self.rt.last_value = (info, "truth")
            self.rt.info("SataAndagi: runtime booted")
            return

        if name == "Americaya":
            if not arg_list:
                self.rt.warn("Americaya() called without argument")
                return
            val, kind = self.eval(arg_list[0])
            if kind == "grain":
                self.rt.info("Americaya: promoted grainsoftruth to truthaboutgrain")
            self.rt.last_value = (val, "truth")
            return

        if name == "slice":
            x, kx = self.eval(node.args[0])
            a, ka = self.eval(node.args[1])
            b, kb = self.eval(node.args[2])

            if not isinstance(x, (list, str)):
                raise RuntimeError("slice() requires list or string")

            result = x[a:b]
            kind = "truth" if kx == ka == kb == "truth" else "grain"
            return result, kind

        if name == "pop":
            lst_node = node.args[0]

            if not isinstance(lst_node, Variable):
                raise RuntimeError(f"Line {node.line}: pop() requires named list")

            name_var = lst_node.name

            if name_var in self.rt.completed:
                self.rt.warn(f"Line {node.line}: {name_var} is Hecho and cannot be modified")
                return None, self.rt.var_kinds.get(name_var)

            if name_var not in self.rt.acknowledged and name_var not in self.rt.assumed:
                self.rt.warn(f"Line {node.line}: {name_var} mutated without Ah()")
            
            if name_var in self.rt.legacy:
                self.rt.warn(f"{name_var} is Ivebeengot (legacy) and cannot be modified")
                return

            lst, lk = self.eval(lst_node)
            if not isinstance(lst, list):
                raise RuntimeError(f"Line {node.line}: pop() requires list")

            if not lst:
                raise RuntimeError(f"Line {node.line}: pop() on empty list")

            return lst.pop(), lk

        if name == "push":
            lst_node = node.args[0]
            val_node = node.args[1]

            if not isinstance(lst_node, Variable):
                raise RuntimeError(f"Line {node.line}: push() requires named list")

            name_var = lst_node.name

            if name_var in self.rt.completed:
                self.rt.warn(f"Line {node.line}: {name_var} is Hecho and cannot be modified")
                return

            # Standard library mutations do not warn on first change
            if name_var in self.rt.initialised:
                if name_var not in self.rt.acknowledged and name_var not in self.rt.assumed:
                    self.rt.warn(f"Line {node.line}: {name_var} mutated without Ah()")
            
            if name_var in self.rt.legacy:
                self.rt.warn(f"{name_var} is Ivebeengot (legacy) and cannot be modified")
                return None, self.rt.var_kinds.get(name_var, "grain")

            lst, lk = self.eval(lst_node)
            val, vk = self.eval(val_node)

            if not isinstance(lst, list):
                raise RuntimeError(f"Line {node.line}: push() requires list")

            lst.append(val)

            if vk == "grain":
                self.rt.var_kinds[name_var] = "grain"

            return

        if name == "contains":
            m, mk = self.eval(node.args[0])
            k, kk = self.eval(node.args[1])
            if not isinstance(m, dict):
                raise RuntimeError("contains() requires map")
            result = k in m
            kind = "truth" if mk == "truth" and kk == "truth" else "grain"
            return result, kind

        if name == "values":
            if not arg_list:
                self.rt.warn("values() called without argument")
                return
            value, kind = self.eval(arg_list[0])
            if not isinstance(value, dict):
                raise RuntimeError("values() requires map")
            return list(value.values()), kind

        if name == "keys":
            if not arg_list:
                self.rt.warn("keys() called without argument")
                return
            value, kind = self.eval(arg_list[0])
            if not isinstance(value, dict):
                raise RuntimeError("keys() requires map")
            return list(value.keys()), kind

        if name == "len":
            if not arg_list:
                self.rt.warn("len() called without argument")
                return
            value, kind = self.eval(arg_list[0])
            if not isinstance(value, (list, dict, str)):
                raise RuntimeError("len() unsupported type")
            return len(value), kind

        if name == "ReadFile":
            if len(arg_list) != 1:
                raise RuntimeError("ReadFile() expects 1 argument")
            path_val, path_kind = self.eval(arg_list[0])
            resolved = self._resolve_file_path(path_val)
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                raise RuntimeError(f"ReadFile failed: {e}")
            return content, path_kind

        if name == "WriteFile":
            if len(arg_list) != 2:
                raise RuntimeError("WriteFile() expects 2 arguments")
            path_val, path_kind = self.eval(arg_list[0])
            content_val, content_kind = self.eval(arg_list[1])
            resolved = self._resolve_file_path(path_val)
            try:
                parent = os.path.dirname(resolved)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(resolved, "w", encoding="utf-8") as f:
                    f.write(str(content_val))
            except Exception as e:
                raise RuntimeError(f"WriteFile failed: {e}")
            kind = "truth" if path_kind == "truth" and content_kind == "truth" else "grain"
            return 1, kind

        if name == "AppendFile":
            if len(arg_list) != 2:
                raise RuntimeError("AppendFile() expects 2 arguments")
            path_val, path_kind = self.eval(arg_list[0])
            content_val, content_kind = self.eval(arg_list[1])
            resolved = self._resolve_file_path(path_val)
            try:
                parent = os.path.dirname(resolved)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(resolved, "a", encoding="utf-8") as f:
                    f.write(str(content_val))
            except Exception as e:
                raise RuntimeError(f"AppendFile failed: {e}")
            kind = "truth" if path_kind == "truth" and content_kind == "truth" else "grain"
            return 1, kind

        if name == "FileExists":
            if len(arg_list) != 1:
                raise RuntimeError("FileExists() expects 1 argument")
            path_val, path_kind = self.eval(arg_list[0])
            resolved = self._resolve_file_path(path_val)
            return (1 if os.path.exists(resolved) else 0), path_kind

        if name == "DeleteFile":
            if len(arg_list) != 1:
                raise RuntimeError("DeleteFile() expects 1 argument")
            path_val, path_kind = self.eval(arg_list[0])
            resolved = self._resolve_file_path(path_val)
            try:
                if os.path.exists(resolved):
                    os.remove(resolved)
            except Exception as e:
                raise RuntimeError(f"DeleteFile failed: {e}")
            return 1, path_kind

        if name == "Say":
            if not arg_list:
                self.rt.warn(f"Line {node.line}: Say() called without argument")
                return

            value, kind = self.eval(arg_list[0])

            if kind == "grain":
                self.rt.warn(f"Line {node.line}: Say() used on grainsoftruth (output may be unreliable)")

            if isinstance(value, list):
                print("[" + ", ".join(str(v) for v in value) + "]", file=self.rt.stdout)
                return
            
            if isinstance(value, dict):
                items = ", ".join(f"{k}: {v}" for k, v in value.items())
                print("{" + items + "}", file=self.rt.stdout)
                return

            print(value, file=self.rt.stdout)
            return

        # --- Ah ---
        if name == "Ah":
            if not arg_list:
                self.rt.warn(f"Line {node.line}: Ah() called without argument")
                return
            if not isinstance(arg_list[0], Variable):
                self.rt.warn(f"Line {node.line}: Ah() requires a variable name")
                return
            var_name = arg_list[0].name
            self.rt.acknowledged.add(var_name)
            self.rt.info(f"Line {node.line}: {var_name} acknowledged")
            return

        # --- Hecho (freeze variable) ---
        if name == "Hecho":
            if not arg_list:
                self.rt.warn(f"Line {node.line}: Hecho called without variable")
                return
            if not isinstance(arg_list[0], Variable):
                self.rt.warn(f"Line {node.line}: Hecho requires a variable name")
                return
            var_name = arg_list[0].name
            try:
                self.rt.get_var(var_name)
            except RuntimeError:
                self.rt.warn(f"Line {node.line}: Hecho on unknown variable {var_name}")
                return

            kind = self.rt.var_kinds.get(var_name)
            if kind == "grain":
                self.rt.info(
                    f"[Advisory] Line {node.line}: Cannot Hecho grainsoftruth variable {var_name}; "
                    f"promote to truthaboutgrain first"
                )
                return

            if var_name in self.rt.completed:
                self.rt.warn(f"Line {node.line}: {var_name} is already Hecho")
                return

            self.rt.completed.add(var_name)
            self.rt.info(f"Line {node.line}: {var_name} marked as Hecho (frozen)")
            return

        # --- Ohmygah (soft error hook, currently just a log) ---
        if name == "Ohmygah":
            if not arg_list:
                self.rt.warn("Ohmygah called without argument")
                return
            if not isinstance(arg_list[0], Variable):
                self.rt.warn("Ohmygah requires a variable name")
                return
            self.rt.warn(f"Ohmygah triggered on {arg_list[0].name} (non-fatal)")
            return

        # --- Getittogether (stabilise system) ---
        if name == "Getittogether":
            # For now, we only mark unresolved grain as "handled".
            # Types remain grain/truth; this just says "we're okay to proceed".
            if self.rt.has_unresolved_grain:
                self.rt.info(
                    "Getittogether: unresolved grainsoftruth acknowledged and stabilised"
                )
                self.rt.has_unresolved_grain = False
            else:
                self.rt.info("Getittogether: nothing pending, system already stable")
            return

        # --- Escalator (stepwise context progression) ---
        if name == "Escalator":
            if self.rt.has_unresolved_grain:
                self.rt.warn(
                    "Cannot Escalator with unresolved grain data; "
                    "call Getittogether first"
                )
                return
            level_label = arg_list[0].name if arg_list else f"level{self.rt.context_level}"
            self.rt.context_level += 1
            self.rt.info(
                f"Escalator: moved to context level {self.rt.context_level} "
                f"({level_label})"
            )
            return

        # --- Elevator (bigger abstraction jump) ---
        if name == "Elevator":
            if self.rt.has_unresolved_grain:
                self.rt.warn(
                    "Cannot Elevator with unresolved grain data; "
                    "call Getittogether first"
                )
                return
            target_label = arg_list[0].name if arg_list else "HIGH"
            self.rt.context_level = max(self.rt.context_level, 5)
            self.rt.info(
                f"Elevator: jumped to high context level {self.rt.context_level} "
                f"({target_label})"
            )
            return
        
        if name == "youknowsealsright":
            if not arg_list:
                self.rt.warn(f"Line {node.line}: youknowsealsright() called without argument")
                return
            if not isinstance(arg_list[0], Variable):
                self.rt.warn(f"Line {node.line}: youknowsealsright() requires a variable name")
                return
            var_name = arg_list[0].name
            self.rt.assumed.add(var_name)
            self.rt.info(f"Line {node.line}: {var_name} marked as assumed (youknowsealsright)")
            return
        
        if name == "Ivebeengot":
            if not arg_list:
                self.rt.warn(f"Line {node.line}: Ivebeengot() called without argument")
                return
            if not isinstance(arg_list[0], Variable):
                self.rt.warn(f"Line {node.line}: Ivebeengot() requires a variable name")
                return
            var_name = arg_list[0].name
            self.rt.legacy.add(var_name)
            self.rt.info(f"Line {node.line}: {var_name} marked as Ivebeengot (legacy)")
            return

        # --- Unknown call ---
        if name in self.rt.functions:
            # Allow user-defined function calls as statements
            self.call_function(CallExpr(name, arg_list, getattr(node, "line", -1)))
            return

        self.rt.warn(f"Unknown function {name}")

    # ---------- User-defined function calls ----------

    def _validate_arg_count(self, call, expected):
        if len(call.args) != expected:
            raise RuntimeError(f"{call.name}() expects {expected} arguments, got {len(call.args)}")

    def call_function(self, call: CallExpr):
        name = self._resolve_std_name(call.name)

        if name in self.rt.imported_module_functions:
            fn_info = self.rt.imported_module_functions[name]
            fn = fn_info.get("function")
            if fn is None:
                raise RuntimeError(f"Imported function {name} is unavailable")
            if len(call.args) != len(fn.params):
                raise RuntimeError(f"Argument count mismatch in {name}")

            arg_values = [self.eval(arg)[0] for arg in call.args]

            original_functions = self.rt.functions
            merged = dict(self.rt.functions)
            for mname, mfn in fn_info.get("module_functions", {}).items():
                merged.setdefault(mname, mfn)
            self.rt.functions = merged

            self.rt.push_scope()
            try:
                for pname, pvalue in zip(fn.params, arg_values):
                    self.rt.set_var(pname, pvalue)
                    self.rt.var_kinds[pname] = "grain"
                self.execute(fn.body)
                raise RuntimeError(f"Function {name} missing return")
            except ReturnSignal as ret:
                return ret.value, ret.kind
            finally:
                self.rt.pop_scope()
                self.rt.functions = original_functions

        # Namespaced exported value access: alias.symbol
        if len(call.args) == 0:
            try:
                value = self.rt.get_var(name)
                kind = self.rt.var_kinds.get(name, "grain")
                return value, kind
            except RuntimeError:
                pass

        if name.startswith("Math."):
            return self._eval_math_builtin(name, call.args, getattr(call, "line", -1))

        # Handle built-in Say first
        if name == "Say":
            if not call.args:
                self.rt.warn("Say() called without argument")
                return None, None
            
            value, kind = self.eval(call.args[0])
            
            if kind == "grain":
                self.rt.warn("Say() used on grainsoftruth (output may be unreliable)")

            if isinstance(value, list):
                print("[" + ", ".join(str(v) for v in value) + "]", file=self.rt.stdout)
            else:
                print(value, file=self.rt.stdout)
            return None, None

        if name == "SataAndagi":
            self._validate_arg_count(call, 0)
            self.rt.booted = True
            info = {
                "name": "SATA",
                "version": getattr(self.rt, "version", "1.0"),
                "context": getattr(self.rt, "context_level", 0),
                "warnings": getattr(self.rt, "shame", 0),
            }
            self.rt.info("SataAndagi: runtime booted")
            return info, "truth"

        if name == "Americaya":
            self._validate_arg_count(call, 1)
            val, kind = self.eval(call.args[0])
            if kind == "grain":
                self.rt.info("Americaya: promoted grainsoftruth to truthaboutgrain")
            return val, "truth"

        # Handle other built-ins
        builtins = {
            "len": lambda: (self._validate_arg_count(call, 1), (len(self.eval(call.args[0])[0]), "truth"))[1],
            "push": lambda: self._handle_push(call),
            "pop": lambda: self._handle_pop(call),
            "contains": lambda: self._handle_contains(call),
            "keys": lambda: (list(self.eval(call.args[0])[0].keys()), "truth"),
            "values": lambda: (list(self.eval(call.args[0])[0].values()), "truth"),
            "slice": lambda: self._handle_slice(call),
            "ReadFile": lambda: self._handle_read_file(call),
            "WriteFile": lambda: self._handle_write_file(call),
            "AppendFile": lambda: self._handle_append_file(call),
            "FileExists": lambda: self._handle_file_exists(call),
            "DeleteFile": lambda: self._handle_delete_file(call),
        }
        
        if name in builtins:
            return builtins[name]()
            
        if name not in self.rt.functions:
            raise RuntimeError(f"Undefined function {name}")

        fn = self.rt.functions[name]

        if len(call.args) != len(fn.params):
            raise RuntimeError(f"Argument count mismatch in {name}")

        # Evaluate arguments FIRST and discard their kind.
        # Inside the function, params always start as grainsoftruth.
        arg_values = [self.eval(arg)[0] for arg in call.args]

        # New function scope
        self.rt.push_scope()

        # Bind parameters as grainsoftruth by rule
        for name, value in zip(fn.params, arg_values):
            self.rt.set_var(name, value)
            self.rt.var_kinds[name] = "grain"

        try:
            # Execute function body; we expect a ReturnSignal
            self.execute(fn.body)
            raise RuntimeError(f"Function {name} missing return")
        except ReturnSignal as ret:
            self.rt.pop_scope()
            return ret.value, ret.kind

    def _handle_push(self, call):
        self._validate_arg_count(call, 2)
        lst = self.eval(call.args[0])[0]
        value = self.eval(call.args[1])[0]
        lst.append(value)
        return None, None

    def _handle_pop(self, call):
        self._validate_arg_count(call, 1)
        lst = self.eval(call.args[0])[0]
        return lst.pop(), "truth"

    def _handle_contains(self, call):
        self._validate_arg_count(call, 2)
        m = self.eval(call.args[0])[0]
        k = self.eval(call.args[1])[0]
        return k in m, "truth"

    def _handle_slice(self, call):
        self._validate_arg_count(call, 3)
        x = self.eval(call.args[0])[0]
        a = self.eval(call.args[1])[0]
        b = self.eval(call.args[2])[0]
        return x[a:b], "truth"

    def _handle_read_file(self, call):
        self._validate_arg_count(call, 1)
        path_val, path_kind = self.eval(call.args[0])
        resolved = self._resolve_file_path(path_val)
        try:
            with open(resolved, "r", encoding="utf-8") as f:
                return f.read(), path_kind
        except Exception as e:
            raise RuntimeError(f"ReadFile failed: {e}")

    def _handle_write_file(self, call):
        self._validate_arg_count(call, 2)
        path_val, path_kind = self.eval(call.args[0])
        content_val, content_kind = self.eval(call.args[1])
        resolved = self._resolve_file_path(path_val)
        try:
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(str(content_val))
        except Exception as e:
            raise RuntimeError(f"WriteFile failed: {e}")
        kind = "truth" if path_kind == "truth" and content_kind == "truth" else "grain"
        return 1, kind

    def _handle_append_file(self, call):
        self._validate_arg_count(call, 2)
        path_val, path_kind = self.eval(call.args[0])
        content_val, content_kind = self.eval(call.args[1])
        resolved = self._resolve_file_path(path_val)
        try:
            parent = os.path.dirname(resolved)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(resolved, "a", encoding="utf-8") as f:
                f.write(str(content_val))
        except Exception as e:
            raise RuntimeError(f"AppendFile failed: {e}")
        kind = "truth" if path_kind == "truth" and content_kind == "truth" else "grain"
        return 1, kind

    def _handle_file_exists(self, call):
        self._validate_arg_count(call, 1)
        path_val, path_kind = self.eval(call.args[0])
        resolved = self._resolve_file_path(path_val)
        return (1 if os.path.exists(resolved) else 0), path_kind

    def _handle_delete_file(self, call):
        self._validate_arg_count(call, 1)
        path_val, path_kind = self.eval(call.args[0])
        resolved = self._resolve_file_path(path_val)
        try:
            if os.path.exists(resolved):
                os.remove(resolved)
        except Exception as e:
            raise RuntimeError(f"DeleteFile failed: {e}")
        return 1, path_kind