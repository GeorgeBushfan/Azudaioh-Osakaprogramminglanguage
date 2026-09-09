"""Static checks for the Osaka subset used to build the self-hosted compiler."""

from ast_nodes import (
    Assign, BinaryOp, Block, Break, Call, CallExpr, Compare, Continue,
    FunctionDef, If, IndexAccess, IndexAssign, ListLiteral, MapLiteral, Number,
    Return, String, UnaryOp, Variable, While,
)
from builtin_registry import BOOTSTRAP_INTERNAL_BUILTINS, BOOTSTRAP_PUBLIC_BUILTINS


ALLOWED_NODE_TYPES = (
    Assign, BinaryOp, Block, Break, Call, CallExpr, Compare, Continue,
    FunctionDef, If, IndexAccess, IndexAssign, ListLiteral, MapLiteral, Number,
    Return, String, UnaryOp, Variable, While,
)


def _children(node):
    if isinstance(node, list):
        return node
    if isinstance(node, (Number, String, Variable, Break, Continue)):
        return []
    if isinstance(node, Assign):
        return [node.expr]
    if isinstance(node, BinaryOp):
        return [node.left, node.right]
    if isinstance(node, UnaryOp):
        return [node.operand]
    if isinstance(node, Compare):
        return [node.left, node.right]
    if isinstance(node, Block):
        return node.statements
    if isinstance(node, If):
        return [node.condition, node.body, node.else_body]
    if isinstance(node, While):
        return [node.condition, node.body]
    if isinstance(node, FunctionDef):
        return [node.body]
    if isinstance(node, Return):
        return [node.expr]
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


def validate_bootstrap_ast(program):
    """Reject source features outside the first self-hosting profile."""
    functions = {node.name for node in program if isinstance(node, FunctionDef)}
    stack = list(program)
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if not isinstance(node, ALLOWED_NODE_TYPES):
            raise RuntimeError(
                f"Bootstrap profile does not allow {type(node).__name__} at line {getattr(node, 'line', -1)}"
            )
        if isinstance(node, (Call, CallExpr)):
            if "." in node.name:
                raise RuntimeError(f"Bootstrap profile does not allow namespaced call {node.name}")
            if (node.name not in BOOTSTRAP_PUBLIC_BUILTINS
                    and node.name not in BOOTSTRAP_INTERNAL_BUILTINS
                    and node.name not in functions):
                raise RuntimeError(f"Bootstrap profile call is unresolved: {node.name}")
        stack.extend(_children(node))
    return True