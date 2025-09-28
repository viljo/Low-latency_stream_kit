"""Map smoothing helpers used by the player UI."""
from __future__ import annotations

"""Map smoothing helpers used by the Flet player UI."""

from dataclasses import dataclass
from typing import Optional, Tuple

from .signals import Signal


@dataclass(slots=True)
class MapState:
    """Represents the smoothed map position and zoom."""

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


class MapPreviewWidget:
    """Headless-friendly map preview that records smoothing state."""

    def __init__(self, smoother: MapSmoother) -> None:
        self._smoother = smoother
        self._state = smoother.state
        self._marker_color = "#00ff00"
        self.state_changed: Signal[MapState] = Signal()
        self.position_text = self._format_position()
        self.marker_text = self._format_marker()

    @property
    def state(self) -> MapState:
        return self._state

    @property
    def marker_color(self) -> str:
        return self._marker_color

    def apply_position(self, center: Tuple[float, float], zoom: float) -> MapState:
        self._state = self._smoother.update(center, zoom)
        self.position_text = self._format_position()
        self.state_changed.emit(self._state)
        return self._state

    def set_marker_color(self, color: str) -> None:
        if not color:
            return
        self._marker_color = color
        self.marker_text = self._format_marker()

    def _format_position(self) -> str:
        center_x, center_y = self._state.center
        return f"Center: {center_x:.2f}, {center_y:.2f} | Zoom: {self._state.zoom:.2f}"

    def _format_marker(self) -> str:
        return f"Marker: {self._marker_color}"


__all__ = ["MapPreviewWidget", "MapSmoother", "MapState"]
