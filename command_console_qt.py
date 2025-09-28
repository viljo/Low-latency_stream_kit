from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt5 import QtCore, QtWidgets

from tspi_kit.commands import (
    COMMAND_SUBJECT_PREFIX,
    CommandSender,
    OpsControlSender,
)
from tspi_kit.commands import OPS_CONTROL_SUBJECT
from tspi_kit.jetstream_client import JetStreamConsumerAdapter, JetStreamThreadedClient
from tspi_kit.ui.command_console import (
    ClientPresence,
    ClientPresenceTracker,
    DataChunk,
    DataChunkTag,
    OperatorEvent,
    compose_replay_identifier,
)
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


_COLOR_CHOICES: List[Tuple[str, str]] = [
    ("Green", "#00ff00"),
    ("Red", "#ff0000"),
    ("Blue", "#0000ff"),
    ("Magenta", "#ff00ff"),
    ("Yellow", "#ffff00"),
]


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


class CommandController(QtCore.QObject):
    status_changed = QtCore.pyqtSignal(str)
    error_occurred = QtCore.pyqtSignal(str)
    group_replay_changed = QtCore.pyqtSignal(str)

    def __init__(self, sender: CommandSender, ops_sender: OpsControlSender) -> None:
        super().__init__()
        self._sender = sender
        self._ops_sender = ops_sender

    def set_units(self, units: str) -> None:
        try:
            payload = self._sender.send_units(units)
        except Exception as exc:  # pragma: no cover - surface to UI
            self.error_occurred.emit(str(exc))
            return
        self.status_changed.emit(f"Units set to {payload.payload['units']}")

    def set_marker_color(self, color: str) -> None:
        try:
            payload = self._sender.send_marker_color(color)
        except Exception as exc:  # pragma: no cover - surface to UI
            self.error_occurred.emit(str(exc))
            return
        self.status_changed.emit(f"Marker color set to {payload.payload['marker_color']}")

    def set_session_metadata(self, name: str, identifier: str) -> None:
        try:
            payload = self._sender.send_session_metadata(name, identifier)
        except Exception as exc:  # pragma: no cover - surface to UI
            self.error_occurred.emit(str(exc))
            return
        details = payload.payload.get("session_metadata", {})
        session_name = details.get("name", name)
        session_id = details.get("id", identifier)
        self.status_changed.emit(f"Session metadata set to {session_name} ({session_id})")

    def start_group_replay(self, identifier: str, stream: str, display_name: Optional[str] = None) -> None:
        try:
            message = self._ops_sender.start_group_replay(
                identifier,
                stream=stream,
                display_name=display_name,
            )
        except Exception as exc:  # pragma: no cover - surface to UI
            self.error_occurred.emit(str(exc))
            return
        channel = message.channel
        description = channel.display_name
        self.group_replay_changed.emit(channel.channel_id)
        self.status_changed.emit(f"Group replay started on {description}")

    def stop_group_replay(self, channel_id: Optional[str]) -> None:
        try:
            message = self._ops_sender.stop_group_replay(channel_id)
        except Exception as exc:  # pragma: no cover - surface to UI
            self.error_occurred.emit(str(exc))
            return
        self.group_replay_changed.emit("")
        self.status_changed.emit(f"Group replay stopped on {message.channel_id}")


