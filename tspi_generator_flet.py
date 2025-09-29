"""Flet-based launcher for the TSPI flight generator."""

from __future__ import annotations

import argparse
import json
import socket
import threading
from typing import Any

from tspi_kit.commands import COMMAND_SUBJECT_PREFIX
from tspi_kit.generator import FlightConfig, TSPIFlightGenerator
from tspi_kit.producer import TSPIProducer
from tspi_kit.ui import GeneratorController
from tspi_kit.ui.flet_app import _ensure_flet, pick_flet_web_port
from tspi_kit.ui.player import connect_in_memory


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TSPI Generator (Flet)")
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
        "--style",
        choices=("normal", "airshow"),
        default="normal",
        help="Flight formation style to generate.",
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


class FlightGeneratorApp:
    """Minimal Flet UI that surfaces generator metrics."""

    def __init__(
        self,
        page: Any,
        controller: GeneratorController,
        *,
        duration: float,
        continuous: bool,
    ) -> None:
        self._ft = _ensure_flet()
        self.page = page
        self.controller = controller
        self.duration = max(duration, 0.1)
        self.continuous = continuous
        self._stop = threading.Event()
        self._running = False
        self._build_controls()
        self._connect_signals()
        self.start_generation()

    def _build_controls(self) -> None:
        ft = self._ft
        self.page.title = "TSPI Flight Generator"
        self.metrics_text = ft.Text("Awaiting metrics…")
        self.status_text = ft.Text("Starting generator…")
        self.log_view = ft.ListView(expand=1, spacing=4, auto_scroll=True)
        self.start_button = ft.ElevatedButton("Start", on_click=self._on_start, disabled=True)
        self.stop_button = ft.OutlinedButton("Stop", on_click=self._on_stop, disabled=not self.continuous)

        layout = ft.Column(
            [
                ft.Text("Generator Status", style=ft.TextThemeStyle.TITLE_MEDIUM),
                self.metrics_text,
                self.status_text,
                ft.Row([self.start_button, self.stop_button]),
                ft.Text("Metrics Log", style=ft.TextThemeStyle.TITLE_MEDIUM),
                ft.Container(self.log_view, expand=True),
            ],
            expand=True,
            spacing=12,
        )
        self.page.add(layout)

    def _connect_signals(self) -> None:
        self.controller.metrics_updated.connect(
            lambda payload: self.page.run_task(self._update_metrics_async, payload)
        )

    def _apply_metrics(self, payload: str) -> None:
        try:
            metrics = json.loads(payload)
        except json.JSONDecodeError:
            text = payload
        else:
            frames = metrics.get("frames_generated", 0)
            aircraft = metrics.get("aircraft", 0)
            rate = metrics.get("rate", 0.0)
            text = f"Frames: {frames} | Aircraft: {aircraft} | Rate: {rate:.2f} Hz"
        self.metrics_text.value = text
        self.log_view.controls.append(self._ft.Text(text))
        if len(self.log_view.controls) > 200:
            del self.log_view.controls[0 : len(self.log_view.controls) - 200]

    async def _update_metrics_async(self, payload: str) -> None:
        self._apply_metrics(payload)
        await self.page.update_async()

    # ------------------------------------------------------------------ Control handlers

    def start_generation(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self.status_text.value = "Generator running"
        self.start_button.disabled = True
        self.stop_button.disabled = False
        self.page.update()
        threading.Thread(target=self._run_loop, daemon=True).start()

    def stop_generation(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        self.stop_generation()

    def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self.controller.run(self.duration)
                if not self.continuous:
                    break
        finally:
            self._running = False
            self.page.run_task(self._on_finished_async)

    def _set_idle_state(self) -> None:
        self.status_text.value = "Generator idle"
        self.start_button.disabled = False
        self.stop_button.disabled = True

    async def _on_finished_async(self) -> None:
        self._set_idle_state()
        await self.page.update_async()

    async def _on_start(self, _event) -> None:
        self.start_generation()

    async def _on_stop(self, _event) -> None:
        self.stop_generation()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    js_client = None
    publisher = None
    if args.jetstream:
        if args.nats_servers:
            from tspi_kit.jetstream_client import JetStreamThreadedClient

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
    config = FlightConfig(count=args.count, rate_hz=args.rate, style=args.style)
    generator = TSPIFlightGenerator(config)
    controller = GeneratorController(generator, producer)

    if args.headless:
        controller.metrics_updated.connect(lambda payload: print(payload, flush=True))
        controller.run(args.duration)
        producer.close()
        if js_client is not None:
            js_client.close()
        return 0

    ft = _ensure_flet()
    app_holder: dict[str, FlightGeneratorApp] = {}

    def _launch(page) -> None:
        app = FlightGeneratorApp(
            page,
            controller,
            duration=args.duration,
            continuous=args.continuous,
        )
        app_holder["app"] = app

        def _cleanup(_event: Any | None = None) -> None:
            app.shutdown()
            producer.close()
            if js_client is not None:
                js_client.close()

        page.on_close = _cleanup
        page.on_disconnect = _cleanup

    ft.app(target=_launch, port=pick_flet_web_port())
    app = app_holder.get("app")
    if app is not None:
        app.shutdown()
    producer.close()
    if js_client is not None:
        js_client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

