"""Minimal stand-in for :mod:`PyQt5.QtWidgets` used in the tests."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .QtCore import QObject, Qt, _Signal


class QApplication(QObject):
    _instance: Optional["QApplication"] = None

    def __init__(self, argv: Optional[list[str]] = None) -> None:
        super().__init__(None)
        self._argv = list(argv or [])
        QApplication._instance = self

    @classmethod
    def instance(cls) -> "QApplication":
        if cls._instance is None:
            cls._instance = cls([])
        return cls._instance

    def processEvents(self) -> None:  # pragma: no cover - no event loop
        pass

    def exec_(self) -> int:  # pragma: no cover - unused
        return 0

    def quit(self) -> None:  # pragma: no cover - unused
        pass


class QWidget(QObject):
    def __init__(self, parent: Optional["QWidget"] = None) -> None:
        super().__init__(parent)
        self._layout: Optional["QLayout"] = None
        self._visible = False

    def setLayout(self, layout: "QLayout") -> None:
        self._layout = layout

    def layout(self) -> Optional["QLayout"]:
        return self._layout

    def show(self) -> None:  # pragma: no cover - behaviour unused but provided
        self._visible = True


class QMainWindow(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._central: Optional[QWidget] = None
        self._title = ""

    def setCentralWidget(self, widget: QWidget) -> None:
        self._central = widget

    def centralWidget(self) -> Optional[QWidget]:  # pragma: no cover - unused
        return self._central

    def setWindowTitle(self, title: str) -> None:
        self._title = title


class QLayout(QObject):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[Any] = []

    def addWidget(self, widget: QWidget) -> None:
        self._items.append(widget)

    def addLayout(self, layout: "QLayout") -> None:
        self._items.append(layout)


class QVBoxLayout(QLayout):
    pass


class QHBoxLayout(QLayout):
    pass


class QAbstractItemView(QWidget):
    SingleSelection = 1

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._selection_mode = self.SingleSelection

    def setSelectionMode(self, mode: int) -> None:
        self._selection_mode = mode


class QPushButton(QWidget):
    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text = text
        self.clicked = _Signal()

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:  # pragma: no cover - debugging helper
        return self._text

    def click(self) -> None:
        self.clicked.emit()


class QLineEdit(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._placeholder = ""

    def setPlaceholderText(self, text: str) -> None:
        self._placeholder = text

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


class QDoubleSpinBox(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._min = 0.0
        self._max = 100.0
        self._value = 0.0
        self.valueChanged = _Signal()

    def setRange(self, minimum: float, maximum: float) -> None:
        self._min = float(minimum)
        self._max = float(maximum)
        if self._value < self._min:
            self.setValue(self._min)
        elif self._value > self._max:
            self.setValue(self._max)

    def setValue(self, value: float) -> None:
        clamped = max(self._min, min(self._max, float(value)))
        self._value = clamped
        self.valueChanged.emit(self._value)

    def value(self) -> float:  # pragma: no cover - unused helper
        return self._value


class QComboBox(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[str] = []
        self._current = ""
        self.currentTextChanged = _Signal()

    def addItems(self, items: List[str]) -> None:
        self._items.extend(items)
        if not self._current and self._items:
            self.setCurrentText(self._items[0])

    def setCurrentText(self, text: str) -> None:
        if text not in self._items:
            self._items.append(text)
        self._current = text
        self.currentTextChanged.emit(text)

    def currentText(self) -> str:  # pragma: no cover - helper
        return self._current


class QSlider(QWidget):
    def __init__(self, orientation: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._min = 0
        self._max = 0
        self._value = 0
        self._signals_blocked = False
        self.sliderPressed = _Signal()
        self.sliderReleased = _Signal()

    def setRange(self, minimum: int, maximum: int) -> None:
        self._min = int(minimum)
        self._max = int(maximum)
        if self._value < self._min:
            self._value = self._min
        if self._value > self._max:
            self._value = self._max

    def setValue(self, value: int) -> None:
        self._value = max(self._min, min(self._max, int(value)))

    def value(self) -> int:
        return self._value

    def blockSignals(self, block: bool) -> None:
        self._signals_blocked = bool(block)


class QLabel(QWidget):
    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._text = text

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:  # pragma: no cover - helper
        return self._text


class QListWidgetItem:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self._data: Dict[int, Any] = {}

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:  # pragma: no cover - helper
        return self._text

    def setData(self, role: int, value: Any) -> None:
        self._data[role] = value

    def data(self, role: int) -> Any:
        return self._data.get(role)


class QListWidget(QAbstractItemView):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: List[QListWidgetItem] = []
        self.itemActivated = _Signal()

    def addItem(self, item: QListWidgetItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> QListWidgetItem:
        return self._items[index]

    def row(self, item: QListWidgetItem) -> int:
        return self._items.index(item)

    def takeItem(self, row: int) -> Optional[QListWidgetItem]:
        if 0 <= row < len(self._items):
            return self._items.pop(row)
        return None


__all__ = [
    "QApplication",
    "QWidget",
    "QMainWindow",
    "QVBoxLayout",
    "QHBoxLayout",
    "QAbstractItemView",
    "QPushButton",
    "QLineEdit",
    "QDoubleSpinBox",
    "QComboBox",
    "QSlider",
    "QLabel",
    "QListWidget",
    "QListWidgetItem",
    "Qt",
]