class ClientTableModel(QtCore.QAbstractTableModel):
    headers = [
        "Client",
        "Streaming Channel",
        "Streaming Status",
        "Connected",
        "Last Seen",
        "Operator Login",
        "Ping (ms)",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._rows: List[ClientPresence] = []
        self._lookup: dict[str, int] = {}

    def rowCount(self, parent: QtCore.QModelIndex | None = None) -> int:  # type: ignore[override]
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QtCore.QModelIndex | None = None) -> int:  # type: ignore[override]
        return len(self.headers)

    def data(self, index: QtCore.QModelIndex, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid() or role != QtCore.Qt.DisplayRole:
            return None
        presence = self._rows[index.row()]
        column = index.column()
        if column == 0:
            return presence.client_id
        if column == 1:
            return presence.channel_display
        if column == 2:
            return presence.state_display
        if column == 3:
            return _format_timestamp(presence.connection_ts)
        if column == 4:
            return _format_timestamp(presence.last_seen_ts)
        if column == 5:
            return presence.operator or ""
        if column == 6:
            return f"{presence.ping_ms:.1f}" if presence.ping_ms is not None else ""
        return None

    def headerData(self, section: int, orientation: QtCore.Qt.Orientation, role: int = QtCore.Qt.DisplayRole):  # type: ignore[override]
        if role != QtCore.Qt.DisplayRole:
            return None
        if orientation == QtCore.Qt.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]
        if orientation == QtCore.Qt.Vertical:
            return section + 1
        return None

    def upsert(self, presence: ClientPresence) -> None:
        row = self._lookup.get(presence.client_id)
        if row is None:
            row = len(self._rows)
            self.beginInsertRows(QtCore.QModelIndex(), row, row)
            self._rows.append(presence)
            self._lookup[presence.client_id] = row
            self.endInsertRows()
            return
        self._rows[row] = presence
        top_left = self.index(row, 0)
        bottom_right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(top_left, bottom_right, [QtCore.Qt.DisplayRole])


class StatusPoller(QtCore.QObject):
    message_received = QtCore.pyqtSignal(bytes)
    error = QtCore.pyqtSignal(str)

    def __init__(self, consumer, *, poll_interval_ms: int = 500, batch: int = 32) -> None:
        super().__init__()
        self._consumer = consumer
        self._batch = batch
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        try:
            messages = self._consumer.pull(self._batch)
        except Exception as exc:  # pragma: no cover - diagnostics
            self.error.emit(str(exc))
            return
        for message in messages:
            data = getattr(message, "data", None)
            if data is None:
                continue
            try:
                payload = bytes(data)
            except Exception:
                continue
            self.message_received.emit(payload)


