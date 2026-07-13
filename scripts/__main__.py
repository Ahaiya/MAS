"""统一 MAS CLI 的包入口。

推荐调用方式：
  python -m scripts ...
"""

from scripts.mas import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
