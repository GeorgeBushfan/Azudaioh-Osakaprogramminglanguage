"""Deterministically flatten tracked Osaka modules for bootstrap compilation."""

from pathlib import Path

from ast_nodes import Export, FunctionDef, Import
from lexer import lex
from parser import Parser


DEFAULT_ORDER = (
    "support.saka",
    "diagnostic.saka",
    "token.saka",
    "ast.saka",
    "lexer.saka",
    "parser.saka",
    "lower.saka",
    "bytecode.saka",
    "compiler.saka",
    "verifier.saka",
    "emit_sbc.saka",
    "main.saka",
)


def _load_module(path):
    return Parser(lex(path.read_text(encoding="utf-8"))).parse()


def bundle_ast(root="selfhost", order=DEFAULT_ORDER):
    root = Path(root)
    combined = []
    seen_functions = set()
    for relative in order:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"Missing bootstrap module: {path.as_posix()}")
        for node in _load_module(path):
            if isinstance(node, Import):
                continue
            node = node.node if isinstance(node, Export) else node
            if isinstance(node, FunctionDef):
                if node.name in seen_functions:
                    raise RuntimeError(f"Duplicate bundled function: {node.name}")
                seen_functions.add(node.name)
            combined.append(node)
    return combined


def source_manifest(root="selfhost", order=DEFAULT_ORDER):
    root = Path(root)
    return [path.as_posix() for path in (root / name for name in order)]