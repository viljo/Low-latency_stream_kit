"""Entry point for the TSPI flight generator application."""
from __future__ import annotations

import argparse
import sys

from PyQt5 import QtWidgets

from tspi_kit.generator import FlightConfig, TSPIFlightGenerator
from tspi_kit.producer import TSPIProducer
from tspi_kit.ui import GeneratorController
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TSPI Generator")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--duration", type=float, default=1.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_offscreen(args.headless)
    stream, _sources = connect_in_memory({"live": "tspi.>"})
    producer = TSPIProducer(stream)
    config = FlightConfig(count=args.count, rate_hz=args.rate)
    generator = TSPIFlightGenerator(config)
    controller = GeneratorController(generator, producer)

    if args.headless:
        controller.run(args.duration)
        return 0

    app = QtWidgets.QApplication(sys.argv)
    window = QtWidgets.QWidget()
    window.setWindowTitle("TSPI Flight Generator")
    layout = QtWidgets.QVBoxLayout(window)
    label = QtWidgets.QLabel("Generator Ready", window)
    layout.addWidget(label)
    window.show()
    controller.metrics_updated.connect(lambda payload: label.setText(payload))
    controller.run(args.duration)
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
