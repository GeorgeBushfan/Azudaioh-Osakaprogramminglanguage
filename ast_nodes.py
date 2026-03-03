# ast_nodes.py

class Node:
    def __init__(self, line=-1):
        self.line = line

class Block(Node):
    def __init__(self, statements):
        self.statements = statements


class MapLiteral(Node):
    def __init__(self, pairs, line=-1):
        super().__init__(line)
        self.pairs = pairs  # list of (key_node, value_node)

class IndexAssign(Node):
    def __init__(self, container, index, value):
        self.container = container
        self.index = index
        self.value = value


class If(Node):
    def __init__(self, condition, body, else_body=None, line=-1):
        super().__init__(line)
        self.condition = condition
        self.body = body
        self.else_body = else_body

class While(Node):
    def __init__(self, condition, body, line=-1):
        super().__init__(line)
        self.condition = condition
        self.body = body

class TryCatch(Node):
    def __init__(self, try_body, catch_body, line=-1):
        super().__init__(line)
        self.try_body = try_body
        self.catch_body = catch_body

class Compare(Node):
    def __init__(self, left, op, right, line=-1):
        super().__init__(line)
        self.left = left
        self.op = op
        self.right = right

class Number(Node):
    def __init__(self, value, line=-1):
        super().__init__(line)
        self.value = value

class Variable(Node):
    def __init__(self, name, line=-1):
        super().__init__(line)
        self.name = name

class Assign(Node):
    def __init__(self, name, expr, decl_type=None, line=-1):
        super().__init__(line)
        self.name = name
        self.expr = expr
        self.decl_type = decl_type   # 'grain' | 'truth' | None

class BinaryOp(Node):
    def __init__(self, left, op, right, line=-1):
        super().__init__(line)
        self.left = left
        self.op = op
        self.right = right

class UnaryOp(Node):
    def __init__(self, op, operand, line=-1):
        super().__init__(line)
        self.op = op
        self.operand = operand

class Call(Node):
    def __init__(self, name, args, line=-1):
        super().__init__(line)
        self.name = name             # e.g. "Ah", "Hecho", "Getittogether"
        self.args = args             # List of argument expressions

class Declaration(Node):
    def __init__(self, decl_type, name):
        self.decl_type = decl_type   # "Escalator" or "Elevator"
        self.name = name

class Block(Node):
    def __init__(self, statements):
        self.statements = statements


class If(Node):
    def __init__(self, condition, body, else_body=None, line=-1):
        super().__init__(line)
        self.condition = condition
        self.body = body
        self.else_body = else_body


class While(Node):
    def __init__(self, condition, body, line=-1):
        super().__init__(line)
        self.condition = condition
        self.body = body


class Compare(Node):
    def __init__(self, left, op, right, line=-1):
        super().__init__(line)
        self.left = left
        self.op = op  # '==', '<', '>'
        self.right = right


class FunctionDef(Node):
    def __init__(self, name, params, body):
        self.name = name
        self.params = params        # list of parameter names
        self.body = body            # Block


class CallExpr(Node):
    def __init__(self, name, args, line=-1):
        super().__init__(line)
        self.name = name
        self.args = args            # list of expressions


class Return(Node):
    def __init__(self, expr):
        self.expr = expr

class String(Node):
    def __init__(self, value, line=-1):
        super().__init__(line)
        self.value = value

class ListLiteral(Node):
    def __init__(self, elements, line=-1):
        super().__init__(line)
        self.elements = elements

class IndexAccess(Node):
    def __init__(self, container, index, line=-1):
        super().__init__(line)
        self.container = container
        self.index = index

class Break(Node):
    def __init__(self, line=-1):
        super().__init__(line)

class Continue(Node):
    def __init__(self, line=-1):
        super().__init__(line)

class Import(Node):
    def __init__(self, module, alias=None, line=-1, is_path=False):
        super().__init__(line)
        self.module = module
        self.alias = alias
        self.is_path = is_path

class Export(Node):
    def __init__(self, node, line=-1):
        super().__init__(line)
        self.node = node
