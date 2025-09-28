"""Entry point for the JetStream Player Qt application."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt5 import QtWidgets

from tspi_kit.jetstream_client import JetStreamThreadedClient
from tspi_kit.receiver import CompositeTSPIReceiver, TSPIReceiver
from tspi_kit.ui import HeadlessPlayerRunner, JetStreamPlayerWindow, UiConfig
from tspi_kit.ui.player import connect_in_memory, ensure_offscreen


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JetStream Player")
    defaults = UiConfig()
    parser.add_argument("--headless", action="store_true", help="Run without a GUI")
    parser.add_argument("--metrics-interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--exit-on-idle", type=float)
    parser.add_argument(
        "--json-stream",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Emit player metrics as JSON lines when running headless.",
    )
    parser.add_argument("--write-cbor", type=Path)
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--room", type=str, default="default")
    parser.add_argument("--clock", choices=["receive", "tspi"], default="receive")
    parser.add_argument(
        "--source",
        choices=["livestream", "replay.default", "live", "historical"],
        default="livestream",
    )
    parser.add_argument(
        "--smooth-center",
        type=float,
        default=defaults.smooth_center,
        help="Smoothing factor applied to the map center (0.0-1.0).",
    )
    parser.add_argument(
        "--smooth-zoom",
        type=float,
        default=defaults.smooth_zoom,
        help="Smoothing factor applied to the map zoom level (0.0-1.0).",
    )
    parser.add_argument(
        "--window-sec",
        type=int,
        default=defaults.window_sec,
        help="Duration of telemetry retained for map previews (seconds).",
    )
    parser.add_argument(
        "--nats-server",
        dest="nats_servers",
        action="append",
        help="NATS server URL (may be provided multiple times).",
    )
    parser.add_argument(
        "--js-stream",
        default="TSPI",
        help="Name of the JetStream stream that stores live telemetry.",
    )
    parser.add_argument(
        "--historical-stream",
        default=None,
        help="JetStream stream providing historical playback (defaults to auto-discovery).",
    )
    parser.add_argument(
        "--durable-prefix",
        default="player-cli",
        help="Prefix for JetStream durable consumer names.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = UiConfig(
        smooth_center=args.smooth_center,
        smooth_zoom=args.smooth_zoom,
        window_sec=args.window_sec,
        metrics_interval=args.metrics_interval,
        default_rate=args.rate,
        default_clock=args.clock,
    )
    ensure_offscreen(args.headless)
    subject_map = {
        "livestream": ["tspi.>", "tspi.cmd.display.>", "tags.broadcast"],
        "replay.default": [f"player.{args.room}.playout.>", "tags.broadcast"],
    }
    js_client: JetStreamThreadedClient | None = None
    cleanup_required = False

    if args.nats_servers:
        js_client = JetStreamThreadedClient(args.nats_servers)
        js_client.start()
        receivers = {}
        for name, subjects in subject_map.items():
            durable_prefix = f"{args.durable_prefix}-{name}"
            stream_name = args.js_stream if name == "livestream" else args.historical_stream
            receiver_list = []
            for index, subject in enumerate(subjects):
                durable = f"{durable_prefix}-{index}"
                consumer = js_client.create_pull_consumer(subject, durable=durable, stream=stream_name)
                receiver_list.append(TSPIReceiver(consumer))
            if len(receiver_list) == 1:
                receiver: TSPIReceiver = receiver_list[0]
            else:
                receiver = CompositeTSPIReceiver(receiver_list)
            receivers[name] = receiver
        sources = receivers
        cleanup_required = True
    else:
        stream, sources = connect_in_memory(subject_map)

    def _close_client() -> None:
        if js_client is not None:
            js_client.close()

    if args.headless:
        runner = HeadlessPlayerRunner(
            sources,
            ui_config=config,
            stdout_json=args.json_stream,
            duration=args.duration,
            exit_on_idle=args.exit_on_idle,
            write_cbor_dir=args.write_cbor,
            initial_source=args.source,
        )
        runner.run()
        if cleanup_required:
            _close_client()
        return 0

    app = QtWidgets.QApplication(sys.argv)
    if cleanup_required:
        app.aboutToQuit.connect(_close_client)
    window = JetStreamPlayerWindow(sources, ui_config=config, initial_source=args.source)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
