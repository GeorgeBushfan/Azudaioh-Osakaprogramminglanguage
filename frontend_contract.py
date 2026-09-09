"""Implementation-neutral frontend records for bootstrap comparisons."""

from ast_nodes import (
    Assign, BinaryOp, Block, Break, Call, CallExpr, Compare, Continue,
    Declaration, Export, FunctionDef, If, Import, IndexAccess, IndexAssign,
    ListLiteral, MapLiteral, Number, Return, String, TryCatch, UnaryOp,
    Variable, While,
)
from lexer import KEYWORDS, TOKEN_RE, decode_string_literal


class FrontendDiagnostic(SyntaxError):
    """Stable diagnostic record that remains compatible with SyntaxError."""

    def __init__(self, phase, code, message, source_span, found=None, expected=None):
        super().__init__(message)
        self.record = diagnostic_record(phase, code, source_span, found, expected)


def position(offset, line, column):
    return {"column": column, "line": line, "offset": offset}


def span(start_offset, start_line, start_column, end_offset, end_line, end_column):
    return {
        "end": position(end_offset, end_line, end_column),
        "start": position(start_offset, start_line, start_column),
    }


def diagnostic_record(phase, code, source_span, found=None, expected=None):
    return {
        "code": code,
        "expected": expected,
        "found": found,
        "phase": phase,
        "span": source_span,
    }


def canonical_tokens(source):
    """Scan source to decoded records with zero-based offsets and 1-based lines."""
    records = []
    offset = 0
    line = 1
    column = 1
    while offset < len(source):
        match = TOKEN_RE.match(source, offset)
        if match is None:
            current = span(offset, line, column, offset + 1, line, column + 1)
            code = "LEX_UNTERMINATED_STRING" if source[offset] == '"' else "LEX_UNEXPECTED_CHAR"
            raise FrontendDiagnostic("lexer", code, code, current, source[offset], None)

        kind = match.lastgroup
        lexeme = match.group()
        start_offset, start_line, start_column = offset, line, column
        offset = match.end()
        if kind == "NEWLINE":
            line += 1
            column = 1
            continue
        column += len(lexeme)
        if kind in {"SKIP", "COMMENT"}:
            continue

        source_span = span(start_offset, start_line, start_column, offset, line, column)
        if kind == "IDENT" and lexeme in KEYWORDS:
            kind = lexeme.upper()
        value = lexeme
        if kind == "STRING":
            try:
                value = decode_string_literal(lexeme, start_line, start_column)
            except SyntaxError as error:
                raise FrontendDiagnostic(
                    "lexer", "LEX_UNSUPPORTED_ESCAPE", str(error), source_span, lexeme, None
                ) from error
        elif kind == "NUMBER":
            value = int(lexeme)
        elif kind == "FLOAT":
            value = float(lexeme)
        records.append({"kind": kind, "lexeme": lexeme, "span": source_span, "value": value})

    eof_span = span(offset, line, column, offset, line, column)
    records.append({"kind": "EOF", "lexeme": "", "span": eof_span, "value": None})
    return records


def _line_start_offsets(source):
    starts = [0]
    index = 0
    while index < len(source):
        if source[index] == "\r":
            if index + 1 < len(source) and source[index + 1] == "\n":
                index += 1
            starts.append(index + 1)
        elif source[index] == "\n":
            starts.append(index + 1)
        index += 1
    return starts


def canonical_diagnostic(error, source, phase="parser"):
    """Normalize Stage 0 frontend failures to the bootstrap diagnostic schema."""
    if isinstance(error, FrontendDiagnostic):
        return error.record

    message = str(error)
    tokens = canonical_tokens(source)
    token_index = None
    marker = " at position "
    if marker in message:
        tail = message.split(marker, 1)[1]
        digits = ""
        for char in tail:
            if not char.isdigit():
                break
            digits += char
        if digits:
            token_index = int(digits)
    if token_index is not None and 0 <= token_index < len(tokens):
        source_span = tokens[token_index]["span"]
        found = tokens[token_index]["kind"]
    else:
        source_span = tokens[-1]["span"]
        found = "EOF"

    expected = None
    code = "PARSE_UNEXPECTED_TOKEN"
    if message.startswith("Unexpected end of input"):
        code = "PARSE_UNEXPECTED_EOF"
    elif message.startswith("Expected "):
        code = "PARSE_EXPECTED_TOKEN"
        expected = message[len("Expected "):].split(",", 1)[0].split(" ", 1)[0].strip("'\"")
    elif "Map keys must be string literals" in message:
        code, expected = "PARSE_EXPECTED_TOKEN", "STRING"
    return diagnostic_record(phase, code, source_span, found, expected)


