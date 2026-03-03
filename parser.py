# parser.py
# pyright: reportShadowedImports=false, reportUnusedVariable=false


from ast_nodes import *
import copy

class Parser:
    def __init__(self, tokens, debug=False):
        self.tokens = tokens
        self.pos = 0
        self.debug = debug

    def peek(self):
        if self.pos >= len(self.tokens):
            return ('EOF', '', -1)
        tok = self.tokens[self.pos]
        # Return token as (kind, value, line)
        return (tok[0], tok[1], tok[2] if len(tok) > 2 else -1)

    def consume(self, expected_kind=None):
        if self.pos >= len(self.tokens):
            # Show last 5 tokens when we run out
            last_tokens = self.tokens[max(0, self.pos-5):self.pos] if self.tokens else []
            raise SyntaxError(f"Unexpected end of input at position {self.pos}. Last tokens: {last_tokens}")
        tok = self.tokens[self.pos]
        if expected_kind and tok[0] != expected_kind:
            raise SyntaxError(f"Expected {expected_kind}, got {tok[0]} at position {self.pos}")
        self.pos += 1
        # Return token as (kind, value, line)
        return (tok[0], tok[1], tok[2] if len(tok) > 2 else -1)

    def parse(self):
        statements = []
        while self.pos < len(self.tokens):
            try:
                stmt = self.statement()
                statements.append(stmt)
            except SyntaxError as e:
                # If we're at the end of tokens, break gracefully
                if "Unexpected end of input" in str(e) and self.pos >= len(self.tokens):
                    break
                else:
                    raise
        if self.debug:
            print(f"Parsed {len(statements)} statements")
            for i, stmt in enumerate(statements):
                print(f"Statement {i}: {type(stmt).__name__}")
        return statements
    
    def statement(self):
        token = self.peek()
        kind = token[0]
        value = token[1]

        # Handle object-style calls like Math.abs(...)
        if (
            kind == "IDENT"
            and self.pos + 3 < len(self.tokens)
            and self.tokens[self.pos + 1][0] == "DOT"
            and self.tokens[self.pos + 2][0] == "IDENT"
            and self.tokens[self.pos + 3][0] == "LPAREN"
        ):
            obj_tok = self.consume("IDENT")
            self.consume("DOT")
            meth_tok = self.consume("IDENT")
            line = obj_tok[2] if len(obj_tok) > 2 else -1
            func_name = f"{obj_tok[1]}.{meth_tok[1]}"
            self.consume("LPAREN")
            args = []
            while self.peek()[0] != "RPAREN":
                args.append(self.expression())
                if self.peek()[0] == "COMMA":
                    self.consume("COMMA")
            self.consume("RPAREN")
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Call(func_name, args, line)

        # Handle function definitions first
        if kind in ("FUNCTION", "FUNC"):
            return self.function_def()
        if kind == "EXPORT":
            tok = self.consume("EXPORT")
            inner = self.statement()
            return Export(inner, tok[2] if len(tok) > 2 else -1)
        if kind == "IMPORT":
            tok = self.consume("IMPORT")
            is_path = False
            alias = None
            if self.peek()[0] == "STRING":
                name_tok = self.consume("STRING")
                module_name = name_tok[1].strip('"')
                is_path = True
                if self.peek()[0] == "AS":
                    self.consume("AS")
                    alias = self.consume("IDENT")[1]
                else:
                    # default alias from file stem
                    stem = module_name.rsplit("/", 1)[-1]
                    alias = stem.rsplit(".", 1)[0]
            else:
                name_tok = self.consume("IDENT")
                module_name = name_tok[1]
                if self.peek()[0] == "AS":
                    self.consume("AS")
                    alias = self.consume("IDENT")[1]
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Import(module_name, alias=alias, line=tok[2] if len(tok) > 2 else -1, is_path=is_path)
        if kind == "RETURN":
            return self.return_stmt()
        if kind == "BREAK":
            tok = self.consume("BREAK")
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Break(tok[2] if len(tok) > 2 else -1)
        if kind == "CONTINUE":
            tok = self.consume("CONTINUE")
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Continue(tok[2] if len(tok) > 2 else -1)
        if kind == "TRY":
            return self.try_catch_stmt()

        # Handle index assignment: container[index] = expr;
        if kind == "IDENT":
            save_pos = self.pos
            token = self.consume()
            name = token[1]

            if self.peek()[0] == "LBRACKET":
                self.consume("LBRACKET")
                index_expr = self.expression()
                self.consume("RBRACKET")

                if self.peek()[0] == "EQUAL":
                    self.consume("EQUAL")
                    value_expr = self.expression()
                    if self.peek()[0] == "SEMICOL":
                        self.consume("SEMICOL")
                    return IndexAssign(Variable(name), index_expr, value_expr)

            # rollback if not index assignment
            self.pos = save_pos

        if kind == "IF":
            return self.if_stmt()
        if kind == "FOR":
            return self.for_stmt()
        if kind == "WHILE":
            return self.while_stmt()
        if kind == "LBRACE":
            return self.block()
            
        # Function-style keywords (require parentheses)
        if kind in ("AH", "HECHO", "OHMYGAH","YOUKNOWSEALSRIGHT", "IVEBEENGOT"):
            token = self.consume()
            kw_name = token[1]
            line = token[2]  # Capture line number

            # Handle function call syntax: Keyword(Argument)
            token = self.consume()
            lparen_kind = token[0]
            if lparen_kind != "LPAREN":
                raise SyntaxError(f"Expected '(' after {kw_name}")
            
            # Parse argument expression
            arg = self.expression()
            
            token = self.consume()
            rparen_kind = token[0]
            if rparen_kind != "RPAREN":
                raise SyntaxError(f"Expected ')' after argument in {kw_name}")
            
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Call(kw_name, [arg], line)  # Pass line number

        # Declaration-style keywords (Escalator/Elevator)
        if kind in ("ESCALATOR", "ELEVATOR"):
            token = self.consume()
            kw_name = token[1]
            
            # Parse identifier name
            token = self.consume()
            ident_kind = token[0]
            ident_value = token[1]
            if ident_kind != "IDENT":
                raise SyntaxError(f"Expected identifier after {kw_name}")
            
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Declaration(kw_name, ident_value)

        # Handle Getittogether()
        if kind == "GETITTOGETHER":
            token = self.consume()
            kw_name = token[1]
            line = token[2]  # Capture line number
            self.consume("LPAREN")
            self.consume("RPAREN")
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")
            return Call(kw_name, [], line)  # Pass line number

        # Declarations with grainsoftruth / truthaboutgrain
        if kind in ("GRAINSOFTRUTH", "TRUTHABOUTGRAIN"):
            decl_kind = kind
            self.consume()  # keyword

            token = self.consume()
            name = token[1]
            line = token[2] if len(token) > 2 else -1
            self.consume("EQUAL")
            expr = self.expression()
            if self.peek()[0] == "SEMICOL":
                self.consume("SEMICOL")

            decl_type = "grain" if decl_kind == "GRAINSOFTRUTH" else "truth"
            return Assign(name, expr, decl_type, line=line)

        # Handle regular function calls (e.g., Say(h);)
        if kind == "IDENT":
            next_pos = self.pos + 1
            if next_pos < len(self.tokens) and self.tokens[next_pos][0] == "LPAREN":
                token = self.consume()
                func_name = token[1]
                line = token[2]  # Capture line number
                self.consume("LPAREN")
                args = []
                # Parse arguments
                while self.peek()[0] != "RPAREN":
                    args.append(self.expression())
                    if self.peek()[0] == "COMMA":
                        self.consume("COMMA")
                self.consume("RPAREN")
                if self.peek()[0] == "SEMICOL":
                    self.consume("SEMICOL")
                return Call(func_name, args, line)

        # Plain assignment: x = expr;
        token = self.consume()
        name = token[1]
        line = token[2] if len(token) > 2 else -1
        self.consume("EQUAL")
        expr = self.expression()
        if self.peek()[0] == "SEMICOL":
            self.consume("SEMICOL")
        return Assign(name, expr, line=line)
    
    def expression(self):
        if self.peek()[0] == 'EOF':
            return None

        return self.or_expr()

    def or_expr(self):
        left = self.and_expr()
        while self.pos < len(self.tokens) and self.peek()[0] == "OR":
            op_tok = self.consume("OR")
            right = self.and_expr()
            left = BinaryOp(left, "or", right, line=op_tok[2] if len(op_tok) > 2 else -1)
        return left

    def and_expr(self):
        left = self.not_expr()
        while self.pos < len(self.tokens) and self.peek()[0] == "AND":
            op_tok = self.consume("AND")
            right = self.not_expr()
            left = BinaryOp(left, "and", right, line=op_tok[2] if len(op_tok) > 2 else -1)
        return left

    def not_expr(self):
        if self.pos < len(self.tokens) and self.peek()[0] == "NOT":
            tok = self.consume("NOT")
            return UnaryOp("not", self.not_expr(), line=tok[2] if len(tok) > 2 else -1)
        return self.compare_expr()

    def compare_expr(self):
        left = self.addition()
        if self.pos < len(self.tokens) and self.peek()[0] in ("EQEQ", "NEQ", "LT", "LE", "GT", "GE"):
            op_tok = self.consume()
            op = op_tok[1]
            right = self.addition()
            line = op_tok[2] if len(op_tok) > 2 else -1
            return Compare(left, op, right, line=line)
        return left
    
    def function_def(self):
        # Consume FUNCTION/FUNC token
        if self.peek()[0] in ("FUNCTION", "FUNC"):
            self.consume()
        
        token = self.consume()        # function name (IDENT)
        name = token[1]
        self.consume("LPAREN")

        params = []
        # Parse parameters
        if self.peek()[0] == "IDENT":
            token = self.consume()   # IDENT
            param = token[1]
            params.append(param)

        # Parse additional comma-separated parameters
        while self.peek()[0] == "COMMA":
            self.consume("COMMA")
            if self.peek()[0] == "IDENT":
                token = self.consume()   # IDENT
                param = token[1]
                params.append(param)

        self.consume("RPAREN")
        body = self.block()
        return FunctionDef(name, params, body)
    
    def return_stmt(self):
        self.consume("RETURN")
        expr = self.expression()
        
        # Only consume semicolon if it's present
        if self.peek()[0] == "SEMICOL":
            self.consume("SEMICOL")
            
        return Return(expr)

    def term(self):
        if self.peek()[0] == 'EOF':
            return None
            
        token = self.consume()
        kind, value, line = token[0], token[1], token[2] if len(token) > 2 else -1

        if kind == "LPAREN":
            expr = self.expression()
            self.consume("RPAREN")
            return expr

        if kind == "STRING":
            return String(value.strip('"'), line)
        
        if kind == "LBRACKET":
            elements = []
            # Empty list
            if self.peek()[0] == "RBRACKET":
                self.consume()
                return ListLiteral(elements, line)

            # Parse elements
            while True:
                elements.append(self.expression())
                if self.peek()[0] == "COMMA":
                    self.consume()
                    continue
                break

            self.consume("RBRACKET")
            return ListLiteral(elements, line)
        
        elif kind == "LBRACE":
            pairs = []

            # Empty map
            if self.peek()[0] == "RBRACE":
                self.consume("RBRACE")
                return MapLiteral(pairs, line)
            else:
                while True:
                    # Key must be STRING
                    key_token = self.consume()
                    key_kind, key_val, key_line = key_token[0], key_token[1], key_token[2] if len(key_token) > 2 else -1
                    if key_kind != "STRING":
                        raise SyntaxError("Map keys must be string literals")

                    self.consume("COLON")
                    value_expr = self.expression()
                    pairs.append((String(key_val.strip('"'), key_line), value_expr))

                    if self.peek()[0] == "COMMA":
                        self.consume("COMMA")
                        continue
                    break

                self.consume("RBRACE")
                return MapLiteral(pairs, line)

        if kind == "FLOAT":
            return Number(float(value), line)

        if kind == "NUMBER":
            return Number(int(value), line)

        if kind == "IDENT":
            # Object-style function call: Math.abs(...)
            if (
                self.pos + 2 < len(self.tokens)
                and self.peek()[0] == "DOT"
                and self.tokens[self.pos + 1][0] == "IDENT"
                and self.tokens[self.pos + 2][0] == "LPAREN"
            ):
                self.consume("DOT")
                method_tok = self.consume("IDENT")
                self.consume("LPAREN")
                args = []
                while self.peek()[0] != "RPAREN":
                    args.append(self.expression())
                    if self.peek()[0] == "COMMA":
                        self.consume("COMMA")
                self.consume("RPAREN")
                node = CallExpr(f"{value}.{method_tok[1]}", args, line)
            # Object-style constant/property access: Math.PI, Math.E
            elif (
                self.pos + 1 < len(self.tokens)
                and self.peek()[0] == "DOT"
                and self.tokens[self.pos + 1][0] == "IDENT"
            ):
                self.consume("DOT")
                prop_tok = self.consume("IDENT")
                node = CallExpr(f"{value}.{prop_tok[1]}", [], line)
            # Function call: foo(...)
            elif self.pos < len(self.tokens) and self.peek()[0] == "LPAREN":
                self.consume("LPAREN")
                args = []
                while self.peek()[0] != "RPAREN":
                    args.append(self.expression())
                    if self.peek()[0] == "COMMA":
                        self.consume("COMMA")
                self.consume("RPAREN")
                node = CallExpr(value, args, line)
            else:
                node = Variable(value, line)
        else:
            node = self.simple_term()
        
        # Handle index accesses as postfix operations
        while self.pos < len(self.tokens) and self.peek()[0] == "LBRACKET":
            self.consume("LBRACKET")
            index_expr = self.expression()
            self.consume("RBRACKET")
            node = IndexAccess(node, index_expr, line)
            
        return node
    
    def simple_term(self):
        token = self.consume()
        kind, value, line = token[0], token[1], token[2] if len(token) > 2 else -1
        if kind == "STRING":
            return String(value.strip('"'), line)
        if kind == "FLOAT":
            return Number(float(value), line)
        if kind == "NUMBER":
            return Number(int(value), line)
        if kind == "IDENT":
            # Check for function calls
            if self.pos < len(self.tokens) and self.peek()[0] == "LPAREN":
                self.consume("LPAREN")
                args = []
                while self.peek()[0] != "RPAREN":
                    args.append(self.expression())
                    if self.peek()[0] == "COMMA":
                        self.consume("COMMA")
                self.consume("RPAREN")
                return CallExpr(value, args, line)
            return Variable(value, line)
        raise SyntaxError(f"Unexpected token: {kind}")

    def addition(self):
        left = self.multiplication()
        while self.pos < len(self.tokens) and self.peek()[0] in ("PLUS", "MINUS"):
            op_tok = self.consume()
            op = "+" if op_tok[0] == "PLUS" else "-"
            right = self.multiplication()
            left = BinaryOp(left, op, right)
        return left

    def multiplication(self):
        left = self.term()
        while self.pos < len(self.tokens) and self.peek()[0] in ("STAR", "SLASH", "PERCENT"):
            op_tok = self.consume()
            if op_tok[0] == "STAR":
                op = "*"
            elif op_tok[0] == "SLASH":
                op = "/"
            else:
                op = "%"
            right = self.term()
            left = BinaryOp(left, op, right)
        return left
    
    def block(self):
        self.consume("LBRACE")
        statements = []
        while self.pos < len(self.tokens) and self.peek()[0] != "RBRACE":
            statements.append(self.statement())
        self.consume("RBRACE")
        return Block(statements)

    def if_stmt(self):
        tok = self.consume("IF")
        line = tok[2] if len(tok) > 2 else -1
        # Allow both parenthesized and non-parenthesized conditions
        if self.peek()[0] == "LPAREN":
            self.consume("LPAREN")
            condition = self.expression()
            self.consume("RPAREN")
        else:
            condition = self.expression()
        body = self.block()
        else_body = None
        if self.pos < len(self.tokens) and self.peek()[0] == "ELSE":
            self.consume("ELSE")
            # Support `else if ...` chains as syntactic sugar for nested If.
            if self.pos < len(self.tokens) and self.peek()[0] == "IF":
                else_body = self.if_stmt()
            else:
                else_body = self.block()
        return If(condition, body, else_body, line=line)

    def while_stmt(self):
        tok = self.consume("WHILE")
        line = tok[2] if len(tok) > 2 else -1
        # Allow both parenthesized and non-parenthesized conditions
        if self.peek()[0] == "LPAREN":
            self.consume("LPAREN")
            condition = self.expression()
            self.consume("RPAREN")
        else:
            condition = self.expression()
        body = self.block()
        return While(condition, body, line=line)

    def _for_header_assignment(self):
        tok = self.consume("IDENT")
        name = tok[1]
        line = tok[2] if len(tok) > 2 else -1
        self.consume("EQUAL")
        expr = self.expression()
        return Assign(name, expr, line=line)

    def for_stmt(self):
        tok = self.consume("FOR")
        line = tok[2] if len(tok) > 2 else -1

        self.consume("LPAREN")
        init = self._for_header_assignment()
        self.consume("SEMICOL")
        condition = self.expression()
        self.consume("SEMICOL")
        step = self._for_header_assignment()
        self.consume("RPAREN")

        body = self.block()

        def rewrite_for_continue(stmt, step_stmt):
            # Continue in this for-loop should execute step before continuing.
            # Do not rewrite through nested loops.
            if isinstance(stmt, Continue):
                return Block([copy.deepcopy(step_stmt), Continue(stmt.line)])
            if isinstance(stmt, While):
                return stmt
            if isinstance(stmt, Block):
                return Block([rewrite_for_continue(s, step_stmt) for s in stmt.statements])
            if isinstance(stmt, If):
                new_body = rewrite_for_continue(stmt.body, step_stmt)
                new_else = rewrite_for_continue(stmt.else_body, step_stmt) if stmt.else_body is not None else None
                return If(stmt.condition, new_body, new_else, line=stmt.line)
            if isinstance(stmt, TryCatch):
                return TryCatch(
                    rewrite_for_continue(stmt.try_body, step_stmt),
                    rewrite_for_continue(stmt.catch_body, step_stmt),
                    line=stmt.line,
                )
            return stmt

        rewritten_body = [rewrite_for_continue(s, step) for s in body.statements]

        # Desugar:
        # for (init; cond; step) { body }
        # => { init; while (cond) { body; step; } }
        while_body = Block(rewritten_body + [step])
        return Block([init, While(condition, while_body, line=line)])

    def try_catch_stmt(self):
        tok = self.consume("TRY")
        line = tok[2] if len(tok) > 2 else -1
        try_body = self.block()
        self.consume("CATCH")
        catch_body = self.block()
        return TryCatch(try_body, catch_body, line=line)