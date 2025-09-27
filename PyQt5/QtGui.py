"""Minimal stub of :mod:`PyQt5.QtGui` for headless testing."""
from __future__ import annotations

from typing import Optional


class QGuiApplication:
    def __init__(self, _argv: Optional[list[str]] = None) -> None:  # pragma: no cover
        pass

    def exec_(self) -> int:  # pragma: no cover - unused
        return 0


class QColor:  # pragma: no cover - placeholder for completeness
    def __init__(self, *_args: object) -> None:
        pass


__all__ = ["QGuiApplication", "QColor"]
