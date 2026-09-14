#!/usr/bin/env python3
"""transpile_vm.py — Gate E one-time transpiler: Osaka -> C.

Transpiles the bootstrap-profile Osaka modules that implement the VM
(selfhost/support.saka + selfhost/vm.saka) into native/vm.c, which the
native host (native/*.c) builds into the `osakavm` binary.

Design (see docs/NATIVE_HOST_CONTRACT.md):

- Input is the Stage 0 AST (lexer + parser), validated with
  bootstrap_profile.validate_bootstrap_ast — a closed set of node types.
- Every Osaka value is a guest Value = [data, kind] pair, represented
  literally as a 2-element list V* (element 0 = data, element 1 = kind).
- Every runtime helper (rt_*, b_*) CONSUMES its arguments and returns an
  owned V*; variable references in generated code use rt_incref.
- Locals are function-wide (matching vm_derive_locals: every STORE_VAR /
  INDEX_SET target is a frame local; SBC1 has no block scoping for locals).
  All assigned names are hoisted to the top of the generated C function and
  decref'd at the single exit label.
- vf_builtin_arities (normally defined in verifier.saka, which would drag in
  the compiler module's registry tables) is generated here directly from
  builtin_registry.BUILTIN_ARITIES — the same name->arity map vm.saka
  consumes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lexer import lex
from parser import Parser
from ast_nodes import (
    Assign, BinaryOp, Block, Break, Call, CallExpr, Compare, Continue,
    FunctionDef, If, IndexAccess, IndexAssign, ListLiteral, MapLiteral,
    Number, Return, String, UnaryOp, Variable, While,
)
from bootstrap_profile import validate_bootstrap_ast
from builtin_registry import BUILTIN_ARITIES

MODULES = ["selfhost/support.saka", "selfhost/vm.saka"]
OUTPUT = Path("native/vm.c")

# Functions provided natively instead of transpiled (call sites still resolve
# to f_<name>): vf_builtin_arities is generated from builtin_registry;
# vm_int_to_text is a pure int->decimal-string utility whose guest
# implementation uses repeated-subtraction division (O(value)); the native
# version in builtins.c is O(digits) with identical output.
NATIVE_FUNCS = {"vf_builtin_arities", "vm_int_to_text"}

# Host-boundary builtins callable directly from the transpiled modules.
BUILTIN_C = {
    "__json_type__": "b_json_type",
    "__is_bool__": "b_is_bool",
    "__is_float__": "b_is_float",
    "__float_repr__": "b_float_repr",
    "__math_call__": "b_math_call",
    "__push_scope__": "b_push_scope",
    "__pop_scope__": "b_pop_scope",
    "ReadFile": "b_read_file",
    "WriteFile": "b_write_file",
    "AppendFile": "b_append_file",
    "FileExists": "b_file_exists",
    "DeleteFile": "b_delete_file",
    "slice": "b_slice",
    "len": "b_len",
    "push": "b_push",
    "pop": "b_pop",
    "keys": "b_keys",
    "values": "b_values",
    "contains": "b_contains",
    "Americaya": "b_americaya",
    "__sbc_load__": "b_sbc_load",
}

BIN_OPS = {
    "+": "rt_add", "-": "rt_sub", "*": "rt_mul", "/": "rt_div", "%": "rt_mod",
    "and": "rt_bool_and", "or": "rt_bool_or",
}
CMP_OPS = {
    "==": "rt_eq", "!=": "rt_ne", "<": "rt_lt",
    "<=": "rt_le", ">": "rt_gt", ">=": "rt_ge",
}


class TranspileError(Exception):
    pass


def c_str(s):
    """Encode a Python string as a C string literal (octal escapes)."""
    out = ['"']
    for ch in s:
        b = ch.encode("utf-8")
        for byte in b:
            c = chr(byte)
            if c == '"':
                out.append('\\"')
            elif c == "\\":
                out.append("\\\\")
            elif 32 <= ord(c) < 127:
                out.append(c)
            else:
                out.append("\\%03o" % byte)
    out.append('"')
    return "".join(out)


class Transpiler:
    def __init__(self, functions):
        self.functions = functions   # name -> FunctionDef
        self.temp = 0

    # ---------- expressions (each yields an owned V* C expression) ----------

    def gen_expr(self, node):
        # Literals are guest Values: pairs whose kind matches the Stage 0
        # const pool (compiler.py: int/str literals -> "truth", float
        # literals -> "grain").
        if isinstance(node, Number):
            if isinstance(node.value, float):
                return "rt_pair(rt_float(%r), K_GRAIN)" % node.value
            return "rt_pair(rt_int(%d), K_TRUTH)" % node.value
        if isinstance(node, String):
            return "rt_pair(rt_lit(%s), K_TRUTH)" % c_str(node.value)
        if isinstance(node, Variable):
            return "rt_incref(%s)" % self.var(node.name)
        if isinstance(node, IndexAccess):
            if isinstance(node.index, String):
                # literal map-key fast path: no key-pair allocation
                return "rt_index_get_lit(%s, %s)" % (
                    self.gen_expr(node.container), c_str(node.index.value))
            return "rt_index_get(%s, %s)" % (
                self.gen_expr(node.container), self.gen_expr(node.index))
        if isinstance(node, ListLiteral):
            n = len(node.elements)
            if n == 0:
                return "rt_make_list(0, 0)"
            elems = ", ".join(self.gen_expr(e) for e in node.elements)
            return "rt_make_list(%d, (V*[]){%s})" % (n, elems)
        if isinstance(node, MapLiteral):
            n = len(node.pairs)
            if n == 0:
                return "rt_make_map(0, 0, 0)"
            keys = ", ".join(self.gen_expr(k) for k, _ in node.pairs)
            vals = ", ".join(self.gen_expr(v) for _, v in node.pairs)
            return "rt_make_map(%d, (V*[]){%s}, (V*[]){%s})" % (n, keys, vals)
        if isinstance(node, BinaryOp):
            fn = BIN_OPS.get(node.op)
            if not fn:
                raise TranspileError("unsupported binary op %r" % node.op)
            return "%s(%s, %s)" % (fn, self.gen_expr(node.left),
                                   self.gen_expr(node.right))
        if isinstance(node, Compare):
            # string-literal equality fast path (no literal-pair allocation)
            if node.op in ("==", "!="):
                lit = None
                other = None
                if isinstance(node.right, String):
                    lit, other = node.right.value, node.left
                elif isinstance(node.left, String):
                    lit, other = node.left.value, node.right
                if lit is not None:
                    fn = "rt_eq_lit" if node.op == "==" else "rt_ne_lit"
                    return "%s(%s, %s)" % (fn, self.gen_expr(other), c_str(lit))
            fn = CMP_OPS.get(node.op)
            if not fn:
                raise TranspileError("unsupported compare op %r" % node.op)
            return "%s(%s, %s)" % (fn, self.gen_expr(node.left),
                                   self.gen_expr(node.right))
        if isinstance(node, UnaryOp):
            if node.op == "not":
                return "rt_bool_not(%s)" % self.gen_expr(node.operand)
            if node.op == "-":
                return "rt_neg(%s)" % self.gen_expr(node.operand)
            raise TranspileError("unsupported unary op %r" % node.op)
        if isinstance(node, (Call, CallExpr)):
            args = ", ".join(self.gen_expr(a) for a in node.args)
            if node.name in self.functions:
                return "f_%s(%s)" % (node.name, args)
            if node.name in BUILTIN_C:
                return "%s(%s)" % (BUILTIN_C[node.name], args)
            raise TranspileError("call to unimplemented builtin %r" % node.name)
        raise TranspileError("unsupported expression %s" % type(node).__name__)

    # ---------- statements ----------

    def var(self, name):
        return "v_%s" % name

    def gen_stmts(self, stmts, ind):
        out = []
        for s in stmts:
            out.extend(self.gen_stmt(s, ind))
        return out

    def cond_fast(self, node):
        """Zero-allocation condition form for If/While: returns a C int
        expression consuming its operand, or None if not applicable."""
        if not isinstance(node, Compare) or node.op not in ("==", "!="):
            return None
        fn = "rt_cond_eq_lit" if node.op == "==" else "rt_cond_ne_lit"
        lit = None
        other = None
        if isinstance(node.right, String):
            lit, other = node.right.value, node.left
        elif isinstance(node.left, String):
            lit, other = node.left.value, node.right
        if lit is not None:
            return "%s(%s, %s)" % (fn, self.gen_expr(other), c_str(lit))
        # map[key] == lit  (the interpreter dispatch pattern)
        idx = None
        if isinstance(node.right, IndexAccess):
            idx, litnode = node.right, node.left
        elif isinstance(node.left, IndexAccess):
            idx, litnode = node.left, node.right
        if (idx is not None and isinstance(litnode, String)
                and isinstance(idx.index, String)
                and isinstance(idx.container, Variable)):
            return "rt_cond_index_eq_lit(%s, %s, %s)" % (
                self.gen_expr(idx.container), c_str(idx.index.value),
                c_str(litnode.value))
        return None

    def gen_stmt(self, node, ind):
        pad = "    " * ind
        if isinstance(node, Assign):
            return [
                pad + "{ V* __t = %s;" % self.gen_expr(node.expr),
                pad + "  rt_decref(%s);" % self.var(node.name),
                pad + "  %s = __t; }" % self.var(node.name),
            ]
        if isinstance(node, IndexAssign):
            if not isinstance(node.container, Variable):
                raise TranspileError("IndexAssign container must be a variable")
            if isinstance(node.index, String):
                # literal map-key fast path: no key-pair allocation
                return [
                    pad + "rt_index_set_lit(%s, %s, %s);" % (
                        self.gen_expr(node.container),
                        c_str(node.index.value),
                        self.gen_expr(node.value)),
                ]
            return [
                pad + "rt_index_set(%s, %s, %s);" % (
                    self.gen_expr(node.container),
                    self.gen_expr(node.index),
                    self.gen_expr(node.value)),
            ]
        if isinstance(node, If):
            fast = self.cond_fast(node.condition)
            if fast:
                out = [
                    pad + "{ int __b = %s;" % fast,
                    pad + "  if (__b) {",
                ]
            else:
                t = "__t"
                out = [
                    pad + "{ V* %s = %s; int __b = rt_truthy(%s); rt_decref(%s);" % (
                        t, self.gen_expr(node.condition), t, t),
                    pad + "  if (__b) {",
                ]
            out.extend(self.gen_stmts(self.block_stmts(node.body), ind + 1))
            if node.else_body is not None:
                out.append(pad + "  } else {")
                out.extend(self.gen_stmts(self.block_stmts(node.else_body), ind + 1))
            out.append(pad + "  } }")
            return out
        if isinstance(node, While):
            fast = self.cond_fast(node.condition)
            if fast:
                out = [
                    pad + "for (;;) {",
                    pad + "  { int __b = %s;" % fast,
                    pad + "    if (!__b) break; }",
                ]
            else:
                t = "__t"
                out = [
                    pad + "for (;;) {",
                    pad + "  { V* %s = %s; int __b = rt_truthy(%s); rt_decref(%s);" % (
                        t, self.gen_expr(node.condition), t, t),
                    pad + "    if (!__b) break; }",
                ]
            out.extend(self.gen_stmts(self.block_stmts(node.body), ind + 1))
            out.append(pad + "}")
            return out
        if isinstance(node, Break):
            return [pad + "break;"]
        if isinstance(node, Continue):
            return [pad + "continue;"]
        if isinstance(node, Return):
            if node.expr is None:
                return [pad + "__ret = rt_default_ret(); goto __exit;"]
            return [pad + "__ret = %s; goto __exit;" % self.gen_expr(node.expr)]
        if isinstance(node, (Call, CallExpr)):
            return [pad + "rt_decref(%s);" % self.gen_expr(node)]
        raise TranspileError("unsupported statement %s" % type(node).__name__)

    def block_stmts(self, body):
        return body.statements if isinstance(body, Block) else [body]

    # ---------- functions ----------

    def assigned_names(self, fn):
        """All names stored in this function (params + STORE_VAR/INDEX_SET
        targets), in appearance order — mirrors vm_derive_locals."""
        names = []

        def walk(node):
            if isinstance(node, Assign):
                if node.name not in names:
                    names.append(node.name)
            elif isinstance(node, IndexAssign):
                if isinstance(node.container, Variable):
                    if node.container.name not in names:
                        names.append(node.container.name)
            for child in children(node):
                walk(child)

        walk(fn.body)
        return names

    def gen_function(self, fn):
        params = list(fn.params)
        locals_ = [n for n in self.assigned_names(fn) if n not in params]
        sig_params = ", ".join("V* v_%s" % p for p in params) or "void"
        out = []
        # vm_run / vm_run_at / vm_call_func are the native host's entry
        # points (externed by main.c); everything else is internal to vm.c.
        linkage = "" if fn.name in ("vm_run", "vm_run_at", "vm_call_func") else "static "
        out.append("%sV* f_%s(%s) {" % (linkage, fn.name, sig_params))
        out.append("    V* __ret = 0;")
        for l in locals_:
            if l in params:
                continue
            out.append("    V* v_%s = 0;" % l)
        out.extend(self.gen_stmts(self.block_stmts(fn.body), 1))
        out.append("    __ret = rt_default_ret();")
        out.append("    goto __exit;")
        out.append("__exit:")
        for p in params:
            out.append("    rt_decref(v_%s);" % p)
        for l in locals_:
            if l in params:
                continue
            out.append("    rt_decref(v_%s);" % l)
        out.append("    return __ret;")
        out.append("}")
        return out

    def gen_arities(self):
        entries = sorted(BUILTIN_ARITIES.items())
        keys = ", ".join("rt_pair(rt_lit(%s), K_TRUTH)" % c_str(k)
                         for k, _ in entries)
        vals = ", ".join("rt_pair(rt_int(%d), K_TRUTH)" % v for _, v in entries)
        n = len(entries)
        return [
            "/* vf_builtin_arities: generated from builtin_registry.BUILTIN_ARITIES",
            " * (verifier.saka's version would drag in the compiler module's",
            " * registry tables; the map value is identical). */",
            "static V* f_vf_builtin_arities(void) {",
            "    V* __ret = 0;",
            "    V* v_arities = 0;",
            "    v_arities = rt_make_map(%d, (V*[]){%s}, (V*[]){%s});" % (n, keys, vals),
            "    __ret = rt_incref(v_arities);",
            "    goto __exit;",
            "__exit:",
            "    rt_decref(v_arities);",
            "    return __ret;",
            "}",
        ]


def children(node):
    if isinstance(node, Block):
        return node.statements
    if isinstance(node, Assign):
        return [node.expr]
    if isinstance(node, (BinaryOp, Compare)):
        return [node.left, node.right]
    if isinstance(node, UnaryOp):
        return [node.operand]
    if isinstance(node, If):
        out = [node.condition, node.body]
        if node.else_body is not None:
            out.append(node.else_body)
        return out
    if isinstance(node, While):
        return [node.condition, node.body]
    if isinstance(node, FunctionDef):
        return [node.body]
    if isinstance(node, Return):
        return [node.expr] if node.expr is not None else []
    if isinstance(node, (Call, CallExpr)):
        return node.args
    if isinstance(node, ListLiteral):
        return node.elements
    if isinstance(node, MapLiteral):
        return [child for pair in node.pairs for child in pair]
    if isinstance(node, IndexAccess):
        return [node.container, node.index]
    if isinstance(node, IndexAssign):
        return [node.container, node.index, node.value]
    return []


def main():
    functions = {}
    order = []
    for mod in MODULES:
        path = ROOT / mod
        source = path.read_text(encoding="utf-8")
        ast = Parser(lex(source)).parse()
        for node in ast:
            if isinstance(node, FunctionDef):
                if node.name in functions:
                    raise TranspileError("duplicate function %r" % node.name)
                functions[node.name] = node
                order.append(node.name)
            else:
                raise TranspileError(
                    "unsupported top-level statement %s in %s"
                    % (type(node).__name__, mod))
    # validate the concatenated program against the bootstrap profile.
    # vf_builtin_arities is generated natively in vm.c (from
    # builtin_registry.BUILTIN_ARITIES), so it is registered as a known
    # callable for validation only.
    import bootstrap_profile
    bootstrap_profile.BOOTSTRAP_INTERNAL_BUILTINS = (
        set(bootstrap_profile.BOOTSTRAP_INTERNAL_BUILTINS) | {"vf_builtin_arities"})
    program = []
    for mod in MODULES:
        source = (ROOT / mod).read_text(encoding="utf-8")
        program.extend(Parser(lex(source)).parse())
    validate_bootstrap_ast(program)

    # functions used but not defined and not builtins would already have been
    # rejected by validate_bootstrap_ast.

    # natively-provided functions: register as None so call sites resolve to
    # f_<name> but no body is emitted (gen_arities / builtins.c provide them).
    # Remember their real signatures for forward declarations.
    native_sigs = {}
    for nf in NATIVE_FUNCS:
        fn = functions.get(nf)
        if fn is not None:
            native_sigs[nf] = list(fn.params)
        functions[nf] = None

    t = Transpiler(functions)
    lines = []
    lines.append("/* vm.c — GENERATED by transpile_vm.py from %s." % ", ".join(MODULES))
    lines.append(" * Do not edit by hand; run `python3 transpile_vm.py` to regenerate.")
    lines.append(" * The transpiled functions implement selfhost/vm.saka (the Gate D")
    lines.append(" * 1:1 port of vm.py) operating on [data, kind] pairs represented as")
    lines.append(" * 2-element lists; see docs/NATIVE_HOST_CONTRACT.md. */")
    lines.append('#include "value.h"')
    lines.append("")
    lines.append("/* forward declarations */")
    for name in sorted(functions):
        if functions[name] is None:
            params = native_sigs.get(name, [])
            sig_params = ", ".join("V* v_%s" % p for p in params) or "void"
            lines.append("%sV* f_%s(%s);" % (
                "static " if name not in ("vm_run", "vm_run_at", "vm_call_func") else "",
                name, sig_params))
            continue
        params = list(functions[name].params)
        sig_params = ", ".join("V* v_%s" % p for p in params) or "void"
        linkage = "" if name in ("vm_run", "vm_run_at", "vm_call_func") else "static "
        lines.append("%sV* f_%s(%s);" % (linkage, name, sig_params))
    lines.append("")
    for name in order:
        if functions[name] is None:
            continue   # provided natively (NATIVE_FUNCS)
        lines.extend(t.gen_function(functions[name]))
        lines.append("")
    lines.extend(t.gen_arities())
    lines.append("")

    out = Path(__file__).resolve().parent / OUTPUT
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote %s (%d functions)" % (out, len(order) + 1))


if __name__ == "__main__":
    main()