class CommandWindow(QtWidgets.QWidget):
    def __init__(
        self,
        controller: CommandController,
        *,
        status_consumer=None,
        default_replay_stream: str = "TSPI",
    ) -> None:
        super().__init__()
        self._controller = controller
        self._tracker = ClientPresenceTracker()
        self._poller: Optional[StatusPoller] = None
        self._default_replay_stream = default_replay_stream
        self.setWindowTitle("Command Console")
        self._chunks: Dict[str, DataChunk] = {}
        self._current_chunk: Optional[DataChunk] = None
        self._current_start_time: Optional[datetime] = None
        self._current_tag: Optional[DataChunkTag] = None
        self._computed_identifier: Optional[str] = None
        self._computed_display_name: Optional[str] = None

        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QGroupBox("Display Controls", self)
        controls_layout = QtWidgets.QGridLayout(controls)

        controls_layout.addWidget(QtWidgets.QLabel("Display Units", self), 0, 0)
        self._units_combo = QtWidgets.QComboBox(self)
        self._units_combo.addItems(["metric", "imperial"])
        controls_layout.addWidget(self._units_combo, 0, 1)
        units_button = QtWidgets.QPushButton("Send", self)
        controls_layout.addWidget(units_button, 0, 2)

        controls_layout.addWidget(QtWidgets.QLabel("Marker Color", self), 1, 0)
        self._color_combo = QtWidgets.QComboBox(self)
        for name, value in _COLOR_CHOICES:
            self._color_combo.addItem(f"{name} ({value})", value)
        controls_layout.addWidget(self._color_combo, 1, 1)
        color_button = QtWidgets.QPushButton("Send", self)
        controls_layout.addWidget(color_button, 1, 2)

        controls_layout.addWidget(QtWidgets.QLabel("Session Name", self), 2, 0)
        self._session_name_edit = QtWidgets.QLineEdit(self)
        self._session_name_edit.setPlaceholderText("e.g. Falcon Lead")
        controls_layout.addWidget(self._session_name_edit, 2, 1)

        controls_layout.addWidget(QtWidgets.QLabel("Session ID", self), 3, 0)
        self._session_id_edit = QtWidgets.QLineEdit(self)
        self._session_id_edit.setPlaceholderText("e.g. 42")
        controls_layout.addWidget(self._session_id_edit, 3, 1)
        metadata_button = QtWidgets.QPushButton("Broadcast Metadata", self)
        controls_layout.addWidget(metadata_button, 3, 2)

        layout.addWidget(controls)

        replay_group = QtWidgets.QGroupBox("Group Replay", self)
        replay_layout = QtWidgets.QGridLayout(replay_group)

        replay_layout.addWidget(QtWidgets.QLabel("Datastore Recording", self), 0, 0)
        self._chunk_combo = QtWidgets.QComboBox(self)
        self._chunk_combo.addItem("Select recording…", None)
        self._chunk_combo.currentIndexChanged.connect(self._apply_chunk_selection)
        replay_layout.addWidget(self._chunk_combo, 0, 1, 1, 2)

        replay_layout.addWidget(QtWidgets.QLabel("Start (UTC)", self), 1, 0)
        self._start_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self)
        self._start_slider.setMinimum(0)
        self._start_slider.setMaximum(0)
        self._start_slider.setEnabled(False)
        self._start_slider.valueChanged.connect(self._apply_slider_offset)
        replay_layout.addWidget(self._start_slider, 1, 1)
        self._start_label = QtWidgets.QLabel("Select a recording", self)
        replay_layout.addWidget(self._start_label, 1, 2)

        replay_layout.addWidget(QtWidgets.QLabel("Named Tags", self), 2, 0)
        self._replay_tag_combo = QtWidgets.QComboBox(self)
        self._replay_tag_combo.addItem("Select tag…", None)
        self._replay_tag_combo.currentIndexChanged.connect(self._apply_replay_tag)
        replay_layout.addWidget(self._replay_tag_combo, 2, 1, 1, 2)

        replay_layout.addWidget(QtWidgets.QLabel("Replay Identifier", self), 3, 0)
        self._replay_identifier_edit = QtWidgets.QLineEdit(self)
        self._replay_identifier_edit.setReadOnly(True)
        self._replay_identifier_edit.setPlaceholderText("Select start point to generate identifier")
        replay_layout.addWidget(self._replay_identifier_edit, 3, 1, 1, 2)

        replay_layout.addWidget(QtWidgets.QLabel("Telemetry Stream", self), 4, 0)
        self._replay_stream_edit = QtWidgets.QLineEdit(self)
        self._replay_stream_edit.setPlaceholderText("TSPI")
        self._replay_stream_edit.setText(default_replay_stream)
        replay_layout.addWidget(self._replay_stream_edit, 4, 1)

        replay_layout.addWidget(QtWidgets.QLabel("Active Channel", self), 5, 0)
        self._replay_channel_edit = QtWidgets.QLineEdit(self)
        self._replay_channel_edit.setPlaceholderText("replay.20240101T120000Z")
        self._replay_channel_edit.setClearButtonEnabled(True)
        replay_layout.addWidget(self._replay_channel_edit, 5, 1)

        replay_buttons = QtWidgets.QHBoxLayout()
        self._start_replay_button = QtWidgets.QPushButton("Play", self)
        self._stop_replay_button = QtWidgets.QPushButton("Stop Replay", self)
        self._start_replay_button.setEnabled(False)
        replay_buttons.addWidget(self._start_replay_button)
        replay_buttons.addWidget(self._stop_replay_button)
        replay_layout.addLayout(replay_buttons, 6, 0, 1, 3)

        layout.addWidget(replay_group)

        log_group = QtWidgets.QGroupBox("Operations Log", self)
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self._log = QtWidgets.QPlainTextEdit(self)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        log_layout.addWidget(self._log)
        layout.addWidget(log_group)

        table_group = QtWidgets.QGroupBox("Active Clients", self)
        table_layout = QtWidgets.QVBoxLayout(table_group)
        self._client_table = QtWidgets.QTableView(self)
        self._table_model = ClientTableModel()
        self._client_table.setModel(self._table_model)
        self._client_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._client_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._client_table.horizontalHeader().setStretchLastSection(True)
        self._client_table.verticalHeader().setVisible(False)
        table_layout.addWidget(self._client_table)
        layout.addWidget(table_group)

        self._status_label = QtWidgets.QLabel("Ready", self)
        layout.addWidget(self._status_label)

        units_button.clicked.connect(self._send_units)
        color_button.clicked.connect(self._send_color)
        metadata_button.clicked.connect(self._send_session_metadata)
        self._start_replay_button.clicked.connect(self._start_group_replay)
        self._stop_replay_button.clicked.connect(self._stop_group_replay)
        controller.status_changed.connect(self._show_status)
        controller.error_occurred.connect(self._show_error)
        controller.group_replay_changed.connect(self._update_active_replay)

        if status_consumer is not None:
            self._poller = StatusPoller(status_consumer)
            self._poller.message_received.connect(self._handle_status_payload)
            self._poller.error.connect(self._show_error)
            self._poller.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._poller is not None:
            self._poller.stop()
        super().closeEvent(event)

    def add_log_entry(self, message: str, *, timestamp: Optional[datetime] = None) -> None:
        ts = timestamp or datetime.now(tz=UTC)
        self._log.appendPlainText(f"{_format_timestamp(ts)} — {message}")

    def _handle_status_payload(self, payload: bytes) -> None:
        presence, events = self._tracker.process_raw(payload)
        if presence is not None:
            self._table_model.upsert(presence)
        for event in events:
            self._append_event(event)

    def _append_event(self, event: OperatorEvent) -> None:
        self.add_log_entry(event.message, timestamp=event.timestamp)

    def _send_units(self) -> None:
        units = self._units_combo.currentText()
        self._controller.set_units(units)

    def _send_color(self) -> None:
        color = self._color_combo.currentData()
        if isinstance(color, str):
            self._controller.set_marker_color(color)

    def _send_session_metadata(self) -> None:
        name = self._session_name_edit.text().strip()
        identifier = self._session_id_edit.text().strip()
        self._controller.set_session_metadata(name, identifier)

    def _apply_chunk_selection(self, index: int) -> None:
        if index <= 0:
            self._current_chunk = None
            self._current_start_time = None
            self._current_tag = None
            self._computed_identifier = None
            self._computed_display_name = None
            self._start_slider.setEnabled(False)
            self._start_slider.setMaximum(0)
            self._start_slider.setValue(0)
            self._start_label.setText("Select a recording")
            self._replay_identifier_edit.clear()
            self._populate_tag_combo([])
            self._start_replay_button.setEnabled(False)
            return

        chunk_key = self._chunk_combo.itemData(index)
        chunk = self._chunks.get(chunk_key)
        if chunk is None:
            return
        self._current_chunk = chunk
        self._current_tag = None
        self._current_start_time = chunk.start
        self._start_slider.blockSignals(True)
        self._start_slider.setEnabled(True)
        self._start_slider.setMaximum(chunk.duration_seconds)
        self._start_slider.setValue(0)
        self._start_slider.blockSignals(False)
        self._start_label.setText(_format_timestamp(chunk.start))
        self._populate_tag_combo(list(chunk.tags))
        self._update_replay_identifier(chunk.start, None)
        self._start_replay_button.setEnabled(True)

    def _populate_tag_combo(self, tags: Sequence[DataChunkTag | str]) -> None:
        self._replay_tag_combo.blockSignals(True)
        self._replay_tag_combo.clear()
        self._replay_tag_combo.addItem("Select tag…", None)
        for tag in tags:
            if isinstance(tag, DataChunkTag):
                self._replay_tag_combo.addItem(tag.label, tag)
            else:
                text = str(tag).strip()
                if text:
                    self._replay_tag_combo.addItem(text, text)
        self._replay_tag_combo.blockSignals(False)

    def _apply_replay_tag(self, index: int) -> None:
        if self._current_chunk is None:
            return
        if index <= 0:
            return
        tag = self._replay_tag_combo.itemData(index)
        if isinstance(tag, DataChunkTag):
            self._current_tag = tag
            self._current_start_time = tag.timestamp
            self._start_slider.blockSignals(True)
            self._start_slider.setValue(self._current_chunk.offset_for_timestamp(tag.timestamp))
            self._start_slider.blockSignals(False)
            self._start_label.setText(_format_timestamp(tag.timestamp))
            self._update_replay_identifier(tag.timestamp, tag)
            return
        if isinstance(tag, str) and tag.strip():
            timestamp = self._current_chunk.start
            self._current_tag = None
            if self._current_start_time is None:
                self._current_start_time = timestamp
            self._start_slider.blockSignals(True)
            self._start_slider.setValue(0)
            self._start_slider.blockSignals(False)
            self._start_label.setText(_format_timestamp(timestamp))
            identifier = f"{self._current_chunk.identifier} {tag.strip()}".strip()
            display_name = f"{self._current_chunk.label} — {tag.strip()}".strip()
            self._computed_identifier = identifier
            self._computed_display_name = display_name
            self._replay_identifier_edit.setText(identifier)

    def _apply_slider_offset(self, value: int) -> None:
        if self._current_chunk is None:
            return
        timestamp = self._current_chunk.timestamp_at_offset(value)
        self._current_start_time = timestamp
        self._current_tag = None
        self._start_label.setText(_format_timestamp(timestamp))
        self._replay_tag_combo.blockSignals(True)
        self._replay_tag_combo.setCurrentIndex(0)
        self._replay_tag_combo.blockSignals(False)
        self._update_replay_identifier(timestamp, None)

    def _update_replay_identifier(self, timestamp: datetime, tag: Optional[DataChunkTag]) -> None:
        if self._current_chunk is None:
            return
        identifier, display_name = compose_replay_identifier(self._current_chunk, start_time=timestamp, tag=tag)
        self._computed_identifier = identifier
        self._computed_display_name = display_name
        self._replay_identifier_edit.setText(identifier)

    def _start_group_replay(self) -> None:
        stream = self._replay_stream_edit.text().strip() or self._default_replay_stream
        if self._current_chunk is None or self._current_start_time is None or not self._computed_identifier:
            self._show_error("Select a datastore recording and start time")
            return
        self._controller.start_group_replay(
            self._computed_identifier,
            stream,
            self._computed_display_name,
        )

    def set_available_chunks(self, chunks: Sequence[DataChunk]) -> None:
        self._chunks = {chunk.identifier: chunk for chunk in chunks}
        self._chunk_combo.blockSignals(True)
        self._chunk_combo.clear()
        self._chunk_combo.addItem("Select recording…", None)
        for chunk in chunks:
            label = f"{chunk.label} ({_format_timestamp(chunk.start)})"
            self._chunk_combo.addItem(label, chunk.identifier)
        self._chunk_combo.blockSignals(False)
        self._apply_chunk_selection(0)

    def set_available_tags(self, tags: List[str]) -> None:
        cleaned = [tag for tag in tags if isinstance(tag, str) and tag.strip()]
        self._populate_tag_combo(cleaned)

    def _stop_group_replay(self) -> None:
        channel_id = self._replay_channel_edit.text().strip() or None
        self._controller.stop_group_replay(channel_id)

    def _update_active_replay(self, channel_id: str) -> None:
        self._replay_channel_edit.setText(channel_id)

    def _show_status(self, message: str) -> None:
        self._status_label.setText(message)
        self.add_log_entry(message)

    def _show_error(self, message: str) -> None:
        self._status_label.setText(f"Error: {message}")
        self.add_log_entry(f"Error: {message}")


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Command Console")
    parser.add_argument("--headless", action="store_true", help="Run without a GUI")
    parser.add_argument("--sender-id", default="command-ui")
    parser.add_argument("--units", choices=["metric", "imperial"])
    parser.add_argument("--marker-color")
    parser.add_argument("--session-name", help="Human-friendly session name to broadcast as metadata.")
    parser.add_argument("--session-id", help="Identifier associated with the session metadata.")
    parser.add_argument(
        "--nats-server",
        dest="nats_servers",
        action="append",
        help="NATS server URL (may be provided multiple times).",
    )
    parser.add_argument("--js-stream", default="TSPI")
    parser.add_argument("--stream-prefix", default="tspi")
    parser.add_argument("--ops-stream", default="TSPI_OPS", help="JetStream stream storing client heartbeats")
    parser.add_argument("--status-subject", default="tspi.ops.status", help="Subject to monitor for client status heartbeats")
    parser.add_argument(
        "--group-replay-id",
        help=(
            "Launch a group replay using the provided datastore identifier, tag name, or timestamp. "
            "Playback continues until the datachunk completes or the administrator stops it."
        ),
    )
    parser.add_argument(
        "--group-replay-stream",
        help="JetStream stream backing historical telemetry replays (defaults to --js-stream).",
    )
    parser.add_argument(
        "--stop-group-replay",
        nargs="?",
        const="",
        help=(
            "Stop an active group replay. Provide a channel_id or omit the value to stop the last replay started "
            "during this session."
        ),
    )
    return parser.parse_args(argv)


