"""Qt-based JetStream player implementation."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt5 import QtCore, QtWidgets

from ..jetstream_sim import InMemoryJetStream
from ..receiver import TSPIReceiver
from ..schema import validate_payload
from .config import UiConfig
from .map import MapPreviewWidget, MapSmoother


@dataclass(slots=True)
class PlayerMetrics:
    frames: int = 0
    rate: float = 0.0
    clock: str = "receive"
    lag: int = 0

    def to_json(self) -> str:
        return json.dumps({
            "frames": self.frames,
            "rate": self.rate,
            "clock": self.clock,
            "lag": self.lag,
        })


class PlayerState(QtCore.QObject):
    """Controller shared between GUI and headless playback."""

    metrics_updated = QtCore.pyqtSignal(PlayerMetrics)

    def __init__(
        self,
        receiver: TSPIReceiver,
        *,
        ui_config: UiConfig,
        map_widget: Optional[MapPreviewWidget] = None,
    ) -> None:
        super().__init__()
        self._receiver = receiver
        self._ui_config = ui_config
        self._map_widget = map_widget
        self._playing = False
        self._buffer: List[dict] = []
        self._metrics = PlayerMetrics(clock=ui_config.default_clock, rate=ui_config.default_rate)
        self._last_metrics = time.monotonic()
        self._timer = QtCore.QTimer()
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._clock_source = ui_config.default_clock
        self._rate = ui_config.default_rate

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def clock_source(self) -> str:
        return self._clock_source

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, value: float) -> None:
        self._rate = max(self._ui_config.rate_min, min(self._ui_config.rate_max, value))
        self._metrics.rate = self._rate

    def set_clock_source(self, value: str) -> None:
        self._clock_source = value
        self._metrics.clock = value

    def start(self) -> None:
        if not self._playing:
            self._playing = True
            self._timer.start()

    def pause(self) -> None:
        if self._playing:
            self._playing = False
            self._timer.stop()

    def seek(self, iso_timestamp: str) -> None:
        try:
            target = datetime.fromisoformat(iso_timestamp)
        except ValueError:
            return
        target_epoch = target.timestamp()
        self._buffer = [
            message
            for message in self._buffer
            if message.get("recv_iso") and datetime.fromisoformat(message["recv_iso"]).timestamp() >= target_epoch
        ]

    def preload(self, batch: int = 50) -> None:
        messages = self._receiver.fetch(batch=batch)
        for message in messages:
            validate_payload(message)
        self._buffer.extend(messages)

    def _tick(self) -> None:
        if not self._playing:
            return
        if not self._buffer:
            self.preload()
            if not self._buffer:
                self.pause()
                return
        message = self._buffer.pop(0)
        self._metrics.frames += 1
        if self._map_widget:
            payload = message.get("payload", {})
            center = (
                float(payload.get("x_m", payload.get("range_m", 0.0))),
                float(payload.get("y_m", payload.get("azimuth_deg", 0.0))),
            )
            zoom = 1.0 + abs(float(payload.get("vx_mps", payload.get("range_rate_mps", 0.0)))) * 0.01
            self._map_widget.apply_position(center, zoom)

        now = time.monotonic()
        if now - self._last_metrics >= self._ui_config.metrics_interval:
            self._last_metrics = now
            if hasattr(self._receiver, "_consumer"):
                pending = getattr(self._receiver._consumer, "pending", lambda: 0)()
            else:
                pending = 0
            self._metrics.lag = pending
            self.metrics_updated.emit(self._metrics)

    def step_once(self) -> None:
        self._tick()

    def buffer_size(self) -> int:
        return len(self._buffer)

    def buffer_snapshot(self) -> List[dict]:
        return list(self._buffer)


class JetStreamPlayerWindow(QtWidgets.QMainWindow):
    """Qt main window for the JetStream player."""

    def __init__(
        self,
        receiver: TSPIReceiver,
        *,
        ui_config: Optional[UiConfig] = None,
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("JetStream Player")
        self._config = ui_config or UiConfig()
        self._map = MapPreviewWidget(MapSmoother(
            smooth_center=self._config.smooth_center,
            smooth_zoom=self._config.smooth_zoom,
        ))
        self._state = PlayerState(receiver, ui_config=self._config, map_widget=self._map)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        control_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(control_layout)

        self.connect_button = QtWidgets.QPushButton("Connect", self)
        control_layout.addWidget(self.connect_button)

        self.play_button = QtWidgets.QPushButton("Play", self)
        control_layout.addWidget(self.play_button)
        self.play_button.clicked.connect(self._toggle_play)

        self.seek_input = QtWidgets.QLineEdit(self)
        self.seek_input.setPlaceholderText("ISO timestamp")
        control_layout.addWidget(self.seek_input)

        self.seek_button = QtWidgets.QPushButton("Seek", self)
        control_layout.addWidget(self.seek_button)
        self.seek_button.clicked.connect(self._on_seek)

        self.rate_spin = QtWidgets.QDoubleSpinBox(self)
        self.rate_spin.setRange(self._config.rate_min, self._config.rate_max)
        self.rate_spin.setValue(self._config.default_rate)
        control_layout.addWidget(self.rate_spin)
        self.rate_spin.valueChanged.connect(self._state.set_rate)

        self.clock_combo = QtWidgets.QComboBox(self)
        self.clock_combo.addItems(["receive", "tspi"])
        control_layout.addWidget(self.clock_combo)
        self.clock_combo.currentTextChanged.connect(self._state.set_clock_source)

        layout.addWidget(self._map)

        self._metrics_label = QtWidgets.QLabel("Frames: 0", self)
        layout.addWidget(self._metrics_label)

        self._state.metrics_updated.connect(self._update_metrics)

    def _toggle_play(self) -> None:
        if self._state.playing:
            self._state.pause()
            self.play_button.setText("Play")
        else:
            self._state.start()
            self.play_button.setText("Pause")

    def _on_seek(self) -> None:
        self._state.seek(self.seek_input.text())

    def _update_metrics(self, metrics: PlayerMetrics) -> None:
        self._metrics_label.setText(metrics.to_json())

    def step_once(self) -> None:
        self._state.step_once()

    @property
    def map_widget(self) -> MapPreviewWidget:
        return self._map

    @property
    def state(self) -> PlayerState:
        return self._state


class HeadlessPlayerRunner:
    """Headless playback runner emitting metrics as JSON."""

    def __init__(
        self,
        receiver: TSPIReceiver,
        *,
        ui_config: Optional[UiConfig] = None,
        stdout_json: bool = True,
        duration: Optional[float] = None,
        exit_on_idle: Optional[float] = None,
        write_cbor_dir: Optional[Path] = None,
    ) -> None:
        self._config = ui_config or UiConfig()
        self._state = PlayerState(receiver, ui_config=self._config)
        self._stdout_json = stdout_json
        self._duration = duration
        self._exit_on_idle = exit_on_idle
        self._write_cbor_dir = write_cbor_dir
        self._start_time = time.monotonic()
        self._last_activity = self._start_time
        if write_cbor_dir:
            write_cbor_dir.mkdir(parents=True, exist_ok=True)
        self._state.metrics_updated.connect(self._on_metrics)

    def run(self) -> None:
        self._state.start()
        while True:
            self._state.step_once()
            now = time.monotonic()
            if self._duration is not None and now - self._start_time >= self._duration:
                break
            if self._exit_on_idle is not None and now - self._last_activity >= self._exit_on_idle:
                break
            if not self._state.playing:
                if self._state.buffer_size() == 0:
                    break
                time.sleep(0.01)
            else:
                self._last_activity = now
                time.sleep(max(0.001, 0.05 / self._state.rate))

    def _on_metrics(self, metrics: PlayerMetrics) -> None:
        if self._stdout_json:
            print(metrics.to_json())
        if self._write_cbor_dir:
            path = self._write_cbor_dir / f"frame_{metrics.frames:06d}.cbor"
            path.write_bytes(b"")


def connect_in_memory() -> tuple[InMemoryJetStream, TSPIReceiver]:
    stream = InMemoryJetStream()
    consumer = stream.create_consumer("tspi.>")
    receiver = TSPIReceiver(consumer)
    return stream, receiver


def ensure_offscreen(headless: bool) -> None:
    if headless:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
