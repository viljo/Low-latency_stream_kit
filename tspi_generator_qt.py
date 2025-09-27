"""Entry point for the TSPI flight generator application."""
from __future__ import annotations

import argparse
import sys

from PyQt5 import QtCore, QtWidgets

from tspi_kit.generator import FlightConfig, TSPIFlightGenerator
from tspi_kit.jetstream_client import JetStreamThreadedClient
from tspi_kit.producer import TSPIProducer
from tspi_kit.ui import GeneratorController
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TSPI Generator")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument(
        "--continuous",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Continuously regenerate telemetry when running with the UI.",
    )
    parser.add_argument(
        "--nats-server",
        dest="nats_servers",
        action="append",
        help="NATS server URL to publish to (may be provided multiple times).",
    )
    parser.add_argument(
        "--js-stream",
        default="TSPI",
        help="JetStream stream to create/use for telemetry publishing.",
    )
    parser.add_argument(
        "--stream-prefix",
        default="tspi",
        help="Telemetry subject prefix when publishing to JetStream.",
    )
    parser.add_argument(
        "--stream-replicas",
        type=int,
        default=None,
        help="Number of JetStream replicas when creating the stream.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ensure_offscreen(args.headless)
    js_client: JetStreamThreadedClient | None = None

    if args.nats_servers:
        js_client = JetStreamThreadedClient(args.nats_servers)
        js_client.start()
        js_client.ensure_stream(args.js_stream, [f"{args.stream_prefix}.>"], num_replicas=args.stream_replicas)
        publisher = js_client.publisher()
    else:
        stream, _sources = connect_in_memory({"live": "tspi.>"})
        publisher = stream

    producer = TSPIProducer(publisher, stream_prefix=args.stream_prefix)
    config = FlightConfig(count=args.count, rate_hz=args.rate)
    generator = TSPIFlightGenerator(config)
    controller = GeneratorController(generator, producer)

    if args.headless:
        controller.run(args.duration)
        if js_client is not None:
            js_client.close()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    window = QtWidgets.QWidget()
    window.setWindowTitle("TSPI Flight Generator")
    layout = QtWidgets.QVBoxLayout(window)
    label = QtWidgets.QLabel("Generator Ready", window)
    layout.addWidget(label)
    window.show()
    controller.metrics_updated.connect(lambda payload: label.setText(payload))
    run_once = lambda: controller.run(args.duration)
    if args.continuous:
        interval_ms = max(1, int(max(args.duration, 0.1) * 1000))
        timer = QtCore.QTimer(window)
        timer.setInterval(interval_ms)
        timer.timeout.connect(run_once)
        timer.start()
        window._generator_timer = timer  # type: ignore[attr-defined]
        QtCore.QTimer.singleShot(0, run_once)
    else:
        QtCore.QTimer.singleShot(0, run_once)
    exit_code = app.exec()
    if js_client is not None:
        js_client.close()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
