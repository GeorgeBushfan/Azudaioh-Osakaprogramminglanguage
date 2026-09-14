# vm.py
import time
import math
import os
from typing import Any, Dict, List, Tuple
from lexer import lex
from parser import Parser
from runtime import Runtime as CoreRuntime
from compiler import Compiler
from bytecode import (
    Value, BytecodeProgram,
    PUSH_CONST, LOAD_VAR, STORE_VAR, DUP, POP, ADD, SUB, MUL, DIV, MOD,
    MAKE_LIST, MAKE_MAP, INDEX_GET, INDEX_SET,
    CALL_BUILTIN, HALT,
    JMP, JMP_IF_FALSE,
    CMP_EQ, CMP_NE, CMP_LT, CMP_LE, CMP_GT, CMP_GE,
    CALL_FUNC, RET, TRACE_POINT, TRY_PUSH, TRY_POP
)
from builtin_registry import validate_builtin_arity

# Module-level so _call_builtin does not rebuild it on every call.
_STD_ALIASES = {
    "std.len": "len",
    "std.keys": "keys",
    "std.values": "values",
    "std.contains": "contains",
    "std.slice": "slice",
    "std.push": "push",
    "std.pop": "pop",
}

class _Uninitialized:
    """Sentinel for pre-seeded frame locals that have not been assigned yet.

    Pre-seeding function-local names in frame.locals isolates nested calls
    from the shared scope chain, but unassigned slots must remain invisible
    to variable loads and execution traces until first STORE_VAR.
    """


_UNINIT = _Uninitialized()


class Frame:
    def __init__(self, code, consts, functions, ret_ip, locals_):
        self.code = code  # List of (op, arg, line)
        self.consts = consts
        self.functions = functions
        self.ip = 0
        self.ret_ip = ret_ip
        self.locals = locals_
        self.stack = []  # Frame-local stack
        self.current_line = -1  # Track current line number
        self.try_stack = []  # stack of catch instruction pointers
        self.scope_base = 1


