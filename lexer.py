# lexer.py

import re

TOKEN_SPEC = [
    ("COMMENT",  r"//[^\n]*"),  # Explicitly match to end of line
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
    ("NEWLINE",  r"\n"),
    ("STRING", r'"[^"]*"'),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("COMMA", r","),
    ("COLON", r":"),
    ("DOT", r"\."),

]

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

def lex(code, show_tokens=False):
    tokens = []
    regex = "|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_SPEC)
    line_num = 1
    for match in re.finditer(regex, code):
        kind = match.lastgroup
        value = match.group()
        if kind == "NEWLINE":
            line_num += 1
            continue
        if kind in ("SKIP", "COMMENT"):
            continue
        if kind == "IDENT" and value in KEYWORDS:
            kind = value.upper()     # e.g. "Ah" -> "AH", "grainsoftruth" -> "GRAINSOFTRUTH"
        tokens.append((kind, value, line_num))
        if show_tokens:
            print(f"LEXER TOKEN: kind={kind}, value='{value}', line={line_num}")
    return tokens
