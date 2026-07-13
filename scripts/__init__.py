"""MAS CLI 统一包。"""

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from scripts.mas import app

        return app
    raise AttributeError(name)