class VM:
    """
    Minimal SATA VM (Stage 7.1):
    - Stack machine
    - Enforces your Stage 6 semantics using the Runtime object you already have.
    """
    def __init__(self, runtime, debug=False):
        self.rt = runtime
        self.debug = debug
        self.stack = []
        self.frames = []
        self.scopes = [{}]
        self.vars: Dict[str, Value] = {}  # local global scope for now
        # Cache of id(fn.program.code) -> has-return flag so CALL_FUNC does
        # not rescan the callee body on every call. Keys stay valid because
        # function objects are alive in the frame's function table for the
        # whole run; each VM instance (including module sub-VMs) gets its own.
        self._has_return = {}
        if self.debug:
            self.rt.debug = True


    # ---------- helpers ----------
    def _truth_and(self, a: str, b: str) -> str:
        return "truth" if a == "truth" and b == "truth" else "grain"

    def _resolve_file_path(self, path_value: Any) -> str:
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
            bytecode = Compiler().compile_program(ast)

            module_rt = CoreRuntime()
            module_rt.module_cache = self.rt.module_cache
            module_rt.module_loading = self.rt.module_loading
            module_rt.current_file = resolved
            module_rt.collecting_exports = True
            module_rt.current_module_exports = {}
            module_rt._is_module_run = True
            module_rt.debug = getattr(self.rt, "debug", False)
            module_rt._math_seed = getattr(self.rt, "_math_seed", 123456789)

            # Share output channels with importer run
            module_rt.stdout = self.rt.stdout
            module_rt.stderr = self.rt.stderr
            module_rt.warnings = self.rt.warnings
            module_rt.errors = self.rt.errors

            VM(module_rt, debug=getattr(self.rt, "debug", False)).run(bytecode)

            self.rt._math_seed = getattr(module_rt, "_math_seed", self.rt._math_seed)

            module_obj = {
                "path": resolved,
                "exports": module_rt.current_module_exports or {},
                "functions": dict(bytecode.functions or {}),
            }
            self.rt.module_cache[resolved] = module_obj
            return module_obj
        finally:
            self.rt.module_loading.discard(resolved)

    def _next_random(self) -> float:
        # Deterministic RNG for interpreter/VM equivalence
        seed = getattr(self.rt, "_math_seed", 123456789)
        seed = (1103515245 * seed + 12345) % (2 ** 31)
        self.rt._math_seed = seed
        return seed / float(2 ** 31)

    def _get_var(self, frame, name: str) -> Value:
        local = frame.locals.get(name)
        if local is not None and not isinstance(local.data, _Uninitialized):
            return local
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        self.rt.warn(f"{name} used before assignment (defaulting to 0)")
        return Value(0, "grain")

    def _store_var(self, frame, name: str, val: Value):
        # if local exists, store into local; else scope-chain set_var semantics
        if name in frame.locals:
            target = frame.locals
            # Interpreter parity: parameters are bound as grainsoftruth on
            # first store, regardless of the argument's kind. Detected via
            # the pre-seeded sentinel placeholder.
            existing = frame.locals.get(name)
            if isinstance(existing, Value) and isinstance(existing.data, _Uninitialized):
                if name in getattr(frame, "param_names", ()):
                    val = Value(val.data, "grain")
            # Function locals must not participate in global mutation-policy
            # bookkeeping: repeated helper calls otherwise contaminate each
            # other through Runtime.initialised and emit spurious warnings.
            target[name] = val
            return
        else:
            target = None
            for scope in reversed(self.scopes):
                if name in scope:
                    target = scope
                    break
            if target is None:
                target = self.scopes[-1]

        # Apply legacy/hecho to non-local scope vars
        if target is not frame.locals:
            if name in getattr(self.rt, "legacy", set()):
                self.rt.warn(f"{name} is Ivebeengot (legacy) and cannot be reassigned")
                return
            if name in getattr(self.rt, "completed", set()):
                self.rt.warn(f"{name} is Hecho and cannot be modified")
                return

        initialised = getattr(self.rt, "initialised", set())
        acknowledged = getattr(self.rt, "acknowledged", set())
        assumed = getattr(self.rt, "assumed", set())

        if name in initialised:
            if name not in acknowledged and name not in assumed:
                warned = getattr(self.rt, "mutation_warned", set())
                if name not in warned:
                    self.rt.warn(f"{name} mutated without Ah()")
                    warned.add(name)
                    self.rt.mutation_warned = warned

        target[name] = val
        initialised.add(name)
        self.rt.initialised = initialised

        if hasattr(self.rt, "var_kinds"):
            self.rt.var_kinds[name] = val.kind

    def _index_set(self, base_name: str, container: Value, index: Value, newval: Value):
        # Apply base variable policy (legacy/hecho/ah/assumed/initialised)
        if hasattr(self.rt, "legacy") and base_name in self.rt.legacy:
            self.rt.warn(f"{base_name} is Ivebeengot (legacy) and cannot be modified")
            return

        if hasattr(self.rt, "completed") and base_name in self.rt.completed:
            self.rt.warn(f"{base_name} is Hecho and cannot be modified")
            return

        initialised = getattr(self.rt, "initialised", set())
        acknowledged = getattr(self.rt, "acknowledged", set())
        assumed = getattr(self.rt, "assumed", set())

        if base_name in initialised:
            if base_name not in acknowledged and base_name not in assumed:
                warned = getattr(self.rt, "mutation_warned", set())
                if base_name not in warned:
                    self.rt.warn(f"{base_name} mutated without Ah()")
                    warned.add(base_name)
                    self.rt.mutation_warned = warned

        try:
            # Special handling for lists
            if isinstance(container.data, list):
                idx = index.data
                # Ensure index is within bounds
                if idx < 0:
                    raise IndexError("Negative indices not supported")
                if idx >= len(container.data):
                    # Extend list with None values if needed
                    container.data += [None] * (idx - len(container.data) + 1)
                container.data[idx] = newval.data
            else:
                # For other types (dicts), use regular assignment
                container.data[index.data] = newval.data
        except IndexError as e:
            raise RuntimeError(f"Invalid list assignment: {e}")
        except Exception:
            raise RuntimeError("Invalid map/list assignment")

        # If you insert grain into a truth container, downgrade variable kind
        if newval.kind == "grain" and hasattr(self.rt, "var_kinds"):
            self.rt.var_kinds[base_name] = "grain"

        initialised.add(base_name)
        self.rt.initialised = initialised

    # ---------- builtins ----------
    def _call_builtin(self, name: str, args: List[Value]) -> Value:
        # Policy builtins expect Variable names at AST level.
        # In VM we just pass actual values for now, except the few that operate on "variable names".
        # We'll do policy ones as name strings passed as Value(str, truth).

        std_aliases = _STD_ALIASES

        validate_builtin_arity(name, len(args))

        if name == "__import_module__":
            module = args[0].data
            if module != "std":
                raise RuntimeError(f"Unknown module {module}")
            imports = getattr(self.rt, "imports", set())
            imports.add("std")
            self.rt.imports = imports
            return Value(None, "truth")

        if name == "__sbc_load__":
            # Stage-1 runtime module loading (NATIVE_HOST_CONTRACT §9.1):
            # resolve path against base (the current document path), read and
            # validate the SBC1 artifact, and return a result envelope — this
            # builtin never raises; the guest VM maps ok=0 to its error state.
            import sbc as _sbc
            raw_path = args[0].data
            base = args[1].data
            if not isinstance(base, str) or not base:
                base = None
            if os.path.isabs(raw_path):
                resolved = os.path.normpath(raw_path)
            elif base:
                resolved = os.path.normpath(os.path.join(os.path.dirname(base), raw_path))
            else:
                resolved = os.path.normpath(raw_path)
            if not os.path.exists(resolved):
                return Value({"ok": 0, "message": f"Module file not found: {resolved}"}, "truth")
            try:
                document = _sbc.program_to_dict(_sbc.load(resolved))
            except Exception as e:
                return Value({"ok": 0, "message": str(e)}, "truth")
            return Value({"ok": 1, "path": resolved, "document": document}, "truth")

        if name == "__import_file_module__":
            module_path = args[0].data
            alias = args[1].data
            module_obj = self._load_file_module(module_path)
            frame = self.frames[-1]
            for export_name, export_data in module_obj["exports"].items():
                fq_name = f"{alias}.{export_name}"
                if export_data.get("type") == "value":
                    val = Value(export_data["value"], export_data.get("kind", "grain"))
                    self.scopes[-1][fq_name] = val
                    if hasattr(self.rt, "var_kinds"):
                        self.rt.var_kinds[fq_name] = val.kind
                    if hasattr(self.rt, "initialised"):
                        self.rt.initialised.add(fq_name)
                elif export_data.get("type") == "function":
                    fn_name = export_data.get("name", export_name)
                    fn = module_obj.get("functions", {}).get(fn_name)
                    if fn is None:
                        raise RuntimeError(f"Exported function not found in module: {export_name}")
                    frame.functions[fq_name] = fn
                else:
                    raise RuntimeError(f"Unknown export type for {export_name}")
            return Value(None, "truth")

        if name == "__export_symbol__":
            if not getattr(self.rt, "collecting_exports", False) or getattr(self.rt, "current_module_exports", None) is None:
                raise RuntimeError("export can only be used inside module files")
            symbol_name = args[0].data
            frame = self.frames[-1]
            if symbol_name in frame.functions:
                self.rt.current_module_exports[symbol_name] = {
                    "type": "function",
                    "name": symbol_name,
                }
            else:
                val = self._get_var(frame, symbol_name)
                self.rt.current_module_exports[symbol_name] = {
                    "type": "value",
                    "value": val.data,
                    "kind": val.kind,
                }
            return Value(None, "truth")

        if name.startswith("std."):
            imports = getattr(self.rt, "imports", set())
            if "std" not in imports:
                raise RuntimeError("std module not imported (use 'import std;')")
            if name not in std_aliases:
                raise RuntimeError(f"Unknown std member {name}")
            name = std_aliases[name]

        def ensure_varname(v: Value) -> str:
            if not isinstance(v.data, str):
                raise RuntimeError(f"{name} requires variable name")
            return v.data
        
        if name == "__force_kind_grain__":
            v = args[0]
            v.kind = "grain"
            return v

        if name == "__force_kind_truth__":
            v = args[0]
            v.kind = "truth"
            return v
        
        if name == "__capture_trace__":
            # Capture a trace at this specific point
            self._capture_trace(self.frames[-1])
            return Value(None, "truth")

        if name == "__to_truthaboutgrain__":
            val = args[0]
            # Convert truth values to truthaboutgrain (boolean)
            # Non-zero numbers are True, zero is False
            truth_value = bool(val.data)
            return Value(truth_value, "truth")

        if name == "__is_bool__":
            val = args[0]
            return Value(isinstance(val.data, bool), "truth")

        if name == "__math_call__":
            # Host-delegated Math.* for the self-hosted VM (the bootstrap
            # profile forbids namespaced source calls). Args are guest
            # pairs: [op_name, [[data, kind], ...]]. The result is wrapped
            # back into a guest [data, kind] pair.
            mname = "Math." + args[0].data
            operands = [Value(pair[0], pair[1]) for pair in args[1].data]
            ret = self._call_builtin(mname, operands)
            return Value([ret.data, ret.kind], "truth")

        if name == "__is_float__":
            val = args[0]
            return Value(isinstance(val.data, float), "truth")

        if name == "__json_type__":
            # Structural type probe for the self-hosted SBC1 emitter.
            # 0=int 1=float 2=str 3=list 4=map 5=bool 6=null
            data = args[0].data
            if isinstance(data, bool):
                return Value(5, "truth")
            if data is None:
                return Value(6, "truth")
            if isinstance(data, int):
                return Value(0, "truth")
            if isinstance(data, float):
                return Value(1, "truth")
            if isinstance(data, str):
                return Value(2, "truth")
            if isinstance(data, list):
                return Value(3, "truth")
            if isinstance(data, dict):
                return Value(4, "truth")
            raise RuntimeError("__json_type__ unsupported constant type")

        if name == "__float_repr__":
            # Python repr() of a float: the exact text json.dumps emits.
            data = args[0].data
            if not isinstance(data, float):
                raise RuntimeError("__float_repr__ expects a float")
            return Value(repr(data), "truth")

        if name == "__to_bool_preserve_kind__":
            val = args[0]
            return Value(bool(val.data), val.kind)

        if name == "__bool_not__":
            val = args[0]
            return Value(not bool(val.data), val.kind)

        if name == "__bool_and__":
            left, right = args
            kind = self._truth_and(left.kind, right.kind)
            return Value(bool(left.data) and bool(right.data), kind)

        if name == "__bool_or__":
            left, right = args
            kind = self._truth_and(left.kind, right.kind)
            return Value(bool(left.data) or bool(right.data), kind)

        if name == "SataAndagi":
            info = {
                "name": "SATA",
                "version": getattr(self.rt, "version", "1.0"),
                "context": getattr(self.rt, "context_level", 0),
                "warnings": getattr(self.rt, "shame", 0),
            }
            self.rt.booted = True
            self.rt.info("SataAndagi: runtime booted")
            return Value(info, "truth")

        if name == "Americaya":
            v = args[0]
            if v.kind == "grain":
                self.rt.info("Americaya: promoted grainsoftruth to truthaboutgrain")
            return Value(v.data, "truth")

        if name == "Getittogether":
            self.rt.has_unresolved_grain = False
            return Value(None, "truth")

        if name == "Ohmygah":
            self.rt.warn(f"Ohmygah triggered on {args[0].data} (non-fatal)")
            return Value(None, "truth")

        if name == "__push_scope__":
            self.scopes.append({})
            return Value(None, "truth")

        if name == "__pop_scope__":
            if len(self.scopes) > 1:
                self.scopes.pop()
            return Value(None, "truth")


        # ---- Output ----
        if name == "Args":
            return Value(list(getattr(self.rt, "program_args", [])), "truth")

        if name == "Panic":
            raise RuntimeError(f"Panic: {args[0].data}")

        if name == "Say":
            v = args[0]
            if v.kind == "grain":
                # Get the current line from the frame if available
                line = self.frames[-1].current_line if self.frames else 0
                self.rt.warn(f"Line {line}: Say() used on grainsoftruth (output may be unreliable)")
            if isinstance(v.data, list):
                print("[" + ", ".join(str(x) for x in v.data) + "]", file=self.rt.stdout)
            elif isinstance(v.data, dict):
                items = ", ".join(f"{k}: {val}" for k, val in v.data.items())
                print("{" + items + "}", file=self.rt.stdout)
            else:
                print(v.data, file=self.rt.stdout)
            return Value(None, "truth")

        # ---- Queries ----
        if name == "len":
            x = args[0]
            if not isinstance(x.data, (list, dict, str)):
                raise RuntimeError("len() unsupported type")
            return Value(len(x.data), x.kind)

        if name == "ReadFile":
            if len(args) != 1:
                raise RuntimeError("ReadFile() expects 1 argument")
            path = args[0]
            resolved = self._resolve_file_path(path.data)
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                raise RuntimeError(f"ReadFile failed: {e}")
            return Value(content, path.kind)

        if name == "WriteFile":
            if len(args) != 2:
                raise RuntimeError("WriteFile() expects 2 arguments")
            path, content = args
            resolved = self._resolve_file_path(path.data)
            try:
                parent = os.path.dirname(resolved)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(resolved, "w", encoding="utf-8") as f:
                    f.write(str(content.data))
            except Exception as e:
                raise RuntimeError(f"WriteFile failed: {e}")
            return Value(1, self._truth_and(path.kind, content.kind))

        if name == "AppendFile":
            if len(args) != 2:
                raise RuntimeError("AppendFile() expects 2 arguments")
            path, content = args
            resolved = self._resolve_file_path(path.data)
            try:
                parent = os.path.dirname(resolved)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(resolved, "a", encoding="utf-8") as f:
                    f.write(str(content.data))
            except Exception as e:
                raise RuntimeError(f"AppendFile failed: {e}")
            return Value(1, self._truth_and(path.kind, content.kind))

        if name == "FileExists":
            if len(args) != 1:
                raise RuntimeError("FileExists() expects 1 argument")
            path = args[0]
            resolved = self._resolve_file_path(path.data)
            return Value(1 if os.path.exists(resolved) else 0, path.kind)

        if name == "DeleteFile":
            if len(args) != 1:
                raise RuntimeError("DeleteFile() expects 1 argument")
            path = args[0]
            resolved = self._resolve_file_path(path.data)
            try:
                if os.path.exists(resolved):
                    os.remove(resolved)
            except Exception as e:
                raise RuntimeError(f"DeleteFile failed: {e}")
            return Value(1, path.kind)

        if name == "keys":
            m = args[0]
            if not isinstance(m.data, dict):
                raise RuntimeError("keys() requires map")
            return Value(list(m.data.keys()), m.kind)

        if name == "values":
            m = args[0]
            if not isinstance(m.data, dict):
                raise RuntimeError("values() requires map")
            return Value(list(m.data.values()), m.kind)

        if name == "contains":
            m, k = args
            if not isinstance(m.data, dict):
                raise RuntimeError("contains() requires map")
            kind = self._truth_and(m.kind, k.kind)
            return Value(k.data in m.data, kind)

        if name == "slice":
            x, a, b = args
            if not isinstance(x.data, (list, str)):
                raise RuntimeError("slice() requires list or string")
            kind = "truth" if x.kind == a.kind == b.kind == "truth" else "grain"
            return Value(x.data[a.data:b.data], kind)

        # ---- Math object builtins ----
        if name == "Math.abs":
            if len(args) != 1:
                raise RuntimeError("Math.abs() expects 1 argument")
            x = args[0]
            return Value(abs(x.data), x.kind)

        if name == "Math.min":
            if len(args) != 2:
                raise RuntimeError("Math.min() expects 2 arguments")
            a, b = args
            kind = self._truth_and(a.kind, b.kind)
            return Value(min(a.data, b.data), kind)

        if name == "Math.max":
            if len(args) != 2:
                raise RuntimeError("Math.max() expects 2 arguments")
            a, b = args
            kind = self._truth_and(a.kind, b.kind)
            return Value(max(a.data, b.data), kind)

        if name == "Math.pow":
            if len(args) != 2:
                raise RuntimeError("Math.pow() expects 2 arguments")
            a, b = args
            kind = self._truth_and(a.kind, b.kind)
            return Value(pow(a.data, b.data), kind)

        if name == "Math.floor":
            if len(args) != 1:
                raise RuntimeError("Math.floor() expects 1 argument")
            x = args[0]
            return Value(math.floor(x.data), x.kind)

        if name == "Math.ceil":
            if len(args) != 1:
                raise RuntimeError("Math.ceil() expects 1 argument")
            x = args[0]
            return Value(math.ceil(x.data), x.kind)

        if name == "Math.sqrt":
            if len(args) != 1:
                raise RuntimeError("Math.sqrt() expects 1 argument")
            x = args[0]
            if x.data < 0:
                raise RuntimeError("Math.sqrt() domain error")
            return Value(math.sqrt(x.data), x.kind)

        if name == "Math.round":
            if len(args) != 1:
                raise RuntimeError("Math.round() expects 1 argument")
            x = args[0]
            # JS-like: ties go toward +infinity
            return Value(math.floor(x.data + 0.5), x.kind)

        if name == "Math.trunc":
            if len(args) != 1:
                raise RuntimeError("Math.trunc() expects 1 argument")
            x = args[0]
            return Value(math.trunc(x.data), x.kind)

        if name == "Math.sign":
            if len(args) != 1:
                raise RuntimeError("Math.sign() expects 1 argument")
            x = args[0]
            data = 1 if x.data > 0 else (-1 if x.data < 0 else 0)
            return Value(data, x.kind)

        if name == "Math.clamp":
            if len(args) != 3:
                raise RuntimeError("Math.clamp() expects 3 arguments")
            x, lo, hi = args
            if lo.data > hi.data:
                raise RuntimeError("Math.clamp() requires min <= max")
            kind = "truth" if x.kind == lo.kind == hi.kind == "truth" else "grain"
            return Value(min(max(x.data, lo.data), hi.data), kind)

        if name == "Math.PI":
            if len(args) != 0:
                raise RuntimeError("Math.PI does not take arguments")
            return Value(math.pi, "truth")

        if name == "Math.E":
            if len(args) != 0:
                raise RuntimeError("Math.E does not take arguments")
            return Value(math.e, "truth")

        if name == "Math.random":
            if len(args) != 0:
                raise RuntimeError("Math.random() expects 0 arguments")
            return Value(self._next_random(), "truth")

        if name == "Math.sin":
            if len(args) != 1:
                raise RuntimeError("Math.sin() expects 1 argument")
            x = args[0]
            return Value(math.sin(x.data), x.kind)

        if name == "Math.cos":
            if len(args) != 1:
                raise RuntimeError("Math.cos() expects 1 argument")
            x = args[0]
            return Value(math.cos(x.data), x.kind)

        if name == "Math.tan":
            if len(args) != 1:
                raise RuntimeError("Math.tan() expects 1 argument")
            x = args[0]
            return Value(math.tan(x.data), x.kind)

        if name == "Math.sinh":
            if len(args) != 1:
                raise RuntimeError("Math.sinh() expects 1 argument")
            x = args[0]
            return Value(math.sinh(x.data), x.kind)

        if name == "Math.cosh":
            if len(args) != 1:
                raise RuntimeError("Math.cosh() expects 1 argument")
            x = args[0]
            return Value(math.cosh(x.data), x.kind)

        if name == "Math.tanh":
            if len(args) != 1:
                raise RuntimeError("Math.tanh() expects 1 argument")
            x = args[0]
            return Value(math.tanh(x.data), x.kind)

        # ---- Mutators (VM version: assumes list passed by reference) ----
        if name == "push":
            lst, v = args
            if not isinstance(lst.data, list):
                # Get the current line from the frame if available
                line = self.frames[-1].current_line if self.frames else 0
                raise RuntimeError(f"Line {line}: push() requires list")
            lst.data.append(v.data)
            kind = "grain" if (lst.kind == "grain" or v.kind == "grain") else "truth"
            lst.kind = kind
            return Value(None, "truth")

        if name == "pop":
            lst = args[0]
            if not isinstance(lst.data, list):
                # Get the current line from the frame if available
                line = self.frames[-1].current_line if self.frames else 0
                raise RuntimeError(f"Line {line}: pop() requires list")
            if not lst.data:
                # Get the current line from the frame if available
                line = self.frames[-1].current_line if self.frames else 0
                raise RuntimeError(f"Line {line}: pop() on empty list")
            return Value(lst.data.pop(), lst.kind)

        # ---- Policy ----
        if name == "Ah":
            var_name = ensure_varname(args[0])
            self.rt.acknowledged.add(var_name)
            # Get the current line from the frame if available
            line = self.frames[-1].current_line if self.frames else 0
            self.rt.info(f"Line {line}: {var_name} acknowledged")
            return Value(None, "truth")

        if name == "Hecho":
            var_name = ensure_varname(args[0])
            kind = getattr(self.rt, "var_kinds", {}).get(var_name)
            if kind == "grain":
                line = self.frames[-1].current_line if self.frames else 0
                self.rt.info(
                    f"[Advisory] Line {line}: Cannot Hecho grainsoftruth variable {var_name}; "
                    f"promote to truthaboutgrain first"
                )
                return Value(None, "truth")
            self.rt.completed.add(var_name)
            # Get the current line from the frame if available
            line = self.frames[-1].current_line if self.frames else 0
            self.rt.info(f"Line {line}: {var_name} marked as Hecho (frozen)")
            return Value(None, "truth")

        if name == "youknowsealsright":
            var_name = ensure_varname(args[0])
            self.rt.assumed.add(var_name)
            # Get the current line from the frame if available
            line = self.frames[-1].current_line if self.frames else 0
            self.rt.info(f"Line {line}: {var_name} marked as assumed (youknowsealsright)")
            return Value(None, "truth")

        if name == "Ivebeengot":
            var_name = ensure_varname(args[0])
            self.rt.legacy.add(var_name)
            # Get the current line from the frame if available
            line = self.frames[-1].current_line if self.frames else 0
            self.rt.info(f"Line {line}: {var_name} marked as Ivebeengot (legacy)")
            return Value(None, "truth")
            
        # Arithmetic builtins
        if name == "add":
            b = args[0]
            a = args[1]
            result = a.data + b.data
            kind = self._truth_and(a.kind, b.kind)
            return Value(result, kind)

        raise RuntimeError(f"Unknown builtin {name}")

    # ---------- execution trace capture ----------
    def _capture_trace(self, frame):
        """Capture VM execution state matching interpreter trace format"""
        # Opt-out for long batch runs: each trace snapshots every variable
        # plus the full stdout/stderr buffers into an unbounded list, which
        # grows without limit over a self-hosting compile (and costs CPU).
        if not getattr(self.rt, "capture_traces", True):
            return
        # Debug: print trace capture attempt
        if self.debug:
            print(f"Attempting to capture trace at line {frame.current_line}")
            
        # Collect all variables (flatten scopes + frame locals)
        variables = {}
        for scope in self.scopes:
            variables.update(scope)
        variables.update(frame.locals)
        
        # Debug: print variables count
        if self.debug:
            print(f"  Found {len(variables)} variables")
        
        # Create variable dictionary with raw values to match interpreter traces.
        # Pre-seeded-but-unassigned frame locals are omitted so traces mirror
        # the interpreter (assignments that never completed leave no variable).
        var_data = {}
        for name, val in variables.items():
            if isinstance(val.data, _Uninitialized):
                continue
            var_data[name] = val.data
            
        # Debug: print variable data
        if self.debug and var_data:
            print(f"  First variable: {list(var_data.keys())[0]} = {list(var_data.values())[0]}")
        
        # Build call stack
        call_stack = []
        for f in self.frames:
            # Use function name if available, otherwise use 'main'
            name = getattr(f, 'function_name', None) or 'main'
            call_stack.append(name)
        
        # Create trace dictionary matching interpreter's format
        trace = {
            "line": frame.current_line,
            "function": call_stack[-1] if call_stack else "global",
            "call_stack": call_stack.copy(),
            "expression": None,
            "variables": var_data,
            "warnings": list(getattr(self.rt, "warnings", [])),
            "errors": [],
            "stdout": self.rt.stdout.getvalue(),
            "stderr": self.rt.stderr.getvalue(),
            "timestamp": time.time()
        }
        
        # Add to runtime traces
        if hasattr(self.rt, 'execution_traces'):
            self.rt.execution_traces.append(trace)
            if self.debug:
                print(f"  Added trace to runtime (total: {len(self.rt.execution_traces)})")
        else:
            if self.debug:
                print("  ERROR: runtime has no execution_traces attribute")
                
        # Debug output
        if self.debug:
            print(f"Captured VM trace at line {frame.current_line}")
            print(f"  Function: {trace['function']}")
            print(f"  Variables: {list(var_data.keys())}")

    def format_trace(self, trace):
        """Format a VM trace for human-readable output"""
        output = f"PC {trace['ip']} in {trace['function']}\n"
        output += f"Call stack: {' -> '.join(trace['call_stack'])}\n"
        
        output += "Variables:\n"
        for var, (val, kind) in trace['variables'].items():
            output += f"  {var} = {val} ({kind})\n"
            
        if trace['warnings']:
            output += "Warnings:\n"
            for warning in trace['warnings']:
                output += f"  - {warning}\n"
                
        if trace['stdout']:
            output += f"STDOUT: {trace['stdout']}\n"
            
        if trace['stderr']:
            output += f"STDERR: {trace['stderr']}\n"
            
        return output

    # ---------- execute ----------
    def run(self, program: BytecodeProgram):
        from verifier import verify_program
        verify_program(program, debug=self.debug)

        # Initialize execution traces
        if not hasattr(self.rt, 'execution_traces'):
            self.rt.execution_traces = []
        else:
            self.rt.execution_traces = []
            
        # Reset other runtime state
        self.rt.initialised = set()
        self.rt.mutation_warned = set()
        self.rt.acknowledged = set()
        self.rt.assumed = set()
        self.rt.var_kinds = {}
        self.rt.imports = set()
        self.rt.module_cache = getattr(self.rt, "module_cache", {})
        self.rt.module_loading = getattr(self.rt, "module_loading", set())
        self.rt.collecting_exports = getattr(self.rt, "collecting_exports", False)
        self.rt.current_module_exports = getattr(self.rt, "current_module_exports", None)
        
        # Clear I/O buffers for top-level runs only.
        if not getattr(self.rt, "_is_module_run", False):
            self.rt.stdout.seek(0)
            self.rt.stdout.truncate(0)
            self.rt.stderr.seek(0)
            self.rt.stderr.truncate(0)
        
        # Debug: show trace initialization
        if self.debug:
            print(f"VM: Initialized execution_traces (count={len(self.rt.execution_traces)})")
            
        self.rt.initialised = set()
        self.rt.acknowledged = set()
        self.rt.assumed = set()
        self.rt.var_kinds = {}
        
        if self.debug:
            print("Function table contents:")
            for name, fn in program.functions.items():
                print(f"  {name}: {len(fn.params)} parameters")
            
            print("\nBytecode to execute:")
            for i, (op, arg, line) in enumerate(program.code):
                print(f"  {i}: {op} {arg} (line {line})")
            
        # Create entry frame
        entry = Frame(program.code, program.consts, program.functions or {}, ret_ip=-1, locals_={})
        entry.scope_base = len(self.scopes)
        self.frames.append(entry)
        
        # Unified execution loop
        debug = self.debug
        while self.frames:
            frame = self.frames[-1]
            
            # Check if frame has finished execution
            if frame.ip >= len(frame.code):
                if self.debug:
                    print("  Frame finished execution")
                return_value = frame.stack.pop() if frame.stack else Value(None, "truth")
                if self.debug:
                    print(f"  Return value: {return_value}")
                self.frames.pop()
                if self.debug:
                    print(f"  Frames remaining: {len(self.frames)}")
                if self.frames:
                    caller_frame = self.frames[-1]
                    caller_frame.stack.append(return_value)
                    if self.debug:
                        print(f"  Pushed return value to caller frame")
                continue
                
            # Get next instruction
            op, arg, line = frame.code[frame.ip]
            frame.current_line = line  # Update current line
            frame.ip += 1
            
            if debug:
                print(f"VM: Capturing trace at IP={frame.ip-1}, line={line}")
            
            # Debug print
            if debug:
                print(f"\nStep: Frame {id(frame)} IP={frame.ip}/{len(frame.code)} Stack size={len(frame.stack)}")
                if frame.ip < len(frame.code):
                    op, arg, _ = frame.code[frame.ip]
                    print(f"  Executing: {op} {arg}")
            
            # Process instruction using frame-local stack
            try:
                if op == PUSH_CONST:
                    const_val = frame.consts[arg]
                    if self.debug:
                        print(f"  PUSH_CONST: {const_val}")
                    frame.stack.append(Value(const_val.data, const_val.kind))
                elif op == LOAD_VAR:
                    frame.stack.append(self._get_var(frame, arg))
                elif op == STORE_VAR:
                    v = frame.stack.pop()
                    self._store_var(frame, arg, v)
                elif op == DUP:
                    if not frame.stack:
                        raise RuntimeError("DUP on empty stack")
                    top = frame.stack[-1]
                    frame.stack.append(Value(top.data, top.kind))
                elif op == POP:
                    if not frame.stack:
                        raise RuntimeError("POP on empty stack")
                    frame.stack.pop()
                elif op == ADD:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    # Numeric addition permits mixed integer/float values;
                    # string concatenation still requires two strings.
                    if isinstance(a.data, (int, float)) and isinstance(b.data, (int, float)):
                        data = a.data + b.data
                    elif isinstance(a.data, str) and isinstance(b.data, str):
                        data = a.data + b.data
                    else:
                        raise RuntimeError(f"ADD operation unsupported for types: {type(a.data)} and {type(b.data)}")
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(data, kind))
                elif op == SUB:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    if not isinstance(a.data, (int, float)) or not isinstance(b.data, (int, float)):
                        raise RuntimeError("SUB operation requires numeric operands")
                    data = a.data - b.data
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(data, kind))
                elif op == MUL:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    if not isinstance(a.data, (int, float)) or not isinstance(b.data, (int, float)):
                        raise RuntimeError("MUL operation requires numeric operands")
                    data = a.data * b.data
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(data, kind))
                elif op == DIV:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    if b.data == 0:
                        raise RuntimeError("division by zero")
                    if not isinstance(a.data, (int, float)) or not isinstance(b.data, (int, float)):
                        raise RuntimeError("DIV operation requires numeric operands")
                    data = a.data / b.data
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(data, kind))
                elif op == MOD:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    if b.data == 0:
                        raise RuntimeError("modulo by zero")
                    if not isinstance(a.data, (int, float)) or not isinstance(b.data, (int, float)):
                        raise RuntimeError("MOD operation requires numeric operands")
                    data = a.data % b.data
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(data, kind))
                elif op == MAKE_LIST:
                    n = arg
                    items = [frame.stack.pop() for _ in range(n)][::-1]
                    data = [v.data for v in items]
                    kind = "truth" if all(v.kind == "truth" for v in items) else "grain"
                    frame.stack.append(Value(data, kind))
                elif op == MAKE_MAP:
                    n = arg
                    pairs = []
                    for _ in range(n):
                        val = frame.stack.pop()
                        key = frame.stack.pop()
                        pairs.append((key, val))
                    pairs.reverse()
                    d = {}
                    kinds = []
                    for k, v in pairs:
                        d[k.data] = v.data
                        kinds.append(v.kind)
                    kind = "truth" if all(k == "truth" for k in kinds) else "grain"
                    frame.stack.append(Value(d, kind))
                elif op == INDEX_GET:
                    idx = frame.stack.pop()
                    cont = frame.stack.pop()
                    try:
                        data = cont.data[idx.data]
                    except Exception:
                        raise RuntimeError("Invalid map/list access")
                    frame.stack.append(Value(data, cont.kind))
                elif op == INDEX_SET:
                    base_name = arg
                    newv = frame.stack.pop()
                    idx = frame.stack.pop()
                    cont = frame.stack.pop()
                    self._index_set(base_name, cont, idx, newv)
                elif op == CALL_BUILTIN:
                    name, argc = arg
                    args = [frame.stack.pop() for _ in range(argc)][::-1]
                    ret = self._call_builtin(name, args)
                    if ret is None:
                        ret = Value(None, "truth")
                    frame.stack.append(ret)
                elif op == CMP_EQ:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(a.data == b.data, kind))
                elif op == CMP_NE:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(a.data != b.data, kind))
                elif op == CMP_LT:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(a.data < b.data, kind))
                elif op == CMP_LE:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(a.data <= b.data, kind))
                elif op == CMP_GT:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(a.data > b.data, kind))
                elif op == CMP_GE:
                    b = frame.stack.pop()
                    a = frame.stack.pop()
                    kind = self._truth_and(a.kind, b.kind)
                    frame.stack.append(Value(a.data >= b.data, kind))
                elif op == JMP:
                    frame.ip = arg  # Directly set instruction pointer
                elif op == CALL_FUNC:
                    fname, argc = arg
                    if fname not in frame.functions:
                        raise RuntimeError(f"Undefined function '{fname}'")
                    fn = frame.functions[fname]
                    expected_args = len(fn.params)
                    if len(frame.stack) < argc:
                        raise RuntimeError(f"Function '{fname}' expects {expected_args} arguments, but only {len(frame.stack)} available")
                    
                    # Check for return instruction in function bytecode.
                    # Cached per callee body: rescanning the whole function on
                    # every call dominated self-hosting workloads.
                    code_id = id(fn.program.code)
                    has_return = self._has_return.get(code_id)
                    if has_return is None:
                        has_return = any(instr_op == RET for instr_op, _, _ in fn.program.code)
                        self._has_return[code_id] = has_return
                    if not has_return:
                        raise RuntimeError(f"Function {fname} missing return")

                    # Pop arguments in reverse order (last argument first)
                    args = [frame.stack.pop() for _ in range(argc)]
                    # Reverse to get original argument order
                    args.reverse()
                    # Create a new frame with all function-local variables pre-seeded
                    # with a sentinel so nested calls cannot contaminate the caller's
                    # scope chain. Slots become real values on first STORE_VAR.
                    param_locals = {var: Value(_UNINIT, "grain") for var in fn.locals}
                    newf = Frame(fn.program.code, fn.program.consts, frame.functions, ret_ip=frame.ip, locals_=param_locals)
                    newf.function_name = fname
                    newf.param_names = set(fn.params)
                    newf.scope_base = len(self.scopes)
                    # Push arguments in correct order (first argument first)
                    for arg_val in args:
                        newf.stack.append(arg_val)
                    self.frames.append(newf)
                    # Skip further processing in current frame
                    continue
                elif op == RET:
                    ret = frame.stack.pop() if frame.stack else Value(None, "truth")
                    while len(self.scopes) > frame.scope_base:
                        self.scopes.pop()
                    self.frames.pop()
                    if self.frames:
                        # Push return value to caller's stack
                        caller_frame = self.frames[-1]
                        caller_frame.stack.append(ret)
                elif op == JMP_IF_FALSE:
                    cond = frame.stack.pop()
                    if cond.kind != "truth":
                        raise RuntimeError(
                            f"Control flow condition must be truthaboutgrain "
                            f"(function={getattr(frame, 'function_name', 'main')}, line={line}, value={cond.data!r})"
                        )
                    if not cond.data:
                        frame.ip = arg
                elif op == TRACE_POINT:
                    # Capture trace at explicit trace points
                    self._capture_trace(frame)
                elif op == TRY_PUSH:
                    frame.try_stack.append((arg, len(self.scopes), len(frame.stack)))
                elif op == TRY_POP:
                    if frame.try_stack:
                        frame.try_stack.pop()
                elif op == HALT:
                    break
                else:
                    raise RuntimeError(f"Unknown opcode {op}")
            except Exception as e:
                handled = False
                while self.frames:
                    top = self.frames[-1]
                    if top.try_stack:
                        catch_ip, scope_depth, stack_height = top.try_stack.pop()
                        while len(self.scopes) > scope_depth:
                            self.scopes.pop()
                        del top.stack[stack_height:]
                        top.ip = catch_ip
                        handled = True
                        break
                    while len(self.scopes) > top.scope_base:
                        self.scopes.pop()
                    self.frames.pop()
                if handled:
                    continue
                raise
