"""Tiny subset of :mod:`PyQt5.QtCore` implemented in Python for tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


PYQT_VERSION = 0x050F00
PYQT_VERSION_STR = "5.15.0"
QT_VERSION_STR = "5.15.0"


_message_handler: Optional[Callable[..., Any]] = None


def qVersion() -> str:  # pragma: no cover - trivial helper
    return QT_VERSION_STR


def _log(message: str) -> None:  # pragma: no cover - debug helper
    if _message_handler is not None:
        _message_handler(None, None, message)


def qDebug(message: str) -> None:  # pragma: no cover - debug helper
    _log(message)


def qWarning(message: str) -> None:  # pragma: no cover - debug helper
    _log(message)


def qCritical(message: str) -> None:  # pragma: no cover - debug helper
    _log(message)


def qFatal(message: str) -> None:  # pragma: no cover - debug helper
    _log(message)


def qInfo(message: str) -> None:  # pragma: no cover - debug helper
    _log(message)


def qInstallMessageHandler(handler: Optional[Callable[..., Any]]) -> Optional[Callable[..., Any]]:
    global _message_handler
    previous = _message_handler
    _message_handler = handler
    return previous


def pyqtSlot(*_args: Any, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func

    return decorator


def pyqtProperty(type_: Any, fget: Callable[..., Any], fset: Optional[Callable[..., Any]] = None) -> property:
    return property(fget, fset)


class QMessageLogger:  # pragma: no cover - minimal stub
    def info(self, message: str) -> None:
        qInfo(message)


class _Signal:
    """Runtime signal implementation with a minimal connect/emit API."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[..., Any]] = []

    def connect(self, callback: Callable[..., Any]) -> None:
        self._callbacks.append(callback)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _SignalDescriptor:
    """Descriptor backing :func:`pyqtSignal` declarations."""

    def __init__(self) -> None:
        self._name: Optional[str] = None

    def __set_name__(self, owner: type, name: str) -> None:  # pragma: no cover - trivial
        self._name = name

    def __get__(self, instance: Any, owner: type | None = None) -> _Signal:
        if instance is None:
            return self  # pragma: no cover - class access not used
        assert self._name is not None
        signal = instance.__dict__.get(self._name)
        if signal is None:
            signal = _Signal()
            instance.__dict__[self._name] = signal
        return signal


def pyqtSignal(*_args: Any, **_kwargs: Any) -> _SignalDescriptor:
    """Return a descriptor emulating :func:`PyQt5.QtCore.pyqtSignal`."""

    return _SignalDescriptor()


class QObject:
    """Base class matching the PyQt5 behaviour relevant for tests."""

    def __init__(self, parent: Optional["QObject"] = None) -> None:
        self._parent = parent

    def deleteLater(self) -> None:  # pragma: no cover - lifecycle helper
        pass


class QTimer(QObject):
    """Simplified timer storing state without real scheduling."""

    def __init__(self) -> None:
        super().__init__(None)
        self._interval_ms = 0
        self._active = False
        self.timeout = _Signal()

    def setInterval(self, interval_ms: int) -> None:
        self._interval_ms = int(interval_ms)

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    @property
    def interval(self) -> int:
        return self._interval_ms

    @property
    def isActive(self) -> bool:  # pragma: no cover - unused but provided for parity
        return self._active


@dataclass
class _QtNamespace:
    Horizontal: int = 1
    LeftButton: int = 1


Qt = _QtNamespace()

__all__ = [
    "QObject",
    "QTimer",
    "Qt",
    "pyqtSignal",
    "pyqtSlot",
    "pyqtProperty",
    "PYQT_VERSION",
    "PYQT_VERSION_STR",
    "QT_VERSION_STR",
    "qVersion",
    "qDebug",
    "qWarning",
    "qCritical",
    "qFatal",
    "qInfo",
    "qInstallMessageHandler",
    "QMessageLogger",
]
