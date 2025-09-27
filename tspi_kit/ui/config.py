"""Shared configuration values for Qt applications."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UiConfig:
    """Runtime configuration for UI and headless runners."""

    smooth_center: float = 0.85
    smooth_zoom: float = 0.85
    window_sec: int = 10
    metrics_interval: float = 1.0
    rate_min: float = 0.01
    rate_max: float = 4.0
    default_rate: float = 1.0
    default_clock: str = "receive"
    scrub_history_size: int = 600
    default_units: str = "metric"
    default_marker_color: str = "#00ff00"
