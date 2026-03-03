
import time
import os

class Runtime:
    def __init__(self):
        import io
        self.scopes = [{}]          # stack of scopes
        self.var_kinds = {}          # name -> 'grain' | 'truth' | None
        self.acknowledged = set()    # names passed to Ah()
        self.completed = set()       # names passed to Hecho()
        self.has_unresolved_grain = False
        self.context_level = 0
        self.shame = 0               # now just "warnings count"
        self.functions = {}
        self.assumed = set()   # variables that suppress "mutated without Ah()"
        self.legacy = set()    # variables that cannot be mutated
        self.initialised = set()
        self.mutation_warned = set()
        self.errors = []         # Track runtime errors
        self.warnings = []       # Track warnings
        self.stdout = io.StringIO()  # Capture stdout
        self.stderr = io.StringIO()  # Capture stderr
        self.execution_traces = []   # For equivalence lock tracing
        self.imports = set()
        self.module_cache = {}
        self.module_loading = set()
        self.current_file = None
        self.collecting_exports = False
        self.current_module_exports = None
        self._math_seed = 123456789
        
    def print(self, *args, **kwargs):
        output = " ".join(str(arg) for arg in args)
        print(output, file=self.stdout, **kwargs)




    def warn(self, msg):
        warning = f"[Warning] {msg}"
        print(warning)
        self.warnings.append(warning)
        self.shame += 1

    def info(self, msg):
        print(f"[Info] {msg}")


    def push_scope(self):
        self.scopes.append({})

    def pop_scope(self):
        self.scopes.pop()

    def set_var(self, name, value):
        # Search outer scopes for existing variable
        for scope in reversed(self.scopes):
            if name in scope:
                scope[name] = value
                return
        # If not found, create in current scope
        self.scopes[-1][name] = value

    def get_var(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise RuntimeError(f"{name} not defined")

    def resolve_module_path(self, module_path: str) -> str:
        if os.path.isabs(module_path):
            return os.path.normpath(module_path)
        base = os.path.dirname(self.current_file) if self.current_file else os.getcwd()
        return os.path.normpath(os.path.join(base, module_path))
        
    def capture_trace(self, line_num, function_name=None, expression=None):
        """Capture interpreter execution state with enhanced context"""
        # Flatten scopes to capture current variable values
        variables = {}
        for scope in self.scopes:
            variables.update(scope)
            
        # Get call stack
        call_stack = []
        for frame in self.scopes:
            if 'function_name' in frame:
                call_stack.append(frame['function_name'])
                
        trace = {
            "line": line_num,
            "function": function_name or "global",
            "call_stack": call_stack.copy(),
            "expression": expression,
            "variables": variables.copy(),
            "warnings": list(self.warnings),
            "errors": [],  # We don't have an errors list yet
            "stdout": self.stdout.getvalue(),
            "stderr": self.stderr.getvalue(),
            "timestamp": time.time()  # For ordering
        }
        self.execution_traces.append(trace)
        
    def clear_traces(self):
        """Clear collected traces"""
        self.execution_traces = []
        
    def format_trace(self, trace):
        """Format a trace for human-readable output"""
        output = f"Line {trace['line']} in {trace['function']}\n"
        output += f"Call stack: {' -> '.join(trace['call_stack'])}\n"
        
        if trace['expression']:
            output += f"Expression: {trace['expression']}\n"
            
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
