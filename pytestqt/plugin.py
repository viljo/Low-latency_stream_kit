"""Stub pytest plugin providing a ``qtbot`` fixture without Qt bindings."""
from __future__ import annotations

from typing import Any

import pytest


class _QtBot:
    def __init__(self) -> None:
        self._widgets: list[Any] = []

    def addWidget(self, widget: Any) -> None:
        self._widgets.append(widget)

    def mouseClick(self, widget: Any, _button: Any) -> None:
        click = getattr(widget, "click", None)
        if callable(click):
            click()
            return
        pressed = getattr(widget, "sliderPressed", None)
        released = getattr(widget, "sliderReleased", None)
        if callable(getattr(pressed, "emit", None)):
            pressed.emit()
        if callable(getattr(released, "emit", None)):
            released.emit()


@pytest.fixture
def qtbot() -> _QtBot:
    return _QtBot()


def pytest_configure(config: pytest.Config) -> None:  # pragma: no cover - compatibility
    config.addinivalue_line(
        "markers", "qt_no_exception_capture: marker ignored by the stub plugin"
    )
