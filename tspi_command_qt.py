from __future__ import annotations

import argparse
import sys
from typing import List, Tuple

from PyQt5 import QtCore, QtWidgets

from tspi_kit.commands import COMMAND_SUBJECT_PREFIX, CommandSender
from tspi_kit.jetstream_client import JetStreamThreadedClient
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


_COLOR_CHOICES: List[Tuple[str, str]] = [
    ("Green", "#00ff00"),
    ("Red", "#ff0000"),
    ("Blue", "#0000ff"),
    ("Magenta", "#ff00ff"),
    ("Yellow", "#ffff00"),
]


class CommandController(QtCore.QObject):
    status_changed = QtCore.pyqtSignal(str)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, sender: CommandSender) -> None:
        super().__init__()
        self._sender = sender

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


class CommandWindow(QtWidgets.QWidget):
    def __init__(self, controller: CommandController) -> None:
        super().__init__()
        self._controller = controller
        self.setWindowTitle("TSPI Command Console")
        layout = QtWidgets.QVBoxLayout(self)

        units_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(units_layout)
        units_layout.addWidget(QtWidgets.QLabel("Display Units", self))
        self._units_combo = QtWidgets.QComboBox(self)
        self._units_combo.addItems(["metric", "imperial"])
        units_layout.addWidget(self._units_combo)
        units_button = QtWidgets.QPushButton("Send", self)
        units_layout.addWidget(units_button)

        colors_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(colors_layout)
        colors_layout.addWidget(QtWidgets.QLabel("Marker Color", self))
        self._color_combo = QtWidgets.QComboBox(self)
        for name, value in _COLOR_CHOICES:
            self._color_combo.addItem(f"{name} ({value})", value)
        colors_layout.addWidget(self._color_combo)
        color_button = QtWidgets.QPushButton("Send", self)
        colors_layout.addWidget(color_button)

        self._status_label = QtWidgets.QLabel("Ready", self)
        layout.addWidget(self._status_label)

        units_button.clicked.connect(self._send_units)
        color_button.clicked.connect(self._send_color)
        controller.status_changed.connect(self._show_status)
        controller.error_occurred.connect(self._show_error)

    def _send_units(self) -> None:
        units = self._units_combo.currentText()
        self._controller.set_units(units)

    def _send_color(self) -> None:
        color = self._color_combo.currentData()
        if isinstance(color, str):
            self._controller.set_marker_color(color)

    def _show_status(self, message: str) -> None:
        self._status_label.setText(message)

    def _show_error(self, message: str) -> None:
        self._status_label.setText(f"Error: {message}")


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TSPI Command Console")
    parser.add_argument("--headless", action="store_true", help="Run without a GUI")
    parser.add_argument("--sender-id", default="command-ui")
    parser.add_argument("--units", choices=["metric", "imperial"])
    parser.add_argument("--marker-color")
    parser.add_argument(
        "--nats-server",
        dest="nats_servers",
        action="append",
        help="NATS server URL (may be provided multiple times).",
    )
    parser.add_argument("--js-stream", default="TSPI")
    parser.add_argument("--stream-prefix", default="tspi")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_offscreen(args.headless)
    js_client: JetStreamThreadedClient | None = None

    if args.nats_servers:
        js_client = JetStreamThreadedClient(args.nats_servers)
        js_client.start()
        subjects = [
            f"{args.stream_prefix}.>",
            f"{COMMAND_SUBJECT_PREFIX}.>",
            "tags.>",
        ]
        js_client.ensure_stream(args.js_stream, subjects)
        publisher = js_client.publisher()
    else:
        stream, _ = connect_in_memory({"live": "tspi.>"})
        publisher = stream

    sender = CommandSender(publisher, sender_id=args.sender_id)

    if args.headless:
        if not args.units and not args.marker_color:
            raise SystemExit("Headless mode requires --units and/or --marker-color")
        if args.units:
            sender.send_units(args.units)
        if args.marker_color:
            sender.send_marker_color(args.marker_color)
        if js_client is not None:
            js_client.close()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    if js_client is not None:
        app.aboutToQuit.connect(js_client.close)
    controller = CommandController(sender)
    window = CommandWindow(controller)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
