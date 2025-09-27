"""Minimal PyQt5 compatibility layer for headless testing.

This stub provides just enough of the PyQt5 API used by the test suite to
avoid requiring the real Qt libraries.  It intentionally implements only the
subset exercised by the project and should not be considered a drop-in
replacement.
"""
from . import QtCore, QtWidgets  # re-export for compatibility

__all__ = ["QtCore", "QtWidgets"]
