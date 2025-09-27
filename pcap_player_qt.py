"""Entry point for the PCAP Player."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt5 import QtWidgets

from tspi_kit.pcap import PCAPReplayer
from tspi_kit.producer import TSPIProducer
from tspi_kit.ui import PCAPPlayerController
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PCAP Player")
    parser.add_argument("--pcap", type=Path, required=True)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--metrics-interval", type=float, default=1.0)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--stdout-json",
        dest="stdout_json",
        action="store_true",
        default=True,
        help="Enable JSON metrics on stdout (default)",
    )
    group.add_argument(
        "--no-stdout-json",
        dest="stdout_json",
        action="store_false",
        help="Disable JSON metrics on stdout",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_offscreen(args.headless)
    stream, receiver = connect_in_memory()
    producer = TSPIProducer(stream)
    replayer = PCAPReplayer(args.pcap)
    controller = PCAPPlayerController(replayer, producer, rate=args.rate, loop=args.loop)

    if args.headless:
        controller.replay()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    window = QtWidgets.QWidget()
    window.setWindowTitle("PCAP Player")
    layout = QtWidgets.QVBoxLayout(window)
    label = QtWidgets.QLabel("PCAP Player Ready", window)
    layout.addWidget(label)
    window.show()
    controller.metrics_updated.connect(lambda payload: label.setText(payload))
    controller.replay()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