def _create_status_consumer(
    js_client: JetStreamThreadedClient,
    *,
    status_subject: str,
    ops_stream: str,
) -> Optional[JetStreamConsumerAdapter]:
    try:
        js_client.ensure_stream(ops_stream, [status_subject])
        return js_client.create_pull_consumer(status_subject, stream=ops_stream)
    except Exception:  # pragma: no cover - stream may already exist without permissions
        try:
            return js_client.create_pull_consumer(status_subject, stream=ops_stream)
        except Exception:
            return None


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_offscreen(args.headless)
    js_client: JetStreamThreadedClient | None = None
    status_consumer = None

    if args.nats_servers:
        js_client = JetStreamThreadedClient(args.nats_servers)
        js_client.start()
        subjects = [
            f"{args.stream_prefix}.>",
            f"{COMMAND_SUBJECT_PREFIX}.>",
            "tags.>",
            OPS_CONTROL_SUBJECT,
        ]
        js_client.ensure_stream(args.js_stream, subjects)
        status_consumer = _create_status_consumer(
            js_client,
            status_subject=args.status_subject,
            ops_stream=args.ops_stream,
        )
        publisher = js_client.publisher()
    else:
        stream, _ = connect_in_memory(
            {
                "live": [
                    "tspi.>",
                    f"{COMMAND_SUBJECT_PREFIX}.>",
                    OPS_CONTROL_SUBJECT,
                    args.status_subject,
                ],
            }
        )
        publisher = stream
        try:
            status_consumer = stream.create_consumer(args.status_subject)
        except Exception:
            status_consumer = None

    sender = CommandSender(publisher, sender_id=args.sender_id)
    ops_sender = OpsControlSender(publisher, sender_id=args.sender_id)

    if args.headless:
        requested_metadata = args.session_name or args.session_id
        if requested_metadata and not (args.session_name and args.session_id):
            raise SystemExit(
                "Headless session metadata broadcast requires both --session-name and --session-id"
            )
        replay_requested = bool(args.group_replay_id) or args.stop_group_replay is not None
        if (
            not args.units
            and not args.marker_color
            and not requested_metadata
            and not replay_requested
        ):
            raise SystemExit(
                "Headless mode requires a display command, session metadata, or group replay command"
            )
        if args.units:
            sender.send_units(args.units)
        if args.marker_color:
            sender.send_marker_color(args.marker_color)
        if args.session_name and args.session_id:
            sender.send_session_metadata(args.session_name, args.session_id)
        if args.group_replay_id:
            stream_name = args.group_replay_stream or args.js_stream
            ops_sender.start_group_replay(
                args.group_replay_id,
                stream=stream_name,
            )
        if args.stop_group_replay is not None:
            channel_id = args.stop_group_replay or None
            ops_sender.stop_group_replay(channel_id)
        if js_client is not None:
            js_client.close()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    if js_client is not None:
        app.aboutToQuit.connect(js_client.close)
    controller = CommandController(sender, ops_sender)
    window = CommandWindow(
        controller,
        status_consumer=status_consumer,
        default_replay_stream=args.group_replay_stream or args.js_stream,
    )
    if status_consumer is None:
        window.add_log_entry("Status feed unavailable; client table will populate when heartbeats are received.")
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