def _line(node):
    return getattr(node, "line", -1)


def canonical_ast(node):
    """Convert Stage 0 AST classes to stable tagged maps and ordered lists."""
    if isinstance(node, list):
        return [canonical_ast(item) for item in node]
    if isinstance(node, Block):
        return {"line": _line(node), "statements": canonical_ast(node.statements), "tag": "block"}
    if isinstance(node, Number):
        return {"line": _line(node), "tag": "number", "value": node.value}
    if isinstance(node, String):
        return {"line": _line(node), "tag": "string", "value": node.value}
    if isinstance(node, Variable):
        return {"line": _line(node), "name": node.name, "tag": "variable"}
    if isinstance(node, Assign):
        return {"decl_kind": node.decl_type, "expr": canonical_ast(node.expr), "line": _line(node),
                "name": node.name, "tag": "assign"}
    if isinstance(node, BinaryOp):
        return {"left": canonical_ast(node.left), "line": _line(node), "op": node.op,
                "right": canonical_ast(node.right), "tag": "binary"}
    if isinstance(node, UnaryOp):
        return {"line": _line(node), "op": node.op, "operand": canonical_ast(node.operand), "tag": "unary"}
    if isinstance(node, Compare):
        return {"left": canonical_ast(node.left), "line": _line(node), "op": node.op,
                "right": canonical_ast(node.right), "tag": "compare"}
    if isinstance(node, ListLiteral):
        return {"elements": canonical_ast(node.elements), "line": _line(node), "tag": "list"}
    if isinstance(node, MapLiteral):
        return {"line": _line(node), "pairs": [[canonical_ast(k), canonical_ast(v)] for k, v in node.pairs],
                "tag": "map"}
    if isinstance(node, IndexAccess):
        return {"container": canonical_ast(node.container), "index": canonical_ast(node.index),
                "line": _line(node), "tag": "index_get"}
    if isinstance(node, IndexAssign):
        return {"container": canonical_ast(node.container), "index": canonical_ast(node.index),
                "line": _line(node), "tag": "index_set", "value": canonical_ast(node.value)}
    if isinstance(node, Call):
        return {"args": canonical_ast(node.args), "line": _line(node), "name": node.name, "tag": "call_stmt"}
    if isinstance(node, CallExpr):
        return {"args": canonical_ast(node.args), "line": _line(node), "name": node.name, "tag": "call_expr"}
    if isinstance(node, If):
        return {"condition": canonical_ast(node.condition), "else": canonical_ast(node.else_body),
                "line": _line(node), "tag": "if", "then": canonical_ast(node.body)}
    if isinstance(node, While):
        return {"body": canonical_ast(node.body), "condition": canonical_ast(node.condition),
                "line": _line(node), "tag": "while"}
    if isinstance(node, TryCatch):
        return {"catch": canonical_ast(node.catch_body), "line": _line(node), "tag": "try_catch",
                "try": canonical_ast(node.try_body)}
    if isinstance(node, FunctionDef):
        return {"body": canonical_ast(node.body), "line": _line(node), "name": node.name,
                "params": list(node.params), "tag": "function"}
    if isinstance(node, Return):
        return {"expr": canonical_ast(node.expr), "line": _line(node), "tag": "return"}
    if isinstance(node, Break):
        return {"line": _line(node), "tag": "break"}
    if isinstance(node, Continue):
        return {"line": _line(node), "tag": "continue"}
    if isinstance(node, Import):
        return {"alias": node.alias, "is_path": node.is_path, "line": _line(node),
                "module": node.module, "tag": "import"}
    if isinstance(node, Export):
        return {"line": _line(node), "node": canonical_ast(node.node), "tag": "export"}
    if isinstance(node, Declaration):
        return {"decl_type": node.decl_type, "line": _line(node), "name": node.name, "tag": "declaration"}
    if node is None:
        return None
    raise TypeError(f"No canonical AST mapping for {type(node).__name__}")