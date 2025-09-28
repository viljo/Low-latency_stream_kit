"""Flet-aware player controller, harness, and headless utilities."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from jsonschema import exceptions as jsonschema_exceptions

from ..jetstream_sim import InMemoryJetStream
from ..receiver import CompositeTSPIReceiver, TSPIReceiver
from ..schema import validate_payload
from ..tags import TagSender
from .config import UiConfig
from .map import MapPreviewWidget, MapSmoother
from .signals import Signal

ReceiverFactory = Callable[[], TSPIReceiver | CompositeTSPIReceiver]


@dataclass(slots=True)
class PlayerMetrics:
    """Runtime metrics emitted by :class:`PlayerState`."""

    frames: int = 0
    rate: float = 0.0
    clock: str = "receive"
    lag: int = 0
    source: str = "livestream"
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


class PlayerState:
    """Controller shared by the Flet UI and headless runner."""

    def __init__(
        self,
        sources: Mapping[str, ReceiverFactory | TSPIReceiver],
        *,
        ui_config: UiConfig,
        initial_source: str = "live",
        map_widget: Optional[MapPreviewWidget] = None,
    ) -> None:
        self.metrics_updated: Signal[PlayerMetrics] = Signal()
        self.display_units_changed: Signal[str] = Signal()
        self.marker_color_changed: Signal[str] = Signal()
        self.command_event: Signal[object] = Signal()
        self.tag_event: Signal[object] = Signal()
        self._ui_config = ui_config
        self._map_widget = map_widget
        self._channel_labels: Dict[str, str] = {}
        self._channel_factories = self._normalize_sources(sources)
        if not self._channel_factories:
            raise ValueError("At least one telemetry source must be provided")
        initial_channel = self._normalise_channel_name(initial_source)
        if initial_channel not in self._channel_factories:
            if "livestream" in self._channel_factories:
                initial_channel = "livestream"
            else:
                initial_channel = next(iter(self._channel_factories))
        self._current_channel = initial_channel
        self._receiver = self._channel_factories[self._current_channel]()
        self._playing = False
        self._timeline: List[dict] = []
        self._position = 0
        self._history_limit = max(1, ui_config.scrub_history_size)
        self._tags: Dict[str, dict] = {}
        self._sideband_cursor = 0
        self._metrics = PlayerMetrics(
            clock=ui_config.default_clock,
            rate=ui_config.default_rate,
            source=self._current_channel,
        )
        self._last_metrics = time.monotonic()
        self._clock_source = ui_config.default_clock
        self._rate = ui_config.default_rate
        self._display_units = ui_config.default_units
        self._marker_color = ui_config.default_marker_color
        if self._map_widget:
            self._map_widget.set_marker_color(self._marker_color)

    @staticmethod
    def _normalise_channel_name(name: str) -> str:
        label = str(name).strip()
        if not label:
            raise ValueError("Channel name must be a non-empty string")
        lowered = label.lower()
        if lowered in {"live", "livestream"}:
            return "livestream"
        if lowered == "historical":
            return "replay.default"
        return label

    def _normalize_sources(
        self, sources: Mapping[str, ReceiverFactory | TSPIReceiver]
    ) -> Dict[str, ReceiverFactory]:
        normalized: Dict[str, ReceiverFactory] = {}
        for raw_name, source in sources.items():
            channel_id = self._normalise_channel_name(raw_name)
            display_name = str(raw_name)
            if channel_id == "livestream":
                display_name = "livestream"
            elif channel_id.startswith("replay."):
                display_name = channel_id
            if isinstance(source, (TSPIReceiver, CompositeTSPIReceiver)):
                receiver = source

                def _factory(receiver=receiver) -> TSPIReceiver:
                    return receiver

                normalized[channel_id] = _factory
            elif callable(source):

                def _factory(factory=source) -> TSPIReceiver:  # type: ignore[valid-type]
                    value = factory()
                    if not isinstance(value, (TSPIReceiver, CompositeTSPIReceiver)):
                        raise TypeError("Receiver factory must return TSPIReceiver")
                    return value

                normalized[channel_id] = _factory
            else:
                raise TypeError("Sources must be TSPIReceiver instances or callables returning them")
            self._channel_labels.setdefault(channel_id, display_name)
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
    def current_channel(self) -> str:
        return self._current_channel

    @property
    def available_channels(self) -> List[str]:
        return list(self._channel_factories.keys())

    def channel_label(self, channel_id: str) -> str:
        return self._channel_labels.get(channel_id, channel_id)

    def channel_options(self) -> List[tuple[str, str]]:
        return [(self.channel_label(channel), channel) for channel in self.available_channels]

    @property
    def display_units(self) -> str:
        return self._display_units

    @property
    def marker_color(self) -> str:
        return self._marker_color

    @property
    def tags(self) -> Mapping[str, dict]:
        return dict(self._tags)

    def start(self) -> None:
        if not self._playing:
            self._playing = True
            self._emit_metrics(force=True)

    def pause(self) -> None:
        if self._playing:
            self._playing = False

    def seek(self, iso_timestamp: str) -> None:
        previous = self._position
        try:
            target = datetime.fromisoformat(iso_timestamp)
        except ValueError:
            return
        target_epoch = target.timestamp()
        new_position = self._position
        for index, message in enumerate(self._timeline):
            recv_iso = message.get("recv_iso")
            if not recv_iso:
                continue
            try:
                recv_epoch = datetime.fromisoformat(recv_iso).timestamp()
            except ValueError:
                continue
            if recv_epoch >= target_epoch:
                new_position = index
                break
        if new_position != self._position:
            self._position = new_position
            self._metrics.position = self._position
            self._handle_jump(previous, self._position)
        self._emit_metrics(force=True)

    def preload(self, batch: int = 50) -> None:
        messages = self._receiver.fetch(batch=batch)
        if not messages:
            return
        filtered: List[dict] = []
        for message in messages:
            if isinstance(message, dict):
                if self._is_tag_event(message):
                    filtered.append(message)
                    continue
                if "cmd_id" not in message:
                    try:
                        validate_payload(message)
                    except jsonschema_exceptions.ValidationError:
                        continue
            filtered.append(message)
        if not filtered:
            return
        self._timeline.extend(filtered)
        if len(self._timeline) > self._history_limit:
            drop = len(self._timeline) - self._history_limit
            del self._timeline[:drop]
            self._position = max(0, self._position - drop)
            self._sideband_cursor = max(0, self._sideband_cursor - drop)
        self._emit_metrics(force=True)

    def scrub_to_index(self, index: int) -> None:
        if not self._timeline:
            return
        previous = self._position
        index = max(0, min(index, len(self._timeline) - 1))
        self._position = index
        self._metrics.position = self._position
        self._handle_jump(previous, self._position)
        self._emit_metrics(force=True)

    def timeline_length(self) -> int:
        return len(self._timeline)

    def position(self) -> int:
        return self._position

    def buffer_size(self) -> int:
        return max(0, len(self._timeline) - self._position)

    def buffer_snapshot(self) -> List[dict]:
        return list(self._timeline[self._position :])

    def seek_to_tag(self, tag_id: str) -> bool:
        tag_id = tag_id.strip()
        if not tag_id:
            return False
        for index, message in enumerate(self._timeline):
            if isinstance(message, dict) and str(message.get("id", "")).strip() == tag_id:
                self._position = index
                self._metrics.position = self._position
                self._emit_metrics(force=True)
                return True
        tag = self._tags.get(tag_id)
        if tag:
            for key in ("recv_iso", "ts", "updated_ts"):
                iso_value = tag.get(key)
                if isinstance(iso_value, str) and iso_value:
                    self.seek(iso_value)
                    return True
        return False

    def set_rate(self, rate: float) -> None:
        clamped = max(self._ui_config.rate_min, min(self._ui_config.rate_max, float(rate)))
        self._rate = clamped
        self._metrics.rate = clamped
        self._emit_metrics(force=True)

    def set_clock_source(self, clock: str) -> None:
        clock = str(clock).lower()
        if clock in {"receive", "tspi"}:
            self._clock_source = clock
            self._metrics.clock = clock
            self._emit_metrics(force=True)

    def set_channel(self, channel: str) -> None:
        normalized = self._normalise_channel_name(channel)
        if normalized not in self._channel_factories:
            return
        if normalized == self._current_channel:
            return
        self._current_channel = normalized
        self._receiver = self._channel_factories[normalized]()
        self._timeline.clear()
        self._position = 0
        self._sideband_cursor = 0
        self._metrics.source = normalized
        self._metrics.frames = 0
        self._emit_metrics(force=True)

    def set_source_mode(self, name: str) -> None:
        self.set_channel(name)

    def step_once(self) -> None:
        self._tick()

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
        self._handle_message(message)
        self._sideband_cursor = max(self._sideband_cursor, self._position)
        self._emit_metrics()

    def _handle_jump(self, previous: int, current: int) -> None:
        if current > previous:
            self._replay_sideband(previous, current)
            self._sideband_cursor = current
        elif current < previous:
            self._sideband_cursor = min(self._sideband_cursor, current)

    def _replay_sideband(self, start: int, end: int) -> None:
        if end <= start:
            return
        start = max(0, start)
        end = min(end, len(self._timeline))
        for message in self._timeline[start:end]:
            if not isinstance(message, dict):
                continue
            if "cmd_id" in message:
                self._handle_command(message)
            elif self._is_tag_event(message):
                self._handle_tag(message)

    @staticmethod
    def _is_tag_event(message: Mapping[str, object]) -> bool:
        return "id" in message and "status" in message and "cmd_id" not in message

    def _handle_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        if "cmd_id" in message:
            self._handle_command(message)
            return
        if self._is_tag_event(message):
            self._handle_tag(message)
            return
        self._handle_telemetry(message)

    def _handle_command(self, message: dict) -> None:
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return
        name = message.get("name")
        if name == "display.units":
            units = str(payload.get("units", "")).lower()
            if units in {"metric", "imperial"} and units != self._display_units:
                self._display_units = units
                self.display_units_changed.emit(units)
        elif name == "display.marker_color":
            color = str(payload.get("marker_color", ""))
            if color and color != self._marker_color:
                self._marker_color = color
                if self._map_widget:
                    self._map_widget.set_marker_color(color)
                self.marker_color_changed.emit(color)
        self.command_event.emit(dict(message))

    def _handle_tag(self, message: dict) -> None:
        tag_id = str(message.get("id", "")).strip()
        if not tag_id:
            return
        status = str(message.get("status", "")).lower()
        if status == "deleted":
            self._tags.pop(tag_id, None)
        else:
            self._tags[tag_id] = dict(message)
        self.tag_event.emit(dict(message))

    def _handle_telemetry(self, message: dict) -> None:
        if self._map_widget:
            payload = message.get("payload", {})
            if isinstance(payload, dict):
                center = (
                    float(payload.get("x_m", payload.get("range_m", 0.0))),
                    float(payload.get("y_m", payload.get("azimuth_deg", 0.0))),
                )
                zoom = 1.0 + abs(
                    float(payload.get("vx_mps", payload.get("range_rate_mps", 0.0)))
                ) * 0.01
                self._map_widget.apply_position(center, zoom)

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
            self.metrics_updated.emit(self._metrics)


class _SignalAdapter:
    def __init__(self) -> None:
        self._callbacks: List[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def emit(self, *args, **kwargs) -> None:
        for callback in list(self._callbacks):
            callback(*args, **kwargs)


class _Button:
    def __init__(self, label: str, on_click: Callable[[], None]) -> None:
        self._label = label
        self._on_click = on_click

    @property
    def text(self) -> str:
        return self._label

    def set_text(self, value: str) -> None:
        self._label = value

    def click(self) -> None:
        self._on_click()


class _TextInput:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value


class _SpinBox:
    def __init__(self, *, minimum: float, maximum: float, value: float) -> None:
        self._min = minimum
        self._max = maximum
        self._value = value
        self.valueChanged = _SignalAdapter()

    def setValue(self, value: float) -> None:
        clamped = max(self._min, min(self._max, value))
        self._value = clamped
        self.valueChanged.emit(clamped)

    def value(self) -> float:
        return self._value


class _ComboBox:
    def __init__(self, options: Sequence[str], current: str) -> None:
        self._options = list(options)
        self._current = current
        self.currentTextChanged = _SignalAdapter()

    def addItems(self, options: Iterable[str]) -> None:
        for option in options:
            if option not in self._options:
                self._options.append(option)

    def setCurrentText(self, value: str) -> None:
        if value not in self._options:
            return
        self._current = value
        self.currentTextChanged.emit(value)

    def currentText(self) -> str:
        return self._current

    def options(self) -> List[str]:
        return list(self._options)


class _Label:
    def __init__(self, text: str) -> None:
        self._text = text

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


class _ListItem:
    def __init__(self, text: str, data: Optional[str] = None) -> None:
        self._text = text
        self._data = data

    def text(self) -> str:
        return self._text

    def data(self) -> Optional[str]:
        return self._data


class _ListWidget:
    def __init__(self) -> None:
        self._items: List[_ListItem] = []

    def addItem(self, text: str) -> None:
        self._items.append(_ListItem(text))

    def add_or_replace(self, text: str, tag_id: str) -> None:
        self.remove_by_data(tag_id)
        self._items.append(_ListItem(text, tag_id))

    def clear(self) -> None:
        self._items.clear()

    def remove_by_data(self, tag_id: str) -> None:
        self._items = [item for item in self._items if item.data() != tag_id]

    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> _ListItem:
        return self._items[index]

    def takeItem(self, index: int) -> None:
        del self._items[index]


class JetStreamPlayerWindow:
    """Headless harness exposing a Flet-inspired API for tests."""

    def __init__(
        self,
        sources: Mapping[str, ReceiverFactory | TSPIReceiver] | ReceiverFactory | TSPIReceiver,
        *,
        ui_config: Optional[UiConfig] = None,
        initial_source: str = "live",
        tag_sender: Optional[TagSender] = None,
    ) -> None:
        config = ui_config or UiConfig()
        self._map = MapPreviewWidget(
            MapSmoother(smooth_center=config.smooth_center, smooth_zoom=config.smooth_zoom)
        )
        normalized_sources = self._coerce_sources(sources)
        self._state = PlayerState(
            normalized_sources,
            ui_config=config,
            initial_source=initial_source,
            map_widget=self._map,
        )
        self._tag_sender = tag_sender
        self._tag_items: Dict[str, _ListItem] = {}
        self._logitech_limit = 200
        self._log_entries = _ListWidget()
        self._tag_list = _ListWidget()
        self.play_button = _Button("Play", self._toggle_play)
        self.seek_input = _TextInput()
        self.seek_button = _Button("Seek", self._on_seek)
        self.rate_spin = _SpinBox(
            minimum=config.rate_min, maximum=config.rate_max, value=self._state.rate
        )
        self.rate_spin.valueChanged.connect(self._state.set_rate)
        self.clock_combo = _ComboBox(["receive", "tspi"], self._state.clock_source)
        self.clock_combo.currentTextChanged.connect(self._state.set_clock_source)
        self.source_combo = _ComboBox(self._state.available_channels, self._state.current_channel)
        self.source_combo.currentTextChanged.connect(self._state.set_channel)
        self._units_label = _Label(f"Units: {self._state.display_units}")
        self._marker_label = _Label(f"Marker: {self._state.marker_color}")
        self._state.display_units_changed.connect(self._update_units)
        self._state.marker_color_changed.connect(self._update_marker_color)
        self._state.command_event.connect(self._on_command_event)
        self._state.tag_event.connect(self._on_tag_event)
        self._state.metrics_updated.connect(self._on_metrics)

    @staticmethod
    def _coerce_sources(
        sources: Mapping[str, ReceiverFactory | TSPIReceiver] | ReceiverFactory | TSPIReceiver,
    ) -> Mapping[str, ReceiverFactory | TSPIReceiver]:
        if isinstance(sources, TSPIReceiver) or callable(sources):
            return {"livestream": sources}
        if hasattr(sources, "items"):
            return dict(sources)  # type: ignore[return-value]
        raise TypeError("Unsupported sources object for JetStreamPlayerWindow")

    def _toggle_play(self) -> None:
        if self._state.playing:
            self._state.pause()
            self.play_button.set_text("Play")
        else:
            self._state.start()
            self.play_button.set_text("Pause")

    def _on_seek(self) -> None:
        self._state.seek(self.seek_input.text())

    def _update_units(self, units: str) -> None:
        self._units_label.setText(f"Units: {units}")

    def _update_marker_color(self, color: str) -> None:
        self._marker_label.setText(f"Marker: {color}")

    def _on_metrics(self, metrics: PlayerMetrics) -> None:
        summary = f"Frames: {metrics.frames} | Rate: {metrics.rate:.2f}"
        self._log_entries.addItem(summary)
        if self._log_entries.count() > self._logitech_limit:
            self._log_entries.takeItem(0)

    def _on_command_event(self, message: Mapping[str, object]) -> None:
        summary = message.get("name", "command")
        timestamp = self._extract_timestamp(message)
        entry = f"[CMD] {timestamp} — {summary}" if timestamp else f"[CMD] {summary}"
        self._log_entries.addItem(entry)
        if self._log_entries.count() > self._logitech_limit:
            self._log_entries.takeItem(0)

    def _on_tag_event(self, message: Mapping[str, object]) -> None:
        tag_id = str(message.get("id", "")).strip()
        if not tag_id:
            return
        status = str(message.get("status", "")).lower()
        label = message.get("label") or tag_id
        summary = f"{label} ({tag_id})"
        if status == "deleted":
            self._tag_items.pop(tag_id, None)
            self._tag_list.remove_by_data(tag_id)
        else:
            item = _ListItem(summary, tag_id)
            self._tag_items[tag_id] = item
            self._tag_list.add_or_replace(summary, tag_id)

    @staticmethod
    def _extract_timestamp(message: Mapping[str, object]) -> str:
        for key in ("recv_iso", "ts", "updated_ts"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        epoch = message.get("recv_epoch_ms")
        if isinstance(epoch, (int, float)):
            dt = datetime.fromtimestamp(float(epoch) / 1000.0, tz=UTC)
            return dt.isoformat()
        return ""

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
    subject_map: Optional[Mapping[str, str | Sequence[str]]] = None,
) -> tuple[InMemoryJetStream, Dict[str, ReceiverFactory]]:
    stream = InMemoryJetStream()
    subjects = dict(
        subject_map
        or {
            "livestream": ["tspi.>", "tspi.cmd.display.>", "tags.broadcast"],
            "replay.default": ["player.default.playout.>", "tags.broadcast"],
        }
    )

    def _make_factory(subjects: Sequence[str]) -> ReceiverFactory:
        def _factory(subjects=tuple(subjects)) -> TSPIReceiver:
            consumers = [stream.create_consumer(subject) for subject in subjects]
            receivers = [TSPIReceiver(consumer) for consumer in consumers]
            if len(receivers) == 1:
                return receivers[0]
            return CompositeTSPIReceiver(receivers)

        return _factory

    normalized: Dict[str, Sequence[str]] = {}
    for name, subject in subjects.items():
        if isinstance(subject, str):
            normalized[name] = [subject]
        else:
            normalized[name] = list(subject)

    receivers = {name: _make_factory(subjects) for name, subjects in normalized.items()}
    return stream, receivers


def ensure_offscreen(headless: bool) -> None:
    # Flet does not require special handling for headless execution, but the
    # function remains for backwards compatibility with the old Qt entry point.
    _ = headless


__all__ = [
    "HeadlessPlayerRunner",
    "JetStreamPlayerWindow",
    "PlayerMetrics",
    "PlayerState",
    "ReceiverFactory",
    "connect_in_memory",
    "ensure_offscreen",
]
