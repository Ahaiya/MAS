"""Package entrypoint for the unified MAS CLI.

Recommended invocation:
  python -m scripts ...
"""

from scripts.mas import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
