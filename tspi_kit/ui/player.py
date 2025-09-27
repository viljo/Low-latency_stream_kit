"""Qt-based JetStream player implementation."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from PyQt5 import QtCore, QtWidgets

from ..jetstream_sim import InMemoryJetStream
from ..receiver import TSPIReceiver
from ..schema import validate_payload
from .config import UiConfig
from .map import MapPreviewWidget, MapSmoother

ReceiverFactory = Callable[[], TSPIReceiver]


@dataclass(slots=True)
class PlayerMetrics:
    frames: int = 0
    rate: float = 0.0
    clock: str = "receive"
    lag: int = 0
    source: str = "live"
    position: int = 0
    timeline: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "frames": self.frames,
                "rate": self.rate,
                "clock": self.clock,
                "lag": self.lag,
                "source": self.source,
                "position": self.position,
                "timeline": self.timeline,
            }
        )


class PlayerState(QtCore.QObject):
    """Controller shared between GUI and headless playback with source switching."""

    metrics_updated = QtCore.pyqtSignal(PlayerMetrics)

    def __init__(
        self,
        sources: Mapping[str, ReceiverFactory | TSPIReceiver],
        *,
        ui_config: UiConfig,
        initial_source: str = "live",
        map_widget: Optional[MapPreviewWidget] = None,
    ) -> None:
        super().__init__()
        self._ui_config = ui_config
        self._map_widget = map_widget
        self._source_factories = self._normalize_sources(sources)
        if not self._source_factories:
            raise ValueError("At least one telemetry source must be provided")
        if initial_source not in self._source_factories:
            initial_source = next(iter(self._source_factories))
        self._current_source = initial_source
        self._receiver = self._source_factories[self._current_source]()
        self._playing = False
        self._timeline: List[dict] = []
        self._position = 0
        self._history_limit = max(1, ui_config.scrub_history_size)
        self._metrics = PlayerMetrics(
            clock=ui_config.default_clock,
            rate=ui_config.default_rate,
            source=self._current_source,
        )
        self._last_metrics = time.monotonic()
        self._timer = QtCore.QTimer()
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._clock_source = ui_config.default_clock
        self._rate = ui_config.default_rate

    @staticmethod
    def _normalize_sources(
        sources: Mapping[str, ReceiverFactory | TSPIReceiver]
    ) -> Dict[str, ReceiverFactory]:
        normalized: Dict[str, ReceiverFactory] = {}
        for name, source in sources.items():
            if isinstance(source, TSPIReceiver):
                receiver = source

                def _factory(receiver=receiver) -> TSPIReceiver:
                    return receiver

                normalized[name] = _factory
            elif callable(source):

                def _factory(factory=source) -> TSPIReceiver:  # type: ignore[valid-type]
                    value = factory()
                    if not isinstance(value, TSPIReceiver):
                        raise TypeError("Receiver factory must return TSPIReceiver")
                    return value

                normalized[name] = _factory
            else:
                raise TypeError("Sources must be TSPIReceiver instances or callables returning them")
        return normalized

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def clock_source(self) -> str:
        return self._clock_source

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def current_source(self) -> str:
        return self._current_source

    @property
    def available_sources(self) -> List[str]:
        return list(self._source_factories.keys())

    def set_rate(self, value: float) -> None:
        self._rate = max(self._ui_config.rate_min, min(self._ui_config.rate_max, value))
        self._metrics.rate = self._rate
        self._emit_metrics(force=True)

    def set_clock_source(self, value: str) -> None:
        self._clock_source = value
        self._metrics.clock = value
        self._emit_metrics(force=True)

    def set_source_mode(self, name: str) -> None:
        if name == self._current_source:
            return
        if name not in self._source_factories:
            raise KeyError(f"Unknown source {name}")
        self.pause()
        self._receiver = self._source_factories[name]()
        self._current_source = name
        self._timeline.clear()
        self._position = 0
        self._metrics.frames = 0
        self._metrics.lag = 0
        self._metrics.source = name
        self._metrics.position = 0
        self._metrics.timeline = 0
        self._emit_metrics(force=True)

    def start(self) -> None:
        if not self._playing:
            self._playing = True
            self._timer.start()
            self._emit_metrics(force=True)

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
        for index, message in enumerate(self._timeline):
            recv_iso = message.get("recv_iso")
            if not recv_iso:
                continue
            try:
                recv_epoch = datetime.fromisoformat(recv_iso).timestamp()
            except ValueError:
                continue
            if recv_epoch >= target_epoch:
                self._position = index
                self._metrics.position = self._position
                break
        self._emit_metrics(force=True)

    def preload(self, batch: int = 50) -> None:
        messages = self._receiver.fetch(batch=batch)
        if not messages:
            return
        for message in messages:
            validate_payload(message)
        self._timeline.extend(messages)
        if len(self._timeline) > self._history_limit:
            drop = len(self._timeline) - self._history_limit
            del self._timeline[:drop]
            self._position = max(0, self._position - drop)
        self._emit_metrics(force=True)

    def scrub_to_index(self, index: int) -> None:
        if not self._timeline:
            return
        index = max(0, min(index, len(self._timeline) - 1))
        self._position = index
        self._metrics.position = self._position
        self._emit_metrics(force=True)

    def timeline_length(self) -> int:
        return len(self._timeline)

    def position(self) -> int:
        return self._position

    def _tick(self) -> None:
        if not self._playing:
            return
        if self._position >= len(self._timeline):
            self.preload()
            if self._position >= len(self._timeline):
                self.pause()
                return
        message = self._timeline[self._position]
        self._position += 1
        self._metrics.frames += 1
        self._metrics.position = self._position
        self._metrics.timeline = len(self._timeline)
        if self._map_widget:
            payload = message.get("payload", {})
            center = (
                float(payload.get("x_m", payload.get("range_m", 0.0))),
                float(payload.get("y_m", payload.get("azimuth_deg", 0.0))),
            )
            zoom = 1.0 + abs(float(payload.get("vx_mps", payload.get("range_rate_mps", 0.0)))) * 0.01
            self._map_widget.apply_position(center, zoom)
        self._emit_metrics()

    def _emit_metrics(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_metrics >= self._ui_config.metrics_interval:
            self._last_metrics = now
            consumer = getattr(self._receiver, "_consumer", None)
            if consumer and hasattr(consumer, "pending"):
                try:
                    pending = consumer.pending()
                except TypeError:
                    pending = consumer.pending
            else:
                pending = 0
            self._metrics.lag = int(pending)
            self._metrics.timeline = len(self._timeline)
            self._metrics.position = self._position
            self.metrics_updated.emit(self._metrics)

    def step_once(self) -> None:
        self._tick()

    def buffer_size(self) -> int:
        return max(0, len(self._timeline) - self._position)

    def buffer_snapshot(self) -> List[dict]:
        return list(self._timeline[self._position :])


class JetStreamPlayerWindow(QtWidgets.QMainWindow):
    """Qt main window for the JetStream player with live/historical switching."""

    def __init__(
        self,
        sources: Mapping[str, ReceiverFactory | TSPIReceiver],
        *,
        ui_config: Optional[UiConfig] = None,
        initial_source: str = "live",
        parent: Optional[QtWidgets.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unified JetStream Receiver")
        self._config = ui_config or UiConfig()
        self._map = MapPreviewWidget(
            MapSmoother(
                smooth_center=self._config.smooth_center,
                smooth_zoom=self._config.smooth_zoom,
            )
        )
        self._state = PlayerState(
            sources,
            ui_config=self._config,
            initial_source=initial_source,
            map_widget=self._map,
        )
        self._scrubbing = False
        self._build_ui()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        control_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(control_layout)

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
        self.clock_combo.setCurrentText(self._state.clock_source)
        control_layout.addWidget(self.clock_combo)
        self.clock_combo.currentTextChanged.connect(self._state.set_clock_source)

        self.source_combo = QtWidgets.QComboBox(self)
        self.source_combo.addItems(self._state.available_sources)
        self.source_combo.setCurrentText(self._state.current_source)
        control_layout.addWidget(self.source_combo)
        self.source_combo.currentTextChanged.connect(self._state.set_source_mode)

        layout.addWidget(self._map)

        self._scrub_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self._scrub_slider.setRange(0, 0)
        layout.addWidget(self._scrub_slider)
        self._scrub_slider.sliderPressed.connect(self._on_scrub_pressed)
        self._scrub_slider.sliderReleased.connect(self._on_scrub_released)

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

    def _on_scrub_pressed(self) -> None:
        self._scrubbing = True

    def _on_scrub_released(self) -> None:
        value = self._scrub_slider.value()
        self._scrubbing = False
        self._state.scrub_to_index(value)

    def _update_metrics(self, metrics: PlayerMetrics) -> None:
        self._metrics_label.setText(metrics.to_json())
        if not self._scrubbing:
            self._scrub_slider.blockSignals(True)
            self._scrub_slider.setRange(0, max(0, metrics.timeline - 1))
            self._scrub_slider.setValue(metrics.position if metrics.timeline else 0)
            self._scrub_slider.blockSignals(False)

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
        sources: Mapping[str, ReceiverFactory | TSPIReceiver],
        *,
        ui_config: Optional[UiConfig] = None,
        stdout_json: bool = True,
        duration: Optional[float] = None,
        exit_on_idle: Optional[float] = None,
        write_cbor_dir: Optional[Path] = None,
        initial_source: str = "live",
    ) -> None:
        self._config = ui_config or UiConfig()
        self._state = PlayerState(
            sources,
            ui_config=self._config,
            initial_source=initial_source,
        )
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
                time.sleep(max(0.001, 0.05 / max(self._state.rate, 0.01)))

    def _on_metrics(self, metrics: PlayerMetrics) -> None:
        if self._stdout_json:
            print(metrics.to_json())
        if self._write_cbor_dir:
            path = self._write_cbor_dir / f"frame_{metrics.frames:06d}.cbor"
            path.write_bytes(b"")


def connect_in_memory(
    subject_map: Optional[Mapping[str, str]] = None,
) -> tuple[InMemoryJetStream, Dict[str, ReceiverFactory]]:
    stream = InMemoryJetStream()
    subjects = dict(subject_map or {"live": "tspi.>", "historical": "player.default.playout.>"})

    def _make_factory(subject: str) -> ReceiverFactory:
        def _factory(subject=subject) -> TSPIReceiver:
            consumer = stream.create_consumer(subject)
            return TSPIReceiver(consumer)

        return _factory

    receivers = {name: _make_factory(subject) for name, subject in subjects.items()}
    return stream, receivers


def ensure_offscreen(headless: bool) -> None:
    if headless:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
