"""`python -m kb_sync` entry point: run the CLI and exit with its code (contracts/kb-cli.md)."""

import sys

from kb_sync import cli

if __name__ == "__main__":
    sys.exit(cli.main())
