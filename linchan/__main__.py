import sys

from .app import main


def cli():
    """Entry point for the `linchan` command (see pyproject.toml)."""
    sys.exit(main(sys.argv))


if __name__ == "__main__":
    cli()
