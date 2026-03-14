"""BHPAN CLI Entry Point."""

import sys
from .shell import PanShell

def cli() -> None:
    try:
        PanShell().run()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    cli()
