"""Lightweight signal helper used by the Flet UI and tests."""
from __future__ import annotations

from typing import Callable, Generic, Iterable, List, TypeVar

T = TypeVar("T")


class Signal(Generic[T]):
    """Small Qt-like signal implementation.

    The implementation intentionally mirrors the minimal subset of the
    ``pyqtSignal`` API that the project relied on.  Subscribers are stored as a
    simple list of callables and invoked synchronously when ``emit`` is called.
    ``connect`` ignores duplicate registrations to keep behaviour predictable in
    tests.
    """

    def __init__(self) -> None:
        self._subscribers: List[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        """Register *callback* to be invoked when the signal emits."""

        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def emit(self, *args, **kwargs) -> None:
        """Invoke every subscribed callback with ``*args`` and ``**kwargs``."""

        for subscriber in list(self._subscribers):
            subscriber(*args, **kwargs)

    def subscribers(self) -> Iterable[Callable[..., None]]:
        """Return the registered callbacks (useful for testing)."""

        return tuple(self._subscribers)


__all__ = ["Signal"]
