# lexer.py

import re

TOKEN_SPEC = [
    ("COMMENT",  r"//[^\r\n]*"),
    ("FLOAT",    r"\d+\.\d+"),
    ("NUMBER",   r"\d+"),
    ("IDENT",    r"[A-Za-z_][A-Za-z0-9_]*"),
    ("LPAREN",   r"\("),
    ("RPAREN",   r"\)"),
    ("LBRACE",   r"\{"),
    ("RBRACE",   r"\}"),
    ("NEQ",      r"!="),
    ("LE",       r"<="),
    ("GE",       r">="),
    ("EQEQ",     r"=="),
    ("STAR",     r"\*"),
    ("PERCENT",  r"%"),
    ("MINUS",    r"-"),
    ("PLUS",     r"\+"),
    ("SLASH",    r"/"),
    ("EQUAL",    r"="),
    ("SEMICOL",  r";"),
    ("LT",       r"<"),
    ("GT",       r">"),
    ("SKIP",     r"[ \t]+"),
    ("NEWLINE",  r"\r\n|\r|\n"),
    ("STRING", r'"(?:\\.|[^"\\\r\n])*"'),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("COMMA", r","),
    ("COLON", r":"),
    ("DOT", r"\."),

]

TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_SPEC))

KEYWORDS = {
    "Ah",
    "Hecho",
    "Ohmygah",
    "grainsoftruth",
    "truthaboutgrain",
    "Getittogether",
    "Escalator",
    "Elevator",
    "if",
    "for",
    "while",
    "break",
    "continue",
    "import",
    "as",
    "export",
    "else",
    "and",
    "or",
    "not",
    "try",
    "catch",
    "function", 
    "func",
    "return",
    "youknowsealsright", 
    "Ivebeengot",
}

def decode_string_literal(lexeme, line=-1, column=-1):
    """Decode one quoted Osaka string and reject unsupported escapes."""
    if len(lexeme) < 2 or lexeme[0] != '"' or lexeme[-1] != '"':
        raise SyntaxError(f"Unterminated string at line {line}, column {column}")

    escapes = {
        '"': '"',
        "\\": "\\",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    result = []
    index = 1
    while index < len(lexeme) - 1:
        char = lexeme[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue

        index += 1
        escaped = lexeme[index]
        if escaped not in escapes:
            raise SyntaxError(
                f"Unsupported escape \\{escaped} at line {line}, column {column + index}"
            )
        result.append(escapes[escaped])
        index += 1

    return "".join(result)


def lex(code, show_tokens=False):
    tokens = []
    line_num = 1
    column = 1
    position = 0

    while position < len(code):
        match = TOKEN_RE.match(code, position)
        if match is None:
            if code[position] == '"':
                raise SyntaxError(f"Unterminated string at line {line_num}, column {column}")
            bad = code[position]
            raise SyntaxError(
                f"Unexpected character {bad!r} at line {line_num}, column {column}"
            )

        kind = match.lastgroup
        value = match.group()
        if kind == "NEWLINE":
            line_num += 1
            column = 1
        else:
            token_column = column
            column += len(value)
            if kind not in ("SKIP", "COMMENT"):
                if kind == "IDENT" and value in KEYWORDS:
                    kind = value.upper()
                if kind == "STRING":
                    decode_string_literal(value, line_num, token_column)
                tokens.append((kind, value, line_num))
                if show_tokens:
                    print(
                        f"LEXER TOKEN: kind={kind}, value={value!r}, "
                        f"line={line_num}, column={token_column}"
                    )

        position = match.end()

    return tokens
