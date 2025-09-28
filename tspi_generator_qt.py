"""Entry point for the TSPI flight generator application."""
from __future__ import annotations

import argparse
import socket
import sys

from PyQt5 import QtCore, QtWidgets

from tspi_kit.commands import COMMAND_SUBJECT_PREFIX
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
    parser.add_argument(
        "--jetstream",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable JetStream publishing (disabled via --no-jetstream).",
    )
    parser.add_argument(
        "--udp-target",
        dest="udp_targets",
        action="append",
        default=None,
        metavar="HOST:PORT",
        help="Send generated datagrams to the given UDP endpoint (repeatable).",
    )

    args = parser.parse_args(argv)

    udp_targets: list[tuple[str, int]] = []
    for target in args.udp_targets or []:
        try:
            host, port_str = target.rsplit(":", 1)
            if not host or not port_str:
                raise ValueError
            port = int(port_str, 10)
        except ValueError as exc:  # pragma: no cover - argparse error path
            parser.error(f"Invalid UDP target '{target}'. Expected format HOST:PORT.")
            raise SystemExit from exc
        udp_targets.append((host, port))
    args.udp_targets = udp_targets

    if not args.jetstream and not args.udp_targets:
        parser.error("At least one output must be enabled via --jetstream or --udp-target.")

    return args


class _MultiOutputProducer:
    """Forward generated datagrams to JetStream and/or UDP sockets."""

    def __init__(
        self,
        jetstream_producer: TSPIProducer | None,
        udp_targets: list[tuple[str, int]],
    ) -> None:
        self._jetstream_producer = jetstream_producer
        self._udp_targets = list(udp_targets)
        self._udp_socket: socket.socket | None = None
        if self._udp_targets:
            self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def ingest(self, datagram: bytes, *, recv_time: float | None = None):
        if self._udp_socket is not None:
            for target in self._udp_targets:
                self._udp_socket.sendto(datagram, target)
        if self._jetstream_producer is not None:
            return self._jetstream_producer.ingest(datagram, recv_time=recv_time)
        return None

    def close(self) -> None:
        if self._udp_socket is not None:
            self._udp_socket.close()
            self._udp_socket = None


def main(argv: list[str] | None = None) -> int:
    from tspi_kit.jetstream_client import JetStreamThreadedClient

    args = parse_args(argv)
    ensure_offscreen(args.headless)
    js_client: JetStreamThreadedClient | None = None
    publisher = None
    if args.jetstream:
        if args.nats_servers:
            js_client = JetStreamThreadedClient(args.nats_servers)
            js_client.start()
            subjects = [
                f"{args.stream_prefix}.>",
                f"{COMMAND_SUBJECT_PREFIX}.>",
                "tags.>",
            ]
            js_client.ensure_stream(
                args.js_stream, subjects, num_replicas=args.stream_replicas
            )
            publisher = js_client.publisher()
        else:
            stream, _sources = connect_in_memory({"live": "tspi.>"})
            publisher = stream

    jetstream_producer = (
        TSPIProducer(publisher, stream_prefix=args.stream_prefix)
        if publisher is not None
        else None
    )
    producer = _MultiOutputProducer(jetstream_producer, args.udp_targets)
    config = FlightConfig(count=args.count, rate_hz=args.rate)
    generator = TSPIFlightGenerator(config)
    controller = GeneratorController(generator, producer)

    if args.headless:
        controller.run(args.duration)
        producer.close()
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
    producer.close()
    if js_client is not None:
        js_client.close()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
