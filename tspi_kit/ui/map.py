"""Map preview helpers with smoothing support."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from PyQt5 import QtCore, QtWidgets


@dataclass(slots=True)
class MapState:
    center: Tuple[float, float] = (0.0, 0.0)
    zoom: float = 1.0


class MapSmoother:
    """Apply exponential smoothing to map center/zoom updates."""

    def __init__(self, *, smooth_center: float = 0.85, smooth_zoom: float = 0.85) -> None:
        if not (0.0 <= smooth_center <= 1.0 and 0.0 <= smooth_zoom <= 1.0):
            raise ValueError("Smoothing factors must be within [0, 1]")
        self._smooth_center = smooth_center
        self._smooth_zoom = smooth_zoom
        self._state: Optional[MapState] = None

    @property
    def state(self) -> MapState:
        if self._state is None:
            self._state = MapState()
        return self._state

    def update(self, center: Tuple[float, float], zoom: float) -> MapState:
        if self._state is None:
            self._state = MapState()

        cx = self._smooth_center * self._state.center[0] + (1 - self._smooth_center) * center[0]
        cy = self._smooth_center * self._state.center[1] + (1 - self._smooth_center) * center[1]
        z = self._smooth_zoom * self._state.zoom + (1 - self._smooth_zoom) * zoom
        self._state = MapState(center=(cx, cy), zoom=z)
        return self._state


class MapPreviewWidget(QtWidgets.QWidget):
    """Lightweight widget that stores smoothed map state for tests."""

    state_changed = QtCore.pyqtSignal(MapState)

    def __init__(self, smoother: MapSmoother, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._smoother = smoother
        layout = QtWidgets.QVBoxLayout(self)
        self._position_label = QtWidgets.QLabel("Map Preview", self)
        layout.addWidget(self._position_label)
        self._marker_color = "#00ff00"
        self._marker_label = QtWidgets.QLabel("Marker: #00ff00", self)
        layout.addWidget(self._marker_label)
        self._state = self._smoother.state
        self._update_position_label()

    @property
    def state(self) -> MapState:
        return self._state

    @property
    def marker_color(self) -> str:
        return self._marker_color

    def apply_position(self, center: Tuple[float, float], zoom: float) -> MapState:
        self._state = self._smoother.update(center, zoom)
        self._update_position_label()
        self.state_changed.emit(self._state)
        return self._state

    def set_marker_color(self, color: str) -> None:
        if not color:
            return
        self._marker_color = color
        self._marker_label.setText(f"Marker: {color}")

    def _update_position_label(self) -> None:
        center_x, center_y = self._state.center
        self._position_label.setText(
            f"Center: {center_x:.2f}, {center_y:.2f} | Zoom: {self._state.zoom:.2f}"
        )
