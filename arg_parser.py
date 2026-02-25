import argparse

def create_arg_parser():
    parser = argparse.ArgumentParser(
        prog='saka',
        description='Osaka Lang interpreter and virtual machine',
        epilog='Equivalence Lock ensures consistent behavior between interpreter and VM'
    )
    
    parser.add_argument(
        'source_file',
        nargs='?',
        help='Source file to execute (.saka extension)'
    )

    parser.add_argument(
        '--repl',
        action='store_true',
        help='Start interactive REPL mode'
    )
    
    parser.add_argument(
        '--no-lock',
        action='store_true',
        help='Disable equivalence lock verification'
    )
    
    parser.add_argument(
        '--vm-only',
        action='store_true',
        help='Execute using only the virtual machine'
    )
    
    parser.add_argument(
        '--interpreter-only',
        action='store_true',
        help='Execute using only the AST interpreter'
    )
    
    parser.add_argument(
        '--lock-strict',
        action='store_true',
        help='Enable strict equivalence lock mode (errors on mismatch)'
    )
    
    parser.add_argument(
        '--warnings-as-errors',
        action='store_true',
        help='Treat warnings as errors in equivalence checks'
    )
    
    # Add debug flag
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode for detailed execution traces'
    )

    parser.add_argument(
        '--show-lexer-tokens',
        action='store_true',
        help='Show lexer token stream output'
    )
    
    return parser